import json
import re

CLOSES_RE = re.compile(r"\b(?:closes?|fix(?:e[sd])?|resolves?)\s+#(\d+)", re.IGNORECASE)


def priority_label_variants(level: int) -> list[str]:
    """The repo carries both `priority: N` and `priority:N` spellings; query both."""
    return [f"priority: {level}", f"priority:{level}"]


def issue_search_query(owner_repo: str, label: str) -> str:
    return f'repo:{owner_repo} is:issue is:open label:"{label}" sort:created-asc'


def parse_closes_issues(text: str | None) -> list[int]:
    if not text:
        return []
    seen: set[int] = set()
    out: list[int] = []
    for m in CLOSES_RE.findall(text):
        n = int(m)
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def extract_json_object(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[A-Za-z0-9]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("no JSON object found in model output")
    return json.loads(cleaned[start : end + 1])


def validate_changes(
    changes: list[dict],
    tree_paths: list[str],
    allowed_new_dirs: tuple[str, ...],
    max_bytes: int,
) -> list[str]:
    errors: list[str] = []
    tree = set(tree_paths)
    total = 0
    for ch in changes:
        path = (ch.get("path") or "").strip()
        content = ch.get("content")
        if not path or path.startswith("/") or ".." in path.split("/") or "\\" in path:
            errors.append(f"unsafe path: {path!r}")
            continue
        if path in tree:
            pass
        elif any(path.startswith(d) for d in allowed_new_dirs):
            pass
        else:
            errors.append(f"unknown path (not in repo tree, not an allowed new file): {path}")
            continue
        if ch.get("delete"):
            continue
        if not isinstance(content, str):
            errors.append(f"missing content for {path}")
            continue
        total += len(content.encode("utf-8"))
    if total > max_bytes:
        errors.append(f"changes too large: {total} bytes > {max_bytes}")
    return errors


def filter_running_run_ids(runs, exclude_ids: set[str]) -> list[str]:
    out: list[str] = []
    for r in runs or []:
        rid = None
        if isinstance(r, dict):
            rid = r.get("execution_id") or r.get("run_id") or r.get("id")
        else:
            for attr in ("execution_id", "run_id", "id"):
                v = getattr(r, attr, None)
                if isinstance(v, str) and v:
                    rid = v
                    break
        if rid and rid not in exclude_ids:
            out.append(rid)
    return out
