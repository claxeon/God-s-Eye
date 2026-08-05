#!/bin/bash
# God's Eye — Daily State Vector Runner
# Called by the SIAIS daily CCR trigger.
# Outputs clean JSON on stdout; progress goes to stderr.
# Supabase writes are handled by the trigger session via MCP (no SUPABASE_KEY needed here).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Load API keys from local .env (gitignored). Routed through sanitize_env.py
# rather than a raw `source .env`: a raw source aborts the whole script
# (set -e) on any line bash's word-splitting can't parse, and bash's own
# parse-error message embeds the offending line's content verbatim -- the
# root cause of the 2026-07-16/17/18 secret-exposure incidents documented
# in SECRET_HANDLING.md. sanitize_env.py never prints a value; it only
# reads KEY=VALUE pairs and re-emits them with guaranteed-safe quoting.
ENV_FILE="$SCRIPT_DIR/.env"
SAFE_ENV_FILE="$SCRIPT_DIR/.env.safe"
if [ -f "$ENV_FILE" ]; then
    python3 "$SCRIPT_DIR/sanitize_env.py" "$ENV_FILE" "$SAFE_ENV_FILE" 1>&2
    set -o allexport
    # shellcheck disable=SC1090
    source "$SAFE_ENV_FILE"
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

# Step 2.2: Kalshi held-position watch — rules-amendment/status/quote-drift
# alerts for the real-money book (G-024). Read-only against the API; never
# places orders. Alerts append to ~/Library/Logs/SIAIS/position_alerts.jsonl
# regardless of this script's own exit code, so a failure here never blocks
# the state vector from computing.
python3 position_watch.py 1>&2 || true

# Step 2.3: FRED Brent prediction auto-resolver (P17/P55). Read-only here —
# it prints TRIGGERED lines to stderr plus the SQL to apply, and the trigger
# session commits them via MCP (this script holds no service-role key).
# Exists because DCOILBRENTEU ran 6 business days stale through the Jul 23-24
# spike to $100+, so the ledger kept reporting "not yet" against a dead price.
python3 resolve_fred_brent_predictions.py 1>&2 || true

# Step 2.4: China crude import tracker (P58) — the trigger variable for
# whether the global deficit becomes priceable. STEO half is automated; the
# observed-imports half is a hand-maintained table (no free monthly source
# exists — EIA international carries China crude imports ANNUALLY to 2018).
python3 china_import_tracker.py 1>&2 || true

# Step 2.45: Pacific carrier watch — counts the China node's own stated
# invasion tripwire ("< 2 battle groups in Pacific"), which had sat in the
# node untracked. USNI Fleet Tracker publishes ~weekly; staleness is printed.
python3 carrier_watch.py 1>&2 || true

# Step 2.46: Semiconductor self-sufficiency gate — the constraint that actually
# gates China's Taiwan kinetic option. carrier_watch (2.45) measures the
# distraction side; this measures whether the door is usable. Also the closest
# thing the framework currently has to a live Leg 7 observable, which is
# otherwise hardcoded at 0.42 (state_vector_compute.py:684).
python3 semi_selfsufficiency_watch.py 1>&2 || true

# Step 2.47: Japan petroleum inventory model. strategic_inventories last
# carries JP at 2026-04-30; METI publishes on a ~2-month lag, so the framework
# is always flying on a stale anchor for the most Hormuz-exposed major economy
# in the model. Rolls the anchor forward on a consumption-vs-import balance.
python3 japan_inventory_model.py 1>&2 || true

# Step 2.48: Carry-trade Channel B — hedged-yield spread (JGB vs FX-hedged UST)
# plus BIS yen-borrow distribution and cohort breakeven. The framework measured
# only Channel A (jpy_spec_short, yen_episode_days ~ half of l_cross), which is
# a ~$13bn futures book; BIS shows ~$2.26tn of JPY cross-border claims actually
# outstanding. Quarterly data, so this mostly reports slowly — run daily anyway
# since the hedged spread moves with rates.
python3 carry_mechanics.py 1>&2 || true

# Step 2.49: RCT trigger monitor — watches the regime boundary (USD/JPY vs the
# 149.6 cohort breakeven) INTERACTED with whether the market is hedged (VIX).
# Proximity alone is not the signal; proximity while volatility is asleep is.
python3 rct_trigger_monitor.py 1>&2 || true

# Step 2.5: Kalman-filtered L(t) — writes state_vector_filtered directly (P-034).
# Runs BEFORE state_vector_compute so today's raw row (inserted by the trigger
# after this script) appears in the NEXT day's filter input; filter output for
# today is based on history through yesterday plus predict step — documented lag.
python3 kalman_lt.py 1>&2 || true

# Step 3: State vector — JSON to stdout for trigger to parse
exec python3 state_vector_compute.py --date "$(date +%Y-%m-%d)" --json
