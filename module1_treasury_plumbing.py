#!/usr/bin/env python3
"""
God's Eye — Module 1: Treasury Market Plumbing
=================================================
Purpose: distinguish ordinary long-end yield repricing from impaired Treasury
market functioning (weak real-money demand, dealer-balance-sheet stress,
funding-market dislocation). Complements, does not replace, the framework's
existing L2 (GCC/Petrodollar) leg and the TIC-flow analysis already in
Intelligence Briefs/TIC Analysis - April-May 2026.md and
Intelligence Briefs/Daily Macro Risk Report - 2026-08-17.md.

Inputs (all FRED, free, daily/weekly series):
  DGS2, DGS5, DGS10, DGS20, DGS30   — Treasury par yields
  SOFR, IORB                        — repo vs. administered rate
  WTREGEN                           — Treasury General Account balance
  WRESBAL                           — reserve balances
  RRPONTSYD                         — ON RRP facility balance

Inputs requiring a paid/authenticated feed (UnavailableProvider stubs, see
class docstrings below for exactly what's missing and why):
  Auction tail/stop-through, bid-to-cover, indirect/direct/dealer allocation
    -> attempted via Treasury's free fiscaldata.treasury.gov API; falls back
       to UnavailableProvider if the endpoint/shape changes or is unreachable.
  MOVE index                        — ICE BofA proprietary, no free feed.
  Treasury repo fails                — DTCC/NY Fed GCF data, not freely scraped.
  10Y/30Y swap spreads               — needs a swaps data vendor.

Signal states: NORMAL / WATCH / ELEVATED / CRITICAL / DATA_QUALITY_FAILURE
Confidence: CONFIRMED (FRED direct pulls) down to SPECULATIVE (any
UnavailableProvider-backed metric is never used to justify CRITICAL alone).

Falsifier (per spec): if long yields rise while auction demand remains
strong, dealer absorption is normal, repo is orderly, and swap spreads do not
indicate stress, classify the move as NORMAL repricing, not impaired function.

Run: python3 module1_treasury_plumbing.py [--json]
Schedule: add to state_vector_daily.sh alongside the other daily pulls (see
MODULES_README.md "Wiring into state_vector_daily.sh").
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from datetime import date, timedelta
from typing import Optional

import requests

from godseye_modules_common import (
    ConfidenceTier, ModuleResult, Observable, ProviderInterface,
    ProviderResult, SignalState, UnavailableProvider, load_config, now_iso,
    rolling_zscore, threshold_signal_state, supabase_upsert, is_stale,
)

FRED_BASE = "https://fred.stlouisfed.org/graph/fredgraph.csv?id="
FISCAL_AUCTIONS = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/od/auctions_query"


class FREDProvider(ProviderInterface):
    name = "FRED"
    requires = "free (no key required for graph CSV endpoint)"

    def fetch(self, series_id: str, lookback_days: int = 30) -> ProviderResult:
        try:
            r = requests.get(f"{FRED_BASE}{series_id}", timeout=15)
            r.raise_for_status()
            rows = list(csv.reader(io.StringIO(r.text)))
            data = [(row[0], row[1]) for row in rows[1:] if len(row) == 2 and row[1] not in (".", "")]
            if not data:
                return ProviderResult(None, None, "FRED:" + series_id, None, ConfidenceTier.SPECULATIVE,
                                       is_fallback=True, note="no non-missing observations returned")
            obs_date, val = data[-1]
            return ProviderResult(float(val), None, "FRED:" + series_id, obs_date, ConfidenceTier.CONFIRMED)
        except Exception as e:  # noqa: BLE001
            return ProviderResult(None, None, "FRED:" + series_id, None, ConfidenceTier.SPECULATIVE,
                                   is_fallback=True, note=f"fetch failed: {e}")

    def series(self, series_id: str) -> list:
        """Full series as [(date_str, float)], for z-score history. Empty list on failure."""
        try:
            r = requests.get(f"{FRED_BASE}{series_id}", timeout=15)
            r.raise_for_status()
            rows = list(csv.reader(io.StringIO(r.text)))
            out = []
            for row in rows[1:]:
                if len(row) == 2 and row[1] not in (".", ""):
                    out.append((row[0], float(row[1])))
            return out
        except Exception:
            return []


class TreasuryAuctionProvider(ProviderInterface):
    """Attempts the free Treasury fiscaldata API for the most recent 30Y and
    10Y auction (bid-to-cover, high yield). This IS a free government API, so
    it gets a real attempt rather than an automatic UnavailableProvider — but
    tail/stop-through requires comparing against the when-issued yield at
    auction time, which this endpoint does not carry, so `tail_bps` is always
    None here pending a WI-yield source; documented, not fabricated."""
    name = "Treasury fiscaldata auctions_query"
    requires = "free (public API, no key), shape may change without notice"

    def fetch(self, security_term: str = "30-Year") -> ProviderResult:
        try:
            params = {
                "filter": f"security_term:eq:{security_term},security_type:eq:Note" if "Year" in security_term and int(security_term.split('-')[0]) <= 10 else f"security_term:eq:{security_term},security_type:eq:Bond",
                "sort": "-auction_date",
                "page[size]": "1",
                "fields": "auction_date,security_term,high_yield,bid_to_cover_ratio",
            }
            r = requests.get(FISCAL_AUCTIONS, params=params, timeout=15)
            r.raise_for_status()
            data = r.json().get("data", [])
            if not data:
                return ProviderResult(None, None, self.name, None, ConfidenceTier.SPECULATIVE,
                                       is_fallback=True, note=f"no rows for security_term={security_term}")
            row = data[0]
            btc = row.get("bid_to_cover_ratio")
            return ProviderResult(
                float(btc) if btc not in (None, "") else None,
                "ratio", self.name, row.get("auction_date"), ConfidenceTier.CONFIRMED,
                note=f"high_yield={row.get('high_yield')} (WI-yield / tail not available from this endpoint)",
            )
        except Exception as e:  # noqa: BLE001
            return ProviderResult(None, None, self.name, None, ConfidenceTier.SPECULATIVE,
                                   is_fallback=True, note=f"fetch failed: {e}")


def compute(as_of: Optional[date] = None) -> ModuleResult:
    cfg = load_config("module1_treasury_plumbing")
    fred = FREDProvider()
    obs: list[Observable] = []
    metrics: dict = {}
    dq_notes: list = []
    missing: list = []
    freshness: dict = {}

    yields = {}
    for tenor, series_id in [("2y", "DGS2"), ("5y", "DGS5"), ("10y", "DGS10"),
                              ("20y", "DGS20"), ("30y", "DGS30")]:
        res = fred.fetch(series_id)
        yields[tenor] = res.value
        obs.append(Observable(f"ust_{tenor}", res.value, "%", res.source, res.obs_date,
                               res.confidence, is_unknown=res.is_unknown, note=res.note))
        if res.is_unknown:
            missing.append(f"ust_{tenor}")
        freshness[series_id] = res.obs_date

    def spread(a, b):
        if yields.get(a) is None or yields.get(b) is None:
            return None
        return round((yields[a] - yields[b]) * 100, 1)  # bps

    metrics["curve_2s10s_bps"] = spread("10y", "2y")
    metrics["curve_5s30s_bps"] = spread("30y", "5y")
    metrics["curve_10s30s_bps"] = spread("30y", "10y")
    metrics["curve_2s30s_bps"] = spread("30y", "2y")

    for name, series_id in [("sofr", "SOFR"), ("iorb", "IORB"), ("tga_balance", "WTREGEN"),
                             ("reserve_balances", "WRESBAL"), ("on_rrp_balance", "RRPONTSYD")]:
        res = fred.fetch(series_id)
        obs.append(Observable(name, res.value, None, res.source, res.obs_date,
                               res.confidence, is_unknown=res.is_unknown, note=res.note))
        metrics[name] = res.value
        if res.is_unknown:
            missing.append(name)
        freshness[series_id] = res.obs_date

    if metrics.get("sofr") is not None and metrics.get("iorb") is not None:
        metrics["sofr_minus_iorb_bps"] = round((metrics["sofr"] - metrics["iorb"]) * 100, 1)
    else:
        metrics["sofr_minus_iorb_bps"] = None

    # Auction internals — real attempt via free Treasury API
    auc = TreasuryAuctionProvider()
    for term in ["30-Year", "10-Year"]:
        res = auc.fetch(term)
        key = term.lower().replace("-", "_")
        obs.append(Observable(f"bid_to_cover_{key}", res.value, res.unit, res.source, res.obs_date,
                               res.confidence, is_unknown=res.is_unknown, note=res.note))
        metrics[f"bid_to_cover_{key}"] = res.value
        if res.is_unknown:
            missing.append(f"bid_to_cover_{key}")

    # Documented-unavailable: MOVE index, repo fails, swap spreads, auction tail,
    # indirect/direct/primary-dealer allocation breakdown.
    for stub_name, reason, requires in [
        ("move_index", "no free MOVE feed; ICE BofA proprietary index", "paid (ICE/Bloomberg terminal)"),
        ("repo_fails", "no free repo-fails series; DTCC/NY Fed GCF data not publicly scraped here", "paid/institutional feed"),
        ("swap_spread_10y", "no free swaps data source wired", "paid (swaps data vendor)"),
        ("swap_spread_30y", "no free swaps data source wired", "paid (swaps data vendor)"),
        ("auction_tail_30y_bps", "fiscaldata API has no when-issued yield to diff against", "needs WI-yield source"),
        ("indirect_bidder_pct", "fiscaldata auctions_query does not expose bidder-class breakdown in this deployment's query", "needs TreasuryDirect auction detail parser"),
        ("primary_dealer_pct", "same as indirect_bidder_pct", "needs TreasuryDirect auction detail parser"),
    ]:
        stub = UnavailableProvider(stub_name, reason, requires)
        res = stub.fetch()
        obs.append(Observable(stub_name, None, None, res.source, None, res.confidence,
                               is_unknown=True, note=res.note))
        missing.append(stub_name)
        dq_notes.append(f"{stub_name}: {res.note}")

    # ── Signal logic ─────────────────────────────────────────────────────────
    breaches = {"watch": False, "elevated": False, "critical": False}

    btc_30y = metrics.get("bid_to_cover_30_year")
    if btc_30y is not None:
        series = fred.series("DGS30")  # placeholder history source; real bid-to-cover
        # history isn't in a single FRED series, so we only have a point value —
        # documented limitation, not a fabricated trailing average.
        dq_notes.append("bid_to_cover_30_year rolling-average z-score not computed: "
                         "no free source for auction-level BTC history wired yet; only latest point available.")

    rising_long_yields = (yields.get("30y") is not None and yields.get("10y") is not None)
    auction_data_present = btc_30y is not None
    repo_orderly = metrics.get("sofr_minus_iorb_bps") is not None and abs(metrics["sofr_minus_iorb_bps"]) < cfg["sofr_iorb_watch_bps"] if "sofr_minus_iorb_bps" in metrics and metrics["sofr_minus_iorb_bps"] is not None else None

    # Given the genuinely-missing auction-tail/dealer-allocation/MOVE/swap-spread
    # inputs, this module can only respond to WATCH/ELEVATED thresholds it CAN
    # evaluate; CRITICAL per the spec requires auction weakness + repo stress +
    # elevated fails + rising long yields TOGETHER, and fails data is structurally
    # unavailable here — so CRITICAL can never fire from this deployment alone.
    # That is itself the falsifier-relevant finding, not a bug: don't let a
    # partial module claim more certainty than its inputs support.
    if repo_orderly is False:
        breaches["watch"] = True
    if len(missing) >= 5:
        dq_notes.append(
            f"{len(missing)} of {len(missing) + len([o for o in obs if not o.is_unknown])} tracked "
            "observables are unknown in this deployment — treat any NORMAL/WATCH read as "
            "low-confidence, not a clean bill of health."
        )

    signal_state = threshold_signal_state(breaches)
    if len([o for o in obs if not o.is_unknown]) < 6:
        signal_state = SignalState.DATA_QUALITY_FAILURE
        dq_notes.append("Fewer than 6 live observables resolved — insufficient data to classify plumbing state at all.")

    falsifiers = [
        "If long yields rise while auction demand remains strong, dealer absorption is "
        "normal, repo is orderly, and swap spreads do not indicate stress, this is NORMAL "
        "repricing, not impaired function.",
        "Swap-spread and dealer-allocation inputs are structurally unavailable in this "
        "deployment (see data_quality_notes) — a CRITICAL read can never be triggered by "
        "this module alone until those providers are wired; treat any escalation above "
        "WATCH as provisional.",
    ]

    result = ModuleResult(
        module_id="M1_treasury_plumbing",
        purpose="Distinguish ordinary long-end repricing from impaired Treasury market function.",
        computed_at=now_iso(),
        signal_state=signal_state,
        confidence=ConfidenceTier.INFERRED if missing else ConfidenceTier.CONFIRMED,
        metrics=metrics,
        observables=obs,
        falsifiers=falsifiers,
        data_quality_notes=dq_notes,
        missing_data=missing,
        source_freshness=freshness,
    )
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--write-supabase", action="store_true")
    args = ap.parse_args()

    result = compute()
    if args.json:
        print(result.to_json())
    else:
        print(f"M1 Treasury Plumbing — {result.signal_state.value} ({result.confidence.value})", file=sys.stderr)
        for k, v in result.metrics.items():
            print(f"  {k}: {v}", file=sys.stderr)
        if result.data_quality_notes:
            print("  Data quality notes:", file=sys.stderr)
            for n in result.data_quality_notes:
                print(f"    - {n}", file=sys.stderr)

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
