import asyncio
import datetime
import os
import re

import mistralai.workflows as workflows
from mistralai.client import Mistral

from . import config as cfg
from . import logic
from .github_api import GitHub


def gh() -> GitHub:
    return GitHub(cfg.OWNER, cfg.REPO, cfg.GITHUB_TOKEN)


def mistral() -> Mistral:
    return Mistral(api_key=cfg.MISTRAL_API_KEY)


def workdir_for(branch: str) -> str:
    return os.path.join(cfg.WORK_ROOT, branch.replace("/", "__"))


async def run_cmd(cmd: list[str], cwd: str, log: list[str]) -> int:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ASKPASS"] = "/tmp/solver-git-askpass.sh"
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=cwd, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    out: list[bytes] = []

    async def drain() -> None:
        while True:
            chunk = await proc.stdout.read(65536)
            if not chunk:
                break
            out.append(chunk)

    read_task = asyncio.create_task(drain())
    while not read_task.done():
        workflows.activity_heartbeat()
        await asyncio.wait({read_task}, timeout=30)
    rc = await proc.wait()
    out.append(await proc.stdout.read() or b"")
    log.append(b"".join(out).decode("utf-8", "replace"))
    return rc


def write_askpass() -> None:
    with open("/tmp/solver-git-askpass.sh", "w") as f:
        f.write('#!/bin/sh\necho "$SECOND_THOUGHT_GH_TOKEN"\n')
    os.chmod("/tmp/solver-git-askpass.sh", 0o700)


@workflows.activity(start_to_close_timeout=datetime.timedelta(minutes=2))
async def guard_running_executions(exclude_ids: list[str]) -> list[str]:
    client = mistral()
    kwargs = {
        "workflow_identifier": cfg.WORKFLOW_NAME,
        "status": ["RUNNING"],
        "page_size": 100,
        "include_internal": False,
    }
    if os.environ.get("DEPLOYMENT_NAME"):
        kwargs["deployment_name"] = cfg.DEPLOYMENT_NAME
    resp = client.workflows.runs.list_runs(**kwargs)
    runs = getattr(resp, "runs", None) or getattr(resp, "items", None) or []
    return logic.filter_running_run_ids(runs, set(exclude_ids))


@workflows.activity(start_to_close_timeout=datetime.timedelta(minutes=2))
async def find_next_issue() -> dict | None:
    g = gh()
    owner_repo = f"{cfg.OWNER}/{cfg.REPO}"
    for level in cfg.PRIORITY_LEVELS:
        for label in logic.priority_label_variants(level):
            items = await g.search_issues(logic.issue_search_query(owner_repo, label))
            if items:
                it = items[0]
                return {
                    "number": it["number"],
                    "title": it.get("title", ""),
                    "level": level,
                    "priority_label": label,
                    "labels": [l.get("name", "") for l in it.get("labels", [])],
                }
    return None


@workflows.activity(start_to_close_timeout=datetime.timedelta(minutes=2))
async def mark_issue_in_progress(number: int) -> dict:
    g = gh()
    await g.ensure_label(cfg.IN_PROGRESS_LABEL, "An autofix run is working on this issue", "fbca04")
    await g.add_labels(number, [cfg.IN_PROGRESS_LABEL])
    issue = await g.get_issue(number)
    comments = await g.list_comments(number)
    return {
        "number": number,
        "title": issue.get("title", ""),
        "body": issue.get("body") or "",
        "labels": [l.get("name", "") for l in issue.get("labels", [])],
        "comments": [c.get("body", "") for c in comments],
    }


@workflows.activity(start_to_close_timeout=datetime.timedelta(minutes=3))
async def gather_context(details: dict) -> dict:
    g = gh()
    tree = await g.get_tree()
    keywords = [w for w in re.findall(r"[a-zA-Z][a-zA-Z0-9_-]{4,}", details["title"])][:4]
    snippets: list[dict] = []
    for kw in keywords:
        try:
            snippets += await g.code_search(f"{kw} repo:{cfg.OWNER}/{cfg.REPO}")
        except RuntimeError:
            pass
        if len(snippets) >= 15:
            break
    docs: dict[str, str] = {}
    for p in ("README.md", "CONTRIBUTING.md"):
        try:
            docs[p] = (await g.get_file_contents(p))[:6000]
        except RuntimeError:
            docs[p] = ""
    return {"details": details, "tree": tree, "snippets": snippets[:15], "docs": docs}


PLAN_PROMPT = """You are fixing a GitHub issue in the repository {repo} (Second Thought: a single-page web app.
Runtime: python/second_thought/ (joined into python/runtime.py and second-thought.html by tools/sync.py).
Tools: tools/. Tests: tests/unit, tests/editor, tests/e2e via tests/run_tests.py).

ISSUE #{number}: {title}

{body}

COMMENTS:
{comments}

REPO TREE (existing paths):
{tree}

CODE SEARCH HITS for issue keywords:
{snippets}

KEY DOCS:
{docs}

RULES (from CONTRIBUTING.md):
- If you change anything under python/second_thought/, i18n/, gallery/, prompts/, samples/, web/ or
  second-thought.example.ini, the worker runs `python tools/sync.py` afterwards, so never edit
  python/runtime.py, second-thought.html or lang/ by hand.
- Never commit second-thought.ini or API keys. Never call real model services.
- Keep the change minimal and focused on the issue.

{attempt_note}

Output ONLY a JSON object with this shape:
{{
  "diagnosis": "root cause, 2-4 sentences",
  "summary": "one-paragraph summary for the PR body and the closing comment",
  "changes": [{{"path": "repo-relative path of an existing file (or a new file under gallery/)", "content": "full new content of the file"}}]
}}
"""


@workflows.activity(start_to_close_timeout=datetime.timedelta(minutes=10), heartbeat_timeout=datetime.timedelta(minutes=1))
async def plan_fix(context: dict, attempt: int, previous_log_tail: str) -> dict:
    details = context["details"]
    attempt_note = ""
    if attempt > 1:
        attempt_note = (
            f"ATTEMPT {attempt} of {cfg.MAX_FIX_ATTEMPTS}: the previous attempt failed the test suite. "
            "Fix the reported failures.\nFAILURE LOG TAIL:\n" + previous_log_tail[-4000:]
        )
    prompt = PLAN_PROMPT.format(
        repo=f"{cfg.OWNER}/{cfg.REPO}",
        number=details["number"],
        title=details["title"],
        body=details["body"][:4000],
        comments="\n---\n".join(details["comments"])[:3000] or "(none)",
        tree="\n".join(context["tree"])[:12000],
        snippets=str(context["snippets"])[:3000],
        docs="\n\n".join(f"### {k}\n{v}" for k, v in context["docs"].items())[:9000],
        attempt_note=attempt_note,
    )
    resp = mistral().chat.complete(
        model=cfg.MODEL,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0.2,
    )
    data = logic.extract_json_object(resp.choices[0].message.content)
    changes = data.get("changes") or []
    if not isinstance(changes, list) or not changes:
        raise ValueError("model returned no changes")
    errors = logic.validate_changes(changes, context["tree"], cfg.NEW_FILE_ALLOWED_DIRS, cfg.MAX_CHANGES_BYTES)
    if errors:
        raise ValueError("invalid changes: " + "; ".join(errors))
    return {
        "diagnosis": str(data.get("diagnosis", ""))[:2000],
        "summary": str(data.get("summary", ""))[:4000],
        "changes": changes,
    }


@workflows.activity(start_to_close_timeout=datetime.timedelta(minutes=15), heartbeat_timeout=datetime.timedelta(minutes=2))
async def prepare_fix_branch(number: int, changes: list[dict], attempt: int) -> str:
    branch = f"{cfg.BRANCH_PREFIX}{number}"
    wd = workdir_for(branch)
    log: list[str] = []
    os.makedirs(cfg.WORK_ROOT, exist_ok=True)
    write_askpass()
    url = f"https://github.com/{cfg.OWNER}/{cfg.REPO}.git"
    if not os.path.isdir(os.path.join(wd, ".git")):
        rc = await run_cmd(["git", "clone", "--depth", "1", "--branch", "main", url, wd], "/tmp", log)
        if rc != 0:
            raise RuntimeError("git clone failed: " + log[-1][-2000:])
    else:
        await run_cmd(["git", "-C", wd, "fetch", "--depth", "1", "origin", "main"], wd, log)
        await run_cmd(["git", "-C", wd, "checkout", "-B", "main", "origin/main"], wd, log)
        await run_cmd(["git", "-C", wd, "clean", "-fdx"], wd, log)
    await run_cmd(["git", "-C", wd, "checkout", "-B", branch], wd, log)
    paths = []
    for ch in changes:
        path = os.path.join(wd, ch["path"])
        paths.append(ch["path"])
        if ch.get("delete"):
            if os.path.exists(path):
                os.remove(path)
        else:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(ch["content"])
    if any(p.startswith(cfg.SYNC_TRIGGER_PATHS) for p in paths):
        await run_cmd(["python3", "tools/sync.py"], wd, log)
    await run_cmd(["git", "-C", wd, "add", "-A"], wd, log)
    msg = (
        f"Fix #{number} (autofix attempt {attempt})\n\n"
        "Co-authored-by: Vibe Nuage Agent <vibe@mistral.ai>\n"
        "Co-authored-by: itsfja <itsfja@users.noreply.github.com>"
    )
    rc = await run_cmd(["git", "-C", wd, "commit", "-m", msg], wd, log)
    if rc != 0:
        raise RuntimeError("git commit failed: " + log[-1][-2000:])
    rc = await run_cmd(["git", "-C", wd, "push", "--force-with-lease", "origin", branch], wd, log)
    if rc != 0:
        raise RuntimeError("git push failed: " + log[-1][-2000:])
    return branch


@workflows.activity(start_to_close_timeout=datetime.timedelta(minutes=75), heartbeat_timeout=datetime.timedelta(minutes=2))
async def run_test_suite(branch: str) -> dict:
    wd = workdir_for(branch)
    log: list[str] = []
    steps = [
        "python3 tools/sync.py --check",
        "python3 -m unittest discover -s tests/unit",
        "node --test tests/editor/*.test.cjs",
        "bash tests/setup.sh",
        "python3 tests/run_tests.py --parallel",
    ]
    passed = True
    for s in steps:
        rc = await run_cmd(["bash", "-c", s], wd, log)
        if rc != 0:
            passed = False
            break
    tail = "\n".join(log)[-8000:]
    if passed and "All checks passed" not in tail:
        passed = False
    return {"passed": passed, "log_tail": tail}


@workflows.activity(start_to_close_timeout=datetime.timedelta(minutes=2))
async def create_pr(number: int, branch: str, summary: str) -> dict:
    g = gh()
    issue = await g.get_issue(number)
    title = f"Fix #{number}: {issue.get('title', '')[:80]}"
    body = f"Closes #{number}\n\n{summary}\n\n---\nAutomated by the `mistral-issues-solver` workflow. Please review."
    pr = await g.create_pr(title=title, head=branch, base="main", body=body, reviewers=[cfg.REVIEWER])
    return {"number": pr["number"], "url": pr.get("html_url", "")}


@workflows.activity(start_to_close_timeout=datetime.timedelta(minutes=2))
async def get_pr_state(number: int) -> dict:
    pr = await gh().get_pr(number)
    return {"closed": pr.get("state") == "closed", "merged": bool(pr.get("merged"))}


@workflows.activity(start_to_close_timeout=datetime.timedelta(minutes=2))
async def comment_issue(number: int, body: str) -> None:
    await gh().create_comment(number, body)


@workflows.activity(start_to_close_timeout=datetime.timedelta(minutes=2))
async def unmark_issue(number: int) -> None:
    await gh().remove_label(number, cfg.IN_PROGRESS_LABEL)


@workflows.activity(start_to_close_timeout=datetime.timedelta(minutes=3))
async def fail_issue(number: int, summary: str, log_tail: str) -> None:
    g = gh()
    body = (
        "`mistral-issues-solver` could not fix this issue automatically: the test suite still failed after "
        f"{cfg.MAX_FIX_ATTEMPTS} attempts.\n\nLast plan: {summary}\n\nLog tail:\n```\n{log_tail[-3000:]}\n```"
    )
    await g.create_comment(number, body)
    await g.remove_label(number, cfg.IN_PROGRESS_LABEL)


@workflows.activity(start_to_close_timeout=datetime.timedelta(minutes=3))
async def close_issue_with_summary(number: int, pr_number: int, summary: str) -> None:
    g = gh()
    body = f"Fixed by PR #{pr_number} (merged).\n\n{summary}\n\n---\nClosed automatically by `mistral-issues-solver`."
    await g.create_comment(number, body)
    await g.close_issue(number)
    await g.remove_label(number, cfg.IN_PROGRESS_LABEL)


@workflows.activity(start_to_close_timeout=datetime.timedelta(minutes=3))
async def handle_merged_pr(pr_number: int) -> list[int]:
    g = gh()
    pr = await g.get_pr(pr_number)
    closed: list[int] = []
    if not pr.get("merged"):
        return closed
    for n in logic.parse_closes_issues(pr.get("body")):
        issue = await g.get_issue(n)
        if issue.get("state") != "open":
            continue
        await g.create_comment(n, f"Fixed by PR #{pr_number} (merged). Closing.\n\n---\nAutomated by `mistral-issues-solver`.")
        await g.close_issue(n)
        await g.remove_label(n, cfg.IN_PROGRESS_LABEL)
        closed.append(n)
    return closed


@workflows.activity(start_to_close_timeout=datetime.timedelta(minutes=2))
async def start_next_execution(exclude_execution_id: str | None) -> str:
    client = mistral()
    inp: dict = {"event": "manual"}
    if exclude_execution_id:
        inp["exclude_execution_id"] = exclude_execution_id
    kwargs: dict = {"workflow_identifier": cfg.WORKFLOW_NAME, "input": inp}
    if os.environ.get("DEPLOYMENT_NAME"):
        kwargs["deployment_name"] = cfg.DEPLOYMENT_NAME
    resp = client.workflows.execute_workflow(**kwargs)
    return getattr(resp, "execution_id", None) or "started"
