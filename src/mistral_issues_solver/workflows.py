import datetime
from typing import Literal, Optional

from pydantic import BaseModel

import mistralai.workflows as workflows
from mistralai.workflows.core.workflow import workflow as temporal

from . import activities as act
from . import config as cfg


class SolverInput(BaseModel):
    event: Literal["issue_labeled", "pr_merged", "manual"] = "manual"
    issue_number: Optional[int] = None
    pr_number: Optional[int] = None
    label: Optional[str] = None
    branch: Optional[str] = None
    exclude_execution_id: Optional[str] = None


@workflows.workflow.define(
    name=cfg.WORKFLOW_NAME,
    workflow_display_name="Mistral issues solver",
    workflow_description=(
        "Picks the oldest open issue with the highest priority label (priority: 1 first, up to priority: 8) in the "
        "target repo, labels it autofix-in-progress, implements and tests a fix (up to 3 attempts), opens a PR for "
        "review, waits for the merge, then closes the issue with a summary and starts the next execution."
    ),
    execution_timeout=datetime.timedelta(days=14),
)
class IssuesSolverWorkflow:
    @workflows.workflow.entrypoint
    async def run(self, input: SolverInput) -> str:
        self_id = workflows.get_execution_id()
        exclude = [self_id] + ([input.exclude_execution_id] if input.exclude_execution_id else [])

        # (1) guard: exit if another execution is already in progress
        running = await act.guard_running_executions(exclude)
        if running:
            return f"exit: another execution is already in progress ({', '.join(running)})"

        # (8, from the merge trigger) close the issue this merged PR referenced
        if input.event == "pr_merged" and input.pr_number:
            await act.handle_merged_pr(input.pr_number)

        # (2) oldest open issue with the highest priority label; exit if none
        issue = await act.find_next_issue()
        if issue is None:
            return "exit: no open issue with a priority label"
        number = issue["number"]

        # (3) label autofix-in-progress and fetch the issue details
        details = await act.mark_issue_in_progress(number)
        context = await act.gather_context(details)

        # (4) understand, (5) fix, (6) test with up to MAX_FIX_ATTEMPTS retries
        passed = False
        branch = ""
        summary = ""
        log_tail = ""
        for attempt in range(1, cfg.MAX_FIX_ATTEMPTS + 1):
            plan = await act.plan_fix(context, attempt, log_tail)
            summary = plan["summary"]
            branch = await act.prepare_fix_branch(number, plan["changes"], attempt)
            result = await act.run_test_suite(branch)
            passed = result["passed"]
            log_tail = result["log_tail"]
            if passed:
                break

        if not passed:
            await act.fail_issue(number, summary, log_tail)
            return (
                f"exit: issue #{number}: test suite still failing after {cfg.MAX_FIX_ATTEMPTS} attempts; "
                "explanatory comment posted and autofix-in-progress removed"
            )

        # (7) open a PR requesting review and wait for the merge
        pr = await act.create_pr(number, branch, summary)
        pr_number = pr["number"]
        while True:
            state = await act.get_pr_state(pr_number)
            if state["closed"]:
                break
            await temporal.sleep(cfg.POLL_INTERVAL)

        if not state["merged"]:
            await act.comment_issue(number, f"PR #{pr_number} was closed without merging; stopping work on this issue.")
            await act.unmark_issue(number)
            return f"exit: PR #{pr_number} for issue #{number} was closed without merging"

        # (8) close the issue with a summary comment, then start the next execution
        await act.close_issue_with_summary(number, pr_number, summary)
        next_id = await act.start_next_execution(self_id)
        return f"done: issue #{number} fixed by PR #{pr_number}, issue closed with summary, next execution started ({next_id})"
