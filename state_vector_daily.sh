#!/bin/bash
# God's Eye — Daily State Vector Runner
# Called by the SIAIS daily CCR trigger.
# Outputs clean JSON on stdout; progress goes to stderr.
# Supabase writes are handled by the trigger session via MCP (no SUPABASE_KEY needed here).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Load API keys from local .env (gitignored)
ENV_FILE="$SCRIPT_DIR/.env"
if [ -f "$ENV_FILE" ]; then
    set -o allexport
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +o allexport
fi

cd "$SCRIPT_DIR"

# Step 1: Physical inventory tracker — runs first so state vector can read from Supabase
python3 inventory_tracker.py 1>&2 || true

# Step 1.5: Market mechanics — COT + EIA flows + physical-financial divergence
python3 market_mechanics.py 1>&2 || true

# Step 1.6: Yen mechanics — USD/JPY key levels + CFTC IMM + BOJ rate dilemma
python3 yen_mechanics.py 1>&2 || true

# Step 2: Polymarket snapshot + STEO refresh + data condition checks
python3 polymarket_snapshot.py 1>&2 || true

# Step 2.5: Kalman-filtered L(t) — writes state_vector_filtered directly (P-034).
# Runs BEFORE state_vector_compute so today's raw row (inserted by the trigger
# after this script) appears in the NEXT day's filter input; filter output for
# today is based on history through yesterday plus predict step — documented lag.
python3 kalman_lt.py 1>&2 || true

# Step 3: State vector — JSON to stdout for trigger to parse
exec python3 state_vector_compute.py --date "$(date +%Y-%m-%d)" --json
