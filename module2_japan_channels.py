#!/usr/bin/env python3
"""
God's Eye — Module 2: Japan Three-Channel Decomposition
==========================================================
Purpose: "Japan sells Treasuries" is not one mechanism. Separate:
  Channel A — MOF/BOJ FX intervention (mobilizes dollars/bills, not
              necessarily long-duration UST selling)
  Channel B — Institutional reallocation (life insurers/pensions/banks
              responding to the FX-hedged JGB-vs-UST yield differential)
  Channel C — Leveraged yen-carry unwind (a risk-asset/leverage event first,
              a Treasury event only incidentally)

This module does NOT recompute Channel A/B/C from scratch. It WRAPS the
framework's existing instruments:
  Channel A + C  -> yen_mechanics.py::run_yen_mechanics()
                    (intervention detection via >=1.5% single-day USD/JPY
                     move; CFTC IMM JPY speculative positioning)
  Channel B      -> carry_mechanics.py's fred_last/jpy_3m_rate/jgb_proxy
                    (FX-hedged UST vs JGB yield spread)
because those already exist, are validated (see backfill_yen_mechanics.py,
Intelligence Briefs/Asian FX Intervention & the UST Channel - 2026-07-31.md),
and duplicating the calculations would risk exactly the kind of two-source
drift the vault's TIC notes had to reconcile by hand. This module's job is
classification and falsification on TOP of those two scripts' outputs, per
Module 2's spec, not re-deriving the numbers.

Falsifiers (per spec, checked explicitly below):
  - If JGB yields rise but hedged Treasury pickup remains strongly positive
    and Japanese flows remain outward, downgrade the reallocation thesis.
  - If yen intervention occurs without a reduction in Treasury holdings (or
    with heavy FIMA use), do not claim direct Treasury selling.
  - If USD/JPY is stable/weakening while risk assets are orderly, do not
    label it a leveraged carry unwind.

Note: FIMA repo usage and Japan's TIC holdings-by-maturity are NOT free-feed
observables in this deployment (documented UnavailableProvider stubs) — so
the "heavy FIMA use" falsifier can be evaluated only qualitatively (from
Intelligence Briefs, e.g. the confirmed May MOF intervention/¥11.73tn +
90%-bills TIC composition), not from a live feed. This is logged, not hidden.

Run: python3 module2_japan_channels.py [--json]
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from typing import Optional

from godseye_modules_common import (
    ConfidenceTier, ModuleResult, Observable, SignalState, UnavailableProvider,
    load_config, now_iso, supabase_upsert,
)

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import yen_mechanics  # noqa: E402
import carry_mechanics  # noqa: E402


def _channel_b_spread() -> dict:
    """Recreate carry_mechanics.py's hedged-spread calc at basis=0.0 only
    (the tightest end of BASIS_BAND) without triggering its BIS/yfinance
    calls — this module only needs the spread sign/magnitude, not the full
    cohort-breakeven report main() prints."""
    _, us10 = carry_mechanics.fred_last("DGS10")
    _, us3m = carry_mechanics.fred_last("DTB3")
    jpy3m, jpy3m_meta = carry_mechanics.jpy_3m_rate()
    jgb10, jgb_meta = carry_mechanics.jgb_proxy()
    if None in (us10, us3m, jpy3m, jgb10):
        return {"ok": False, "reason": "missing one or more rate inputs"}
    hedge_cost = us3m - jpy3m - 0.0
    hedged_ust = us10 - hedge_cost
    spread = jgb10 - hedged_ust
    return {
        "ok": True,
        "us_10y": us10, "us_3m": us3m, "jpy_3m": jpy3m, "jpy_3m_source": jpy3m_meta,
        "jgb_10y": jgb10, "jgb_source": jgb_meta,
        "raw_gap": round(us10 - jgb10, 3),
        "hedged_ust": round(hedged_ust, 3),
        "spread_vs_jgb_pp": round(spread, 3),
        "repatriation_favoured": spread > 0,
    }


def compute() -> ModuleResult:
    cfg = load_config("module2_japan_channels")
    obs: list[Observable] = []
    metrics: dict = {}
    dq_notes: list = []
    missing: list = []
    channel_states: dict = {}

    # ── Channels A + C, via yen_mechanics ───────────────────────────────────
    try:
        ym_row = yen_mechanics.run_yen_mechanics()
    except Exception as e:  # noqa: BLE001
        ym_row = None
        dq_notes.append(f"yen_mechanics.run_yen_mechanics() failed: {e}")

    if ym_row:
        spot = ym_row.get("usdjpy_spot")
        daily_pct = ym_row.get("usdjpy_daily_change_pct")
        intervention_flag = ym_row.get("intervention_flag")
        dominant_pressure = ym_row.get("dominant_pressure")
        nc_pct_oi = ym_row.get("jpy_nc_pct_oi")

        obs.append(Observable("usdjpy_spot", spot, "JPY/USD", "yen_mechanics.py (FRED DEXJPUS)",
                               ym_row.get("as_of_date"), ConfidenceTier.CONFIRMED))
        obs.append(Observable("usdjpy_daily_change_pct", daily_pct, "%", "yen_mechanics.py",
                               ym_row.get("as_of_date"), ConfidenceTier.CONFIRMED))
        obs.append(Observable("cftc_jpy_nc_pct_oi", nc_pct_oi, "% of OI", "yen_mechanics.py (CFTC TFF)",
                               ym_row.get("cot_report_date"), ConfidenceTier.CONFIRMED,
                               is_unknown=nc_pct_oi is None))

        # Channel A: intervention detection is yen_mechanics' existing
        # >=1.5% single-day-move heuristic (config threshold mirrors it so a
        # future retune only needs one edit, in yen_mechanics' own constant —
        # this module reads the FLAG it already computed, not a duplicate).
        channel_a_state = SignalState.ELEVATED if intervention_flag else SignalState.NORMAL
        metrics["channel_a_intervention_flag"] = bool(intervention_flag)
        metrics["channel_a_daily_move_pct"] = daily_pct
        channel_states["channel_a"] = channel_a_state.value

        # Channel C: leveraged carry unwind requires BOTH yen strength beyond
        # threshold AND corroborating risk-asset stress (the module spec's
        # own falsifier). yen_mechanics' CFTC positioning tells us how
        # crowded the speculative book is; it does NOT by itself confirm a
        # risk-asset event, which this module has no live feed for (VIX/
        # credit spreads are tracked elsewhere in the existing framework, not
        # duplicated here) — so Channel C can reach WATCH from yen data alone
        # but never ELEVATED/CRITICAL without that external corroboration,
        # which is logged as missing rather than assumed.
        yen_strength_pct = -daily_pct if daily_pct is not None else None  # positive = yen strengthened
        channel_c_watch = (
            yen_strength_pct is not None
            and yen_strength_pct >= cfg["channel_c_yen_appreciation_watch_pct_1d"]
        )
        metrics["channel_c_yen_strength_pct_1d"] = yen_strength_pct
        missing.append("channel_c_risk_asset_confirmation (VIX/credit-spread corroboration not wired into this module)")
        channel_states["channel_c"] = (SignalState.WATCH if channel_c_watch else SignalState.NORMAL).value
        dq_notes.append(
            "Channel C falsifier check: 'if USD/JPY is stable/weakening while risk assets are "
            "orderly, do not label it a leveraged carry unwind' — this module cannot confirm risk-asset "
            "orderliness (no VIX/credit feed wired), so it never escalates Channel C past WATCH on its own."
        )
    else:
        missing += ["usdjpy_spot", "channel_a_intervention_flag", "channel_c_yen_strength_pct_1d"]
        channel_states["channel_a"] = SignalState.DATA_QUALITY_FAILURE.value
        channel_states["channel_c"] = SignalState.DATA_QUALITY_FAILURE.value

    # ── Channel B, via carry_mechanics ──────────────────────────────────────
    cb = _channel_b_spread()
    if cb.get("ok"):
        obs.append(Observable("hedged_ust_vs_jgb_spread_pp", cb["spread_vs_jgb_pp"], "pp",
                               "carry_mechanics.py (FRED + BOJ policy proxy)", date.today().isoformat(),
                               ConfidenceTier.INFERRED,  # INFERRED not CONFIRMED: JPY 3M / JGB proxy are themselves derived
                               note="Positive = JGBs out-yield hedged USTs = repatriation favoured"))
        metrics["channel_b_hedged_spread_pp"] = cb["spread_vs_jgb_pp"]
        metrics["channel_b_repatriation_favoured"] = cb["repatriation_favoured"]
        channel_b_state = (
            SignalState.ELEVATED if cb["spread_vs_jgb_pp"] >= cfg["channel_b_repatriation_incentive_elevated_pp"]
            else SignalState.WATCH if cb["spread_vs_jgb_pp"] >= cfg["channel_b_hedged_spread_watch_pp"]
            else SignalState.NORMAL
        )
        channel_states["channel_b"] = channel_b_state.value
    else:
        missing.append("channel_b_hedged_spread_pp")
        dq_notes.append(f"Channel B unavailable: {cb.get('reason')}")
        channel_states["channel_b"] = SignalState.DATA_QUALITY_FAILURE.value

    # ── Structurally unavailable: FIMA repo usage, TIC holdings-by-maturity ─
    for stub_name, reason in [
        ("fima_repo_usage", "Fed does not publish counterparty-level FIMA repo usage; only aggregate facility totals, and not on a Japan-attributable basis"),
        ("tic_holdings_by_maturity_japan", "TIC country tables do not break down by maturity bucket; see Intelligence Briefs/TIC Analysis - April-May 2026.md for the closest available proxy (bills vs. long-term composition, reported after the fact)"),
    ]:
        stub = UnavailableProvider(stub_name, reason)
        res = stub.fetch()
        obs.append(Observable(stub_name, None, None, res.source, None, res.confidence,
                               is_unknown=True, note=res.note))
        missing.append(stub_name)
        dq_notes.append(f"{stub_name}: {res.note}")

    overall_order = [SignalState.NORMAL, SignalState.WATCH, SignalState.ELEVATED,
                      SignalState.CRITICAL, SignalState.DATA_QUALITY_FAILURE]
    worst = max((SignalState(v) for v in channel_states.values()),
                key=lambda s: overall_order.index(s) if s != SignalState.DATA_QUALITY_FAILURE else 3)
    # DATA_QUALITY_FAILURE on one channel shouldn't mask a real signal on
    # another — report the worst NON-DQF state as the headline, and surface
    # any DQF channel in data_quality_notes instead of hiding it.
    non_dqf = [SignalState(v) for v in channel_states.values() if v != SignalState.DATA_QUALITY_FAILURE.value]
    signal_state = max(non_dqf, key=overall_order.index) if non_dqf else SignalState.DATA_QUALITY_FAILURE
    metrics["channel_states"] = channel_states

    falsifiers = [
        "Channel B: if JGB yields rise but hedged Treasury pickup remains strongly positive "
        "and Japanese flows remain outward, downgrade the reallocation thesis.",
        "Channel A: if yen intervention occurs without a reduction in Treasury holdings, or "
        "with heavy FIMA use, do not claim direct Treasury selling. (FIMA usage is structurally "
        "unavailable here — see data_quality_notes; this falsifier can only be checked "
        "qualitatively via dated Intelligence Briefs, not this module's live feed.)",
        "Channel C: if USD/JPY is stable or weakening while risk assets are orderly, do not "
        "label it a leveraged carry unwind. (Risk-asset orderliness is not wired into this "
        "module — Channel C is capped at WATCH here for that reason.)",
    ]

    return ModuleResult(
        module_id="M2_japan_channels",
        purpose="Decompose 'Japan sells Treasuries' into FX intervention (A), institutional "
                "reallocation (B), and leveraged carry unwind (C) — do not conflate them.",
        computed_at=now_iso(),
        signal_state=signal_state,
        confidence=ConfidenceTier.INFERRED,
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
    args = ap.parse_args()

    result = compute()
    if args.json:
        print(result.to_json())
    else:
        print(f"\nM2 Japan Channels — headline {result.signal_state.value}", file=sys.stderr)
        print(f"  channel states: {result.metrics.get('channel_states')}", file=sys.stderr)

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
