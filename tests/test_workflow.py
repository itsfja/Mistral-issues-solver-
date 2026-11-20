import mistralai.workflows as workflows
from mistral_issues_solver import workflows as wf


def test_workflow_is_discovered_and_named():
    classes = workflows.discover_all_workflows_in_package("mistral_issues_solver")
    names = [workflows.get_workflow_definition(c).name for c in classes]
    assert names == ["mistral-issues-solver"]


def test_input_defaults():
    inp = wf.SolverInput()
    assert inp.event == "manual"
    assert inp.exclude_execution_id is None


def test_input_accepts_trigger_events():
    inp = wf.SolverInput(event="issue_labeled", issue_number=446, label="priority: 2")
    assert inp.issue_number == 446
    pr = wf.SolverInput(event="pr_merged", pr_number=491, branch="issues-solver/issue-446")
    assert pr.pr_number == 491
