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
exec python3 state_vector_compute.py --date "$(date +%Y-%m-%d)" --json
