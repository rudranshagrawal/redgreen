# RedGreen — hackathon build recipes.
# Use `just` (https://github.com/casey/just). All recipes run from repo root.

set dotenv-load := true
set shell := ["bash", "-cu"]

# Use the repo-local venv so we don't fight PEP 668 system-python.
PY := ".venv/bin/python"

# Default: list available recipes.
default:
    @just --list

# First-time setup: make the venv + install dev deps.
bootstrap:
    test -d .venv || python3 -m venv .venv
    .venv/bin/pip install --upgrade pip
    .venv/bin/pip install pydantic fastapi uvicorn httpx openai supabase python-dotenv

# Quick env + reachability sanity check (fast subset of `verify`).
check:
    {{PY}} -c "from contracts.schemas import AnalyzeRequest, RunRequest, StatusResponse; print('contracts ok')"
    @{{PY}} -c "import os; missing=[k for k in ('OPENAI_API_KEY','NEBIUS_API_KEY','SUPABASE_URL','SUPABASE_SERVICE_ROLE_KEY') if not os.environ.get(k)]; print('env missing:', missing) if missing else print('env ok')"

# Full pre-commit gate: contracts + env + reachability + docker + seeds. Must pass before backend/runner/plugin commits (hard rule #10).
verify:
    bash scripts/verify.sh

# Bring up backend + runner + web for local dev.
dev:
    bash scripts/dev.sh

# Run the 60-second demo sequence headless.
demo:
    bash scripts/demo.sh

# One episode end-to-end from the CLI (no plugin needed).
seed name:
    {{PY}} -m backend.orchestrator --seed {{name}} --cli

# All four seeds, RED->GREEN verification.
seed-all:
    bash scripts/seed.sh

# Single-seed smoke test through the Docker runner only.
test-seed name:
    {{PY}} -m runner.run_test --seed {{name}} --gate red
    {{PY}} -m runner.run_test --seed {{name}} --gate green

# Build the pytest runner Docker image.
runner-build:
    docker build -t "${RUNNER_DOCKER_IMAGE:-redgreen-runner:dev}" runner/

# Deploy the leaderboard web app to Vercel production.
web-deploy:
    cd web && vercel --prod --yes

# Launch a PyCharm sandbox with the RedGreen plugin loaded.
plugin-run:
    cd plugin && ./gradlew runIde
