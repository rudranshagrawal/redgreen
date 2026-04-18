#!/usr/bin/env bash
# RedGreen — `just verify`. One command to prove the demo still has a heartbeat.
#
# Exits non-zero on any failure. Safe to run before every commit.
# Adapted from the ECC verification-loop skill (build/type/lint/test/security),
# narrowed to the things that actually matter for this hackathon.

set -u
set -o pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

pass=0
fail=0
PY=".venv/bin/python"

ok()   { echo "  PASS  $1"; pass=$((pass+1)); }
bad()  { echo "  FAIL  $1"; fail=$((fail+1)); }
note() { echo "        $1"; }

section() { echo; echo "-- $1 --"; }

# Load env if present.
if [ -f .env.local ]; then
    set -a; . ./.env.local; set +a
fi

section "contracts"
if $PY -c "from contracts.schemas import AnalyzeRequest, RunRequest, StatusResponse" 2>/dev/null; then
    ok "schemas import"
else
    bad "contracts/schemas.py fails to import"
fi

section "env"
for k in OPENAI_API_KEY NEBIUS_API_KEY SUPABASE_URL SUPABASE_SERVICE_ROLE_KEY; do
    if [ -n "${!k:-}" ]; then
        ok "$k set"
    else
        bad "$k missing"
    fi
done

section "reachability"
if [ -n "${OPENAI_API_KEY:-}" ]; then
    code=$(curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $OPENAI_API_KEY" https://api.openai.com/v1/models || echo 000)
    [ "$code" = "200" ] && ok "OpenAI /v1/models 200" || bad "OpenAI returned $code"
fi
if [ -n "${NEBIUS_API_KEY:-}" ]; then
    code=$(curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $NEBIUS_API_KEY" "${NEBIUS_BASE_URL:-https://api.studio.nebius.ai/v1/}models" || echo 000)
    [ "$code" = "200" ] && ok "Nebius /v1/models 200" || bad "Nebius returned $code"
fi
if [ -n "${SUPABASE_URL:-}" ] && [ -n "${SUPABASE_SERVICE_ROLE_KEY:-}" ]; then
    code=$(curl -s -o /dev/null -w "%{http_code}" -H "apikey: $SUPABASE_SERVICE_ROLE_KEY" -H "Authorization: Bearer $SUPABASE_SERVICE_ROLE_KEY" "$SUPABASE_URL/rest/v1/episodes?select=id&limit=1" || echo 000)
    [ "$code" = "200" ] && ok "Supabase /rest/v1/episodes 200" || bad "Supabase returned $code"
fi

section "docker"
if command -v docker >/dev/null 2>&1; then
    if docker info >/dev/null 2>&1; then
        ok "docker daemon reachable"
        if docker image inspect "${RUNNER_DOCKER_IMAGE:-redgreen-runner:dev}" >/dev/null 2>&1; then
            ok "runner image built (${RUNNER_DOCKER_IMAGE:-redgreen-runner:dev})"
        else
            note "runner image missing — run: just runner-build"
        fi
    else
        bad "docker daemon not running"
    fi
else
    bad "docker not installed"
fi

section "seeds"
if [ -d seeds/null_guard ]; then
    # null_guard crash.py must actually crash — the RED baseline for the whole product.
    if $PY seeds/null_guard/crash.py >/dev/null 2>&1; then
        bad "null_guard/crash.py should raise, but exited 0"
    else
        ok "null_guard/crash.py reproduces TypeError"
    fi
    # Existing happy-path tests must still pass — protects us from breaking seeds while iterating.
    (cd seeds/null_guard && $REPO_ROOT/$PY -m pytest tests/ -q >/dev/null 2>&1) && ok "null_guard happy-path pytest green" || bad "null_guard happy-path pytest failing"
else
    bad "seeds/null_guard missing"
fi

section "summary"
echo "  $pass passed, $fail failed"
exit $fail
