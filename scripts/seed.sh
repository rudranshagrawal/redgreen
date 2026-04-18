#!/usr/bin/env bash
# Run all 4 seeds in sequence. Each prints its own race log; this
# script rolls up the winners at the end.
#
# `just seed-all` calls this.

set -u
set -o pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PY=".venv/bin/python"
SEEDS=(null_guard input_shape async_race config_drift)

if [ -f .env.local ]; then
    set -a; . ./.env.local; set +a
fi

results=()
for seed in "${SEEDS[@]}"; do
    echo
    echo "####################  $seed  ####################"
    if $PY -m backend.orchestrator --seed "$seed" --cli; then
        results+=("$seed: WINNER")
    else
        results+=("$seed: NO WINNER")
    fi
done

echo
echo "=============== summary ==============="
printf '  %s\n' "${results[@]}"
