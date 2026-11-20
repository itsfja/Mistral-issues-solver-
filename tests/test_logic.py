from mistral_issues_solver import logic


def test_priority_label_variants_cover_both_spellings():
    assert logic.priority_label_variants(2) == ["priority: 2", "priority:2"]


def test_issue_search_query():
    q = logic.issue_search_query("itsfja/second-thought", "priority: 1")
    assert 'repo:itsfja/second-thought is:issue is:open label:"priority: 1" sort:created-asc' == q


def test_parse_closes_issues():
    assert logic.parse_closes_issues("Closes #12, fixes #7 and resolves #12") == [12, 7]
    assert logic.parse_closes_issues("") == []
    assert logic.parse_closes_issues(None) == []
    assert logic.parse_closes_issues("no refs here") == []


def test_extract_json_object_plain_and_fenced():
    assert logic.extract_json_object('{"a": 1}') == {"a": 1}
    assert logic.extract_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert logic.extract_json_object('prefix {"a": {"b": 2}} suffix') == {"a": {"b": 2}}


def test_validate_changes_accepts_existing_and_allowed_new_paths():
    tree = ["README.md", "python/runtime.py"]
    changes = [
        {"path": "README.md", "content": "x"},
        {"path": "gallery/new-program.json", "content": "{}"},
    ]
    assert logic.validate_changes(changes, tree, ("gallery/",), 1_500_000) == []


def test_validate_changes_rejects_unknown_unsafe_and_oversized():
    tree = ["README.md"]
    errors = logic.validate_changes(
        [
            {"path": "nope/evil.py", "content": "x"},
            {"path": "../escape.py", "content": "x"},
            {"path": "README.md"},
            {"path": "README.md", "content": "y" * 2_000_000},
        ],
        tree,
        ("gallery/",),
        1_500_000,
    )
    assert any("unknown path" in e for e in errors)
    assert any("unsafe path" in e for e in errors)
    assert any("missing content" in e for e in errors)
    assert any("too large" in e for e in errors)


def test_filter_running_run_ids_dicts_and_objects():
    class R:
        def __init__(self, execution_id):
            self.execution_id = execution_id

    runs = [{"execution_id": "a"}, {"run_id": "b"}, R("c"), {"id": "a"}]
    assert sorted(logic.filter_running_run_ids(runs, {"a"})) == ["b", "c"]
