import datetime
import os

WORKFLOW_NAME = "mistral-issues-solver"
DEPLOYMENT_NAME = os.environ.get("DEPLOYMENT_NAME") or WORKFLOW_NAME
TARGET_REPO = os.environ.get("TARGET_REPO", "itsfja/second-thought")
OWNER, REPO = TARGET_REPO.split("/")
GITHUB_TOKEN = os.environ.get("SECOND_THOUGHT_GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
MISTRAL_API_KEY = os.environ.get("MISTRAL_API_KEY", "")
MODEL = os.environ.get("SOLVER_MODEL", "mistral-large-latest")
REVIEWER = os.environ.get("SOLVER_REVIEWER", "itsfja")

IN_PROGRESS_LABEL = "autofix-in-progress"
BRANCH_PREFIX = "issues-solver/issue-"
PRIORITY_LEVELS = list(range(1, 9))
MAX_FIX_ATTEMPTS = 3
POLL_INTERVAL = datetime.timedelta(minutes=5)
WORK_ROOT = "/tmp/solver-work"

# Paths whose changes require `python tools/sync.py` to rebuild generated files (CONTRIBUTING.md).
SYNC_TRIGGER_PATHS = (
    "python/second_thought/",
    "i18n/",
    "gallery/",
    "prompts/",
    "samples/",
    "web/",
    "second-thought.example.ini",
)
NEW_FILE_ALLOWED_DIRS = ("gallery/",)
MAX_CHANGES_BYTES = 1_500_000
