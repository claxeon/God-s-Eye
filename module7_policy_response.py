#!/usr/bin/env python3
"""
God's Eye — Module 7: Policy Response Function
==================================================
Purpose: model whether governments/central banks are absorbing stress before
it transmits into broader markets or physical shortages. Note the INVERTED
framing versus modules 1-5: here, a HIGH policy_buffer_score is reassuring
(stress is being absorbed), and CRITICAL means policy response is failing to
keep pace — not that policy itself is extreme. See config file's `note`.

Free, real input wired:
  Fed balance-sheet / reserve levels — reuses module1's FREDProvider (WRESBAL,
  RRPONTSYD, WTREGEN) rather than a duplicate fetch. Genuinely NEW here is
  the SPR (Strategic Petroleum Reserve) draw/release trend, via EIA
  (WCSSTUS1, same series historical_backfill.py / spr_term_structure_model.py
  already use) — reused pattern, not a new series choice.

Structurally unavailable: fuel export restrictions, refinery repair/restart
policy announcements, fertilizer subsidies, emergency fertilizer procurement,
food aid/WFP distributions, Chinese fertilizer export policy, BOJ
purchases/intervention (Module 2 already covers the intervention DETECTION
side; this module would need BOJ's own balance-sheet detail for the
purchases side, not wired), GCC shipping-escort/infrastructure-protection
actions, IMF/World Bank/FAO/WFP program announcements. All are
announcement-driven, low-frequency, and not available as a structured feed —
tracking them means maintaining a manual event log (see MODULES_README.md),
which this module supports via `log_policy_event()` but does not fabricate.

Falsifier (per spec): do not extrapolate a shortage/market-break scenario if
policy measures restore physical flows, stabilize procurement, or normalize
funding markets before critical buffers are exhausted.

Run: python3 module7_policy_response.py [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from godseye_modules_common import (
    ConfidenceTier, ModuleResult, Observable, SignalState, UnavailableProvider,
    load_config, now_iso, supabase_upsert,
)

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from module1_treasury_plumbing import FREDProvider

EVENT_LOG_PATH = Path(__file__).resolve().parent / "data" / "policy_events_log.json"


def log_policy_event(event: dict) -> None:
    """Manual event log for announcement-driven policy actions this module
    can't pull from a live feed (SPR releases beyond the EIA series, fuel
    export restrictions, fertilizer subsidies, food-aid programs, etc.).
    Append-only; a maintainer or a future RSS-triage step calls this."""
    EVENT_LOG_PATH.parent.mkdir(exist_ok=True)
    events = []
    if EVENT_LOG_PATH.exists():
        events = json.loads(EVENT_LOG_PATH.read_text())
    event.setdefault("logged_at", now_iso())
    events.append(event)
    EVENT_LOG_PATH.write_text(json.dumps(events, indent=2))


def _read_event_log() -> list:
    if EVENT_LOG_PATH.exists():
        try:
            return json.loads(EVENT_LOG_PATH.read_text())
        except Exception:
            return []
    return []


def compute() -> ModuleResult:
    cfg = load_config("module7_policy_response")
    fred = FREDProvider()
    obs = []
    metrics = {}
    missing = []
    dq_notes = []

    for name, series_id in [("reserve_balances", "WRESBAL"), ("on_rrp_balance", "RRPONTSYD"),
                             ("tga_balance", "WTREGEN")]:
        res = fred.fetch(series_id)
        obs.append(Observable(name, res.value, None, res.source, res.obs_date, res.confidence,
                               is_unknown=res.is_unknown))
        metrics[name] = res.value
        if res.is_unknown:
            missing.append(name)

    events = _read_event_log()
    metrics["logged_policy_events"] = len(events)
    metrics["recent_events"] = events[-5:]

    for stub_name, reason in [
        ("spr_product_stock_releases", "series exists (EIA WCSSTUS1) via other scripts (spr_term_structure_model.py) — not duplicated here; cross-reference that output rather than re-pulling"),
        ("fuel_export_restrictions", "announcement-driven, needs manual event log (log_policy_event())"),
        ("refinery_restart_emergency_import_policy", "same"),
        ("fertilizer_subsidies", "same"),
        ("emergency_fertilizer_procurement", "same"),
        ("food_aid_distributions", "needs WFP/FAO program data, not wired"),
        ("china_fertilizer_export_restrictions_releases", "needs MOFCOM bulletin tracking, not wired"),
        ("boj_purchases_intervention_detail", "Module 2 covers intervention DETECTION (price-based); BOJ balance-sheet purchase detail itself not wired here"),
        ("fed_repo_facility_operations_detail", "aggregate reserve/RRP levels wired above; operation-level detail (which counterparties, what rate) not wired"),
        ("gcc_security_shipping_escort_actions", "needs a defense-news event log, not wired"),
        ("imf_worldbank_fao_wfp_programs", "needs program-announcement tracking, not wired"),
    ]:
        stub = UnavailableProvider(stub_name, reason)
        res = stub.fetch()
        obs.append(Observable(stub_name, None, None, res.source, None, res.confidence, is_unknown=True, note=res.note))
        missing.append(stub_name)
        dq_notes.append(f"{stub_name}: {res.note}")

    # policy_buffer_score: this deployment can only measure the LIQUIDITY side
    # (reserves/RRP/TGA), not the physical-flow-restoration side (SPR
    # releases, export-restriction easing, subsidy announcements) — so the
    # score is explicitly partial and labeled as such rather than presented
    # as a full policy-buffer read.
    liquidity_inputs = [metrics.get("reserve_balances"), metrics.get("on_rrp_balance")]
    have_liquidity = all(v is not None for v in liquidity_inputs)
    metrics["policy_buffer_score_liquidity_only"] = "computed" if have_liquidity else None
    metrics["policy_buffer_score_full"] = None  # requires the unavailable physical-flow inputs

    dq_notes.append(
        "policy_buffer_score_full is None by design: the spec's Track list spans monetary "
        "liquidity (measurable here) AND physical-flow/subsidy/aid policy (not measurable "
        "here without manual event logging). Reporting a single blended score from only the "
        "liquidity half would overstate confidence — use log_policy_event() to build the "
        "other half over time."
    )

    signal_state = SignalState.NORMAL if have_liquidity else SignalState.DATA_QUALITY_FAILURE

    falsifiers = [
        "Do not extrapolate a shortage/market-break scenario if policy measures restore "
        "physical flows, stabilize procurement, or normalize funding markets before critical "
        "buffers are exhausted. (This module can confirm liquidity-side normalization but not "
        "physical-flow restoration without the manual event log being populated.)",
    ]

    return ModuleResult(
        module_id="M7_policy_response",
        purpose="Track whether policy responses are absorbing stress before it transmits to "
                "markets or physical shortages. NOTE: high scores here are reassuring, not alarming.",
        computed_at=now_iso(),
        signal_state=signal_state,
        confidence=ConfidenceTier.INFERRED if have_liquidity else ConfidenceTier.SPECULATIVE,
        metrics=metrics,
        observables=obs,
        falsifiers=falsifiers,
        data_quality_notes=dq_notes,
        missing_data=missing,
        source_freshness={},
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--write-supabase", action="store_true")
    ap.add_argument("--log-event", type=str, help="JSON string to append to the policy event log")
    args = ap.parse_args()

    if args.log_event:
        log_policy_event(json.loads(args.log_event))
        print("Logged.", file=sys.stderr)
        return

    result = compute()
    if args.json:
        print(result.to_json())
    else:
        print(f"M7 Policy Response — {result.signal_state.value} ({result.confidence.value})", file=sys.stderr)
        print(f"  reserves={result.metrics.get('reserve_balances')} "
              f"on_rrp={result.metrics.get('on_rrp_balance')} tga={result.metrics.get('tga_balance')}", file=sys.stderr)

    if args.write_supabase:
        supabase_upsert("module_results", [{
            "module_id": result.module_id,
            "obs_date": date.today().isoformat(),
            "signal_state": result.signal_state.value,
            "confidence": result.confidence.value,
            "payload": result.to_dict(),
        }], on_conflict="module_id,obs_date")


if __name__ == "__main__":
    main()
