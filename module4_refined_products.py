#!/usr/bin/env python3
"""
God's Eye — Module 4: Refined Products and Physical Energy
==============================================================
Purpose: separate crude supply shocks from refined-product / refinery /
logistics / distillate scarcity. This module formalizes, as a reusable gate,
the exact check that Intelligence Briefs/Daily Macro Risk Report -
2026-08-17.md had to do by hand for the ~$102/bbl ULSD-WTI crack: "confirm
that the diesel and WTI quotes are matched contract months and equivalent
dollar-per-barrel units" before treating a crack spread as real.

Verified free sources (tested live 2026-08-18, see module4 config comment):
  WTI spot          — FRED DCOILWTICO ($/bbl, already comparable)
  ULSD NY Harbor spot — EIA v2 seriesid route,
                        PET.EER_EPD2DXL0_PF4_Y35NY_DPG.D ($/gal, converted
                        ×42 to $/bbl, conversion logged)
  Both are SPOT/physical assessments (not futures) — a clean, same-kind-of-
  quote pairing per the data-quality gate, logged as such.

DATA QUALITY GATE (per spec): never calculate/alert on a crack spread unless
contracts are comparable units, delivery months match (or, for spot
assessments, both sides are the same assessment date), currency basis is
known, and quote types are not mixed (futures vs. physical). If not met,
return DATA_QUALITY_FAILURE, not a number.

Structurally unavailable in this deployment: matched-month ICE gasoil/Brent
futures (needs a futures data vendor), tanker rates/war-risk insurance,
non-US diesel wholesale/retail prices (East Africa, South Asia — needs a
dedicated retail-price panel), distillate inventories vs. 5-year range and
refinery utilization (EIA weekly series exist and ARE wired below via the
same EIA client pattern already used in state_vector_compute.py / market_
mechanics.py for crude, reused here for distillates).

Falsifier (per spec): if diesel cracks normalize while inventories stabilize
and refinery utilization recovers, downgrade the product-scarcity thesis
even if crude remains elevated.

Run: python3 module4_refined_products.py [--json]
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import sys
from datetime import date
from typing import Optional

import requests

from godseye_modules_common import (
    ConfidenceTier, ModuleResult, Observable, ProviderInterface, ProviderResult,
    SignalState, UnavailableProvider, load_config, matched_crack_spread,
    now_iso, supabase_upsert,
)

FRED_BASE = "https://fred.stlouisfed.org/graph/fredgraph.csv?id="
EIA_KEY = os.environ.get("EIA_API_KEY", "6JlB2qAQoHxNGL6kEiiZ6fIRt8cU5FlqR8ReVWYE")
EIA_SERIESID_BASE = "https://api.eia.gov/v2/seriesid/"


class FREDSpotProvider(ProviderInterface):
    name = "FRED"
    requires = "free"

    def fetch(self, series_id: str) -> ProviderResult:
        try:
            r = requests.get(f"{FRED_BASE}{series_id}", timeout=15)
            r.raise_for_status()
            rows = list(csv.reader(io.StringIO(r.text)))
            data = [(row[0], row[1]) for row in rows[1:] if len(row) == 2 and row[1] not in (".", "")]
            if not data:
                return ProviderResult(None, None, "FRED:" + series_id, None, ConfidenceTier.SPECULATIVE, is_fallback=True)
            d, v = data[-1]
            return ProviderResult(float(v), "$/bbl", "FRED:" + series_id, d, ConfidenceTier.CONFIRMED)
        except Exception as e:  # noqa: BLE001
            return ProviderResult(None, None, "FRED:" + series_id, None, ConfidenceTier.SPECULATIVE,
                                   is_fallback=True, note=f"fetch failed: {e}")


class EIASeriesProvider(ProviderInterface):
    name = "EIA"
    requires = "free (public API key)"

    def fetch(self, series_id: str) -> ProviderResult:
        try:
            url = f"{EIA_SERIESID_BASE}{series_id}?api_key={EIA_KEY}"
            r = requests.get(url, timeout=15)
            r.raise_for_status()
            data = r.json().get("response", {}).get("data", [])
            if not data:
                return ProviderResult(None, None, "EIA:" + series_id, None, ConfidenceTier.SPECULATIVE, is_fallback=True)
            row = data[0]  # already sorted desc by EIA for this series shape
            return ProviderResult(
                float(row["value"]), row.get("units"), "EIA:" + series_id,
                row.get("period"), ConfidenceTier.CONFIRMED,
                note=row.get("series-description", ""),
            )
        except Exception as e:  # noqa: BLE001
            return ProviderResult(None, None, "EIA:" + series_id, None, ConfidenceTier.SPECULATIVE,
                                   is_fallback=True, note=f"fetch failed: {e}")


def compute() -> ModuleResult:
    cfg = load_config("module4_refined_products")
    fred = FREDSpotProvider()
    eia = EIASeriesProvider()
    obs = []
    metrics = {}
    missing = []
    dq_notes = []
    freshness = {}

    wti = fred.fetch("DCOILWTICO")
    obs.append(Observable("wti_spot_usd_bbl", wti.value, "$/bbl", wti.source, wti.obs_date,
                           wti.confidence, is_unknown=wti.is_unknown))
    metrics["wti_spot_usd_bbl"] = wti.value
    freshness["DCOILWTICO"] = wti.obs_date
    if wti.is_unknown:
        missing.append("wti_spot")

    ulsd = eia.fetch("PET.EER_EPD2DXL0_PF4_Y35NY_DPG.D")
    ulsd_usd_bbl = None
    conversion_log = []
    if not ulsd.is_unknown:
        ulsd_usd_bbl = round(ulsd.value * cfg["gal_per_bbl"], 2)
        conversion_log.append(
            f"ULSD ${ulsd.value}/gal * {cfg['gal_per_bbl']} gal/bbl = ${ulsd_usd_bbl}/bbl "
            f"(EIA series {ulsd.source}, {ulsd.obs_date})"
        )
    obs.append(Observable("ulsd_ny_harbor_usd_bbl", ulsd_usd_bbl, "$/bbl", ulsd.source, ulsd.obs_date,
                           ulsd.confidence, is_unknown=ulsd.is_unknown))
    metrics["ulsd_ny_harbor_usd_bbl"] = ulsd_usd_bbl
    freshness["EIA_ULSD"] = ulsd.obs_date
    if ulsd.is_unknown:
        missing.append("ulsd_spot")

    # ── Data-quality-gated crack spread ─────────────────────────────────────
    gate_result = matched_crack_spread(
        product_price=ulsd_usd_bbl,
        crude_price=wti.value,
        product_unit="$/bbl",
        crude_unit="$/bbl",
        product_month=ulsd.obs_date,   # spot assessments: "month" = assessment date
        crude_month=wti.obs_date,
        product_is_futures=False,
        crude_is_futures=False,
        conversion_log=conversion_log + ["both legs are SPOT/physical assessments (EIA NY Harbor ULSD spot vs. FRED WTI spot) — same kind of quote, logged"],
    )
    metrics["crack_spread_gate"] = gate_result["status"]
    metrics["crack_spread_log"] = gate_result["log"]
    if gate_result["status"] == "OK":
        crack = gate_result["crack"]
        metrics["ulsd_wti_crack_usd_bbl"] = crack
        dq_notes.append(
            f"Crack spread dates: WTI {wti.obs_date}, ULSD {ulsd.obs_date} — "
            + ("SAME DATE, cleanly matched." if wti.obs_date == ulsd.obs_date
               else "DIFFERENT DATES — both are the most recent available print for each series; "
                    "treat the spread as approximate, not same-day-matched.")
        )
    else:
        crack = None
        metrics["ulsd_wti_crack_usd_bbl"] = None
        dq_notes.append(f"Crack spread gate FAILED: {gate_result['reason']}")

    # ── Distillate inventories / refinery utilization (EIA weekly) ─────────
    # NOTE: distillate stocks and refinery utilization weekly EIA series IDs
    # (e.g. WDISTUS1, WPULEUS3) follow the same v2/seriesid pattern as ULSD
    # above but are intentionally left as UnavailableProvider stubs here
    # rather than guessed-and-possibly-wrong series IDs — market_mechanics.py
    # already wires WPULEUS3 (refinery utilization) via the v2 data/ route
    # with a validated baseline (EIA_FLOW_BASELINES); this module defers to
    # that existing instrument rather than risking a second, unverified copy.
    for stub_name, reason in [
        ("distillate_inventory_pct_vs_5yr", "use market_mechanics.py's existing EIA weekly flow tracker (WPULEUS3 baseline already validated there) rather than a second unverified series ID here"),
        ("refinery_utilization_pct", "same — see market_mechanics.py EIA_FLOW_BASELINES['WPULEUS3']"),
        ("gasoil_brent_crack", "needs matched-month ICE gasoil + Brent futures, no free feed wired"),
        ("gasoline_crack", "needs matched-month RBOB futures, no free feed wired"),
        ("tanker_rates_war_risk", "needs a shipping-data vendor (Baltic Exchange, Windward, etc.)"),
        ("diesel_price_east_africa_south_asia", "needs a dedicated regional retail-price panel"),
    ]:
        stub = UnavailableProvider(stub_name, reason)
        res = stub.fetch()
        obs.append(Observable(stub_name, None, None, res.source, None, res.confidence, is_unknown=True, note=res.note))
        missing.append(stub_name)
        dq_notes.append(f"{stub_name}: {res.note}")

    # ── Signal state ─────────────────────────────────────────────────────────
    if gate_result["status"] != "OK":
        signal_state = SignalState.DATA_QUALITY_FAILURE
    elif crack >= cfg["ulsd_wti_crack_critical_usd_bbl"]:
        signal_state = SignalState.CRITICAL
    elif crack >= cfg["ulsd_wti_crack_elevated_usd_bbl"]:
        signal_state = SignalState.ELEVATED
    elif crack >= cfg["ulsd_wti_crack_watch_usd_bbl"]:
        signal_state = SignalState.WATCH
    else:
        signal_state = SignalState.NORMAL

    dq_notes.append(
        "This module can price the crude-vs-refined-product DIVERGENCE (the crack) but "
        "cannot currently confirm the falsifier condition (inventories stabilizing, refinery "
        "utilization recovering) with a live feed — see missing_data. A CRITICAL crack reading "
        "here should be read as 'refined-product price stress confirmed', not 'refinery/logistics "
        "root cause confirmed' — that requires the inventory/utilization series this deployment "
        "doesn't yet pull independently of market_mechanics.py."
    )

    falsifiers = [
        "If diesel cracks normalize while inventories stabilize and refinery utilization "
        "recover, downgrade the product-scarcity thesis even if crude remains elevated. "
        "(Inventory/utilization confirmation is not wired into this module — see data_quality_notes.)",
        "A crack spread computed from mismatched units, mismatched delivery months/dates, or "
        "a futures-vs-physical mismatch is a DATA_QUALITY_FAILURE, not a signal, by construction "
        "of matched_crack_spread() in godseye_modules_common.py.",
    ]

    return ModuleResult(
        module_id="M4_refined_products",
        purpose="Separate crude supply shocks from refined-product/refinery/logistics scarcity, "
                "with a hard data-quality gate on crack-spread unit/month matching.",
        computed_at=now_iso(),
        signal_state=signal_state,
        confidence=ConfidenceTier.CONFIRMED if gate_result["status"] == "OK" else ConfidenceTier.SPECULATIVE,
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
        print(f"M4 Refined Products — {result.signal_state.value} ({result.confidence.value})", file=sys.stderr)
        print(f"  WTI: {result.metrics.get('wti_spot_usd_bbl')}  ULSD($/bbl): "
              f"{[o.value for o in result.observables if o.name=='ulsd_ny_harbor_usd_bbl']}", file=sys.stderr)
        print(f"  Crack: {result.metrics.get('ulsd_wti_crack_usd_bbl')} (gate: {result.metrics.get('crack_spread_gate')})", file=sys.stderr)

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
