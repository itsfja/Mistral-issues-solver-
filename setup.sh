#!/usr/bin/env bash
# Setup for the mistral-issues-solver workflow project.
# Safe to re-run. Works from any directory, also via: curl -fsSL <raw-url>/setup.sh | bash
set -euo pipefail

REPO_URL="${SOLVER_REPO_URL:-https://github.com/itsfja/Mistral-issues-solver-.git}"
REPO_BRANCH="${SOLVER_BRANCH:-main}"

log()  { printf '[setup] %s\n' "$*"; }
warn() { printf '[warn] %s\n' "$*" >&2; }
die()  { printf '[error] %s\n' "$*" >&2; exit 1; }

need() { command -v "$1" >/dev/null 2>&1 || die "missing command '$1'. Install it first (e.g. 'sudo apt-get install $2') and re-run this script."; }

# If we are not inside the repo (e.g. curl | bash from another directory), clone it first.
if [ ! -f pyproject.toml ]; then
    if [ -f setup.sh ]; then
        cd "$(dirname "$0")"
    fi
fi
if [ ! -f pyproject.toml ]; then
    need git git
    target="mistral-issues-solver"
    [ -d "$target" ] || { log "cloning $REPO_URL ($REPO_BRANCH) into ./$target"; git clone --depth 1 --branch "$REPO_BRANCH" "$REPO_URL" "$target"; }
    cd "$target"
fi
[ -f pyproject.toml ] || die "pyproject.toml not found even after cloning; is SOLVER_BRANCH=$REPO_BRANCH merged?"

need python3 python3
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
    || die "Python >= 3.11 is required (found $(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')). Install a newer Python and re-run."

if [ ! -d .venv ]; then
    log "creating virtualenv .venv"
    python3 -m venv .venv || die "could not create .venv; install python3-venv (e.g. 'sudo apt-get install python3-venv') and re-run."
fi
# shellcheck disable=SC1091
source .venv/bin/activate

log "upgrading pip"
pip install -q --upgrade pip

log "installing mistral-issues-solver with dev dependencies"
pip install -q -e '.[dev]'

log "running the test suite"
python -m pytest -q

if [ -z "${MISTRAL_API_KEY:-}" ]; then
    warn "MISTRAL_API_KEY is not set: the worker and the run-guard/starter activities need it at run time."
fi
if [ -z "${SECOND_THOUGHT_GH_TOKEN:-}" ]; then
    warn "SECOND_THOUGHT_GH_TOKEN is not set: the managed deployment gets it from workspace secret 'second_thought_gh_token' (a fine-grained PAT for itsfja/second-thought)."
fi

log "done. Next steps:"
log "  1. In Mistral Studio, create workspace secret 'second_thought_gh_token' (fine-grained PAT for itsfja/second-thought: issues + PRs read/write, contents read/write, metadata read)."
log "  2. On itsfja/second-thought, add Actions secret MISTRAL_API_KEY (used by .github/workflows/issues-solver.yml)."
log "  3. Deploy a managed deployment named 'mistral-issues-solver' from this repo (build dir '.', Dockerfile), binding secret 'second_thought_gh_token' as env SECOND_THOUGHT_GH_TOKEN."
log "  4. Local smoke test of the worker: source .venv/bin/activate && MISTRAL_API_KEY=... python -m mistral_issues_solver.worker"
