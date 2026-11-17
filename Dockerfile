FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DEPLOYMENT_NAME=mistral-issues-solver

RUN apt-get update \
 && apt-get install -y --no-install-recommends git curl ca-certificates gnupg \
 && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install .

# The workflow runs the target repo's test suite in-worker (headless Chromium for the e2e tests).
RUN pip install playwright ruff \
 && playwright install --with-deps chromium

CMD ["python", "-m", "mistral_issues_solver.worker"]
