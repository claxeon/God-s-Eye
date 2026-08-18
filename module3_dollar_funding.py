#!/usr/bin/env python3
"""
God's Eye — Module 3: Dollar Funding and Collateral Stress
==============================================================
Purpose: is global dollar liquidity tightening, and are official facilities
(FIMA, swap lines, ON RRP, reserves) absorbing stress before it forces asset
liquidation? Classifies "stress absorbed by facilities" vs. "stress
transmitted to asset sales" per the module spec.

Free (FRED) inputs wired for real:
  SOFR, IORB              — reused via module1's FREDProvider
  DTWEXBGS                — Broad (nominal) dollar index

Structurally unavailable in this deployment (UnavailableProvider, documented):
  USD/JPY, EUR/USD, KRW/USD cross-currency basis — no free swaps-basis feed.
    (carry_mechanics.py's BASIS_BAND (0.0, -0.25) is an ASSUMED sensitivity
    band for the hedged-spread calc, not an observed basis print — do not
    treat it as a live basis observation.)
  FRA-OIS / modern equivalent, FIMA repo usage, central-bank swap-line usage,
  repo specialness/fails, FX implied vol / risk reversals, offshore funding
  spread proxies — all need a paid/institutional feed.

Falsifier (per spec): if FIMA, swap lines, repo facilities, and reserve
liquidity expand while basis and repo stress normalize, do not infer a
forced Treasury-selling cascade.

Run: python3 module3_dollar_funding.py [--json]
"""
from __future__ import annotations

import argparse
import sys
from datetime import date

from godseye_modules_common import (
    ConfidenceTier, ModuleResult, Observable, SignalState, UnavailableProvider,
    load_config, now_iso, supabase_upsert,
)

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from module1_treasury_plumbing import FREDProvider  # reuse, don't duplicate


UNAVAILABLE = [
    ("usdjpy_cross_currency_basis", "no free cross-currency basis swap feed"),
    ("eurusd_cross_currency_basis", "no free cross-currency basis swap feed"),
    ("krwusd_cross_currency_basis", "no free cross-currency basis swap feed"),
    ("fra_ois_spread", "no free FRA-OIS or modern-equivalent feed"),
    ("fima_repo_facility_usage", "Fed publishes aggregate only, not attributable/live"),
    ("fed_swap_line_usage", "not broken out in a free, timely series"),
    ("foreign_cb_swap_line_usage", "same as fed_swap_line_usage"),
    ("repo_specialness", "needs GCF/tri-party repo data, not freely scraped"),
    ("repo_fails", "same limitation as module1's repo_fails"),
    ("fx_implied_vol", "needs an options-data vendor"),
    ("fx_risk_reversals", "needs an options-data vendor"),
    ("offshore_funding_spread_proxy", "needs a dedicated offshore-dollar funding series (e.g. CIP deviations), not freely available at daily frequency"),
]


def compute() -> ModuleResult:
    cfg = load_config("module3_dollar_funding")
    fred = FREDProvider()
    obs = []
    metrics = {}
    missing = []
    dq_notes = []
    freshness = {}

    for name, series_id in [("sofr", "SOFR"), ("iorb", "IORB"), ("dollar_index_broad", "DTWEXBGS")]:
        res = fred.fetch(series_id)
        obs.append(Observable(name, res.value, None, res.source, res.obs_date,
                               res.confidence, is_unknown=res.is_unknown, note=res.note))
        metrics[name] = res.value
        freshness[series_id] = res.obs_date
        if res.is_unknown:
            missing.append(name)

    if metrics.get("sofr") is not None and metrics.get("iorb") is not None:
        metrics["sofr_minus_iorb_bps"] = round((metrics["sofr"] - metrics["iorb"]) * 100, 1)
    else:
        metrics["sofr_minus_iorb_bps"] = None

    for name, reason in UNAVAILABLE:
        stub = UnavailableProvider(name, reason)
        res = stub.fetch()
        obs.append(Observable(name, None, None, res.source, None, res.confidence,
                               is_unknown=True, note=res.note))
        missing.append(name)
        dq_notes.append(f"{name}: {res.note}")

    breaches = {"watch": False, "elevated": False, "critical": False}
    sofr_iorb = metrics.get("sofr_minus_iorb_bps")
    if sofr_iorb is not None:
        if sofr_iorb >= cfg["sofr_iorb_elevated_bps"]:
            breaches["elevated"] = True
        elif sofr_iorb >= cfg["sofr_iorb_watch_bps"]:
            breaches["watch"] = True

    # Per the spec's falsifier, this module explicitly CANNOT determine
    # "stress absorbed by facilities" vs. "stress transmitted to asset sales"
    # without the FIMA/swap-line/basis feeds it doesn't have — so it reports
    # the classification as unknown rather than guessing.
    metrics["stress_classification"] = "UNKNOWN — requires FIMA/swap-line/cross-currency-basis feeds not wired in this deployment"

    dq_notes.append(
        f"{len(missing)} of {len(missing) + 3} tracked observables are unknown. "
        "SOFR-IORB alone is a weak proxy for funding stress; do not treat a NORMAL "
        "read here as confirmation of orderly funding markets broadly."
    )

    signal_state = SignalState.WATCH if breaches["watch"] else (
        SignalState.ELEVATED if breaches["elevated"] else SignalState.NORMAL
    )
    if metrics.get("sofr_minus_iorb_bps") is None:
        signal_state = SignalState.DATA_QUALITY_FAILURE

    falsifiers = [
        "If FIMA, swap lines, repo facilities, and reserve liquidity expand while basis "
        "and repo stress normalize, do not infer a forced Treasury-selling cascade. "
        "(This module cannot currently observe FIMA/swap-line usage to check this — "
        "treat any escalation above WATCH as provisional pending those feeds.)",
    ]

    return ModuleResult(
        module_id="M3_dollar_funding",
        purpose="Track whether global dollar liquidity is tightening and whether official "
                "facilities are absorbing stress without forcing asset liquidation.",
        computed_at=now_iso(),
        signal_state=signal_state,
        confidence=ConfidenceTier.SPECULATIVE if len(missing) > 6 else ConfidenceTier.INFERRED,
        metrics=metrics,
        observables=obs,
        falsifiers=falsifiers,
        data_quality_notes=dq_notes,
        missing_data=missing,
        source_freshness=freshness,
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
        print(f"M3 Dollar Funding — {result.signal_state.value} ({result.confidence.value})", file=sys.stderr)
        print(f"  SOFR-IORB: {result.metrics.get('sofr_minus_iorb_bps')} bps", file=sys.stderr)
        print(f"  {len(result.missing_data)} observables unknown", file=sys.stderr)

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
