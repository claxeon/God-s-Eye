#!/usr/bin/env python3
"""
God's Eye — Module 5: Fertilizer Procurement Clock
======================================================
Purpose: treat fertilizer risk as a SEASONAL PROCUREMENT AND FARM-APPLICATION
problem, not simply a commodity-price problem. Per the spec: do not infer a
crop-yield outcome from fertilizer prices alone — yield risk requires
evidence of procurement failures, delivery delays, reduced application,
reduced acreage, or missed planting windows.

Free, real input wired:
  Henry Hub natural gas (EIA RNGWHHD) — the dominant marginal cost driver for
  ammonia/urea production; already pulled elsewhere in the codebase
  (state_vector_compute.py's L5 henry_hub component) — REUSED here via the
  same EIA seriesid client pattern module4 validated, not duplicated logic.

Structurally unavailable (all need a paid commodity-data vendor — Argus,
Fertecon, CRU, or a dedicated agricultural-data subscription — or manual
maintenance from primary-source tender announcements, which this deployment
does not currently ingest):
  Urea, ammonia, DAP, MAP, potash/MOP, sulfur prices; plant operating status
  by country; India/Brazil/Bangladesh/Pakistan tender volume, clearing price,
  delivery date, subscription rate; China/Russia/Belarus export-restriction
  status; farm-credit conditions; application-rate surveys; planted-acreage
  surveys.

What this module CAN compute without a live feed: the planting-window
urgency score, which is a calendar fact (when key nitrogen-intensive crops
need fertilizer applied in the highest-exposure countries), not a live
observable. This is domain reference data, versioned in config, same status
as e.g. KEY_LEVELS in yen_mechanics.py or BASIS_BAND in carry_mechanics.py —
not fabricated, but also not live.

IMPORTANT (per spec, enforced structurally): this module NEVER emits a
crop-yield or production-outcome claim. Its highest possible signal_state
from price/gas data alone is WATCH; ELEVATED/CRITICAL require at least one
non-price observable (tender failure, plant outage, application-rate drop),
none of which are wired to a live source here — so in this deployment the
module is structurally capped below ELEVATED, and that cap is itself
reported, not hidden.

Falsifier (per spec): if major tenders clear near normal volumes and
delivery schedules, plants restart, export restrictions ease, and
application surveys are stable, classify fertilizer stress as a price shock,
not an agricultural-production risk.

Run: python3 module5_fertilizer_clock.py [--json]
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime

from godseye_modules_common import (
    ConfidenceTier, ModuleResult, Observable, SignalState, UnavailableProvider,
    load_config, now_iso, supabase_upsert,
)

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from module4_refined_products import EIASeriesProvider  # reuse EIA client, not a new one

# Reference data, not a live feed — see module docstring. Month = when
# nitrogen application is most time-critical for that country/crop pairing.
# Sourced from FAO crop calendars (public, static reference, not scraped live).
PLANTING_WINDOWS = {
    "India (rabi wheat, N top-dress)":      {"month": 12, "day": 15},
    "Bangladesh (boro rice, N top-dress)":   {"month": 1,  "day": 15},
    "Pakistan (rabi wheat)":                 {"month": 11, "day": 15},
    "Brazil (safrinha corn, N application)": {"month": 2,  "day": 15},
    "East Africa (long-rains maize)":        {"month": 3,  "day": 15},
    "Southeast Asia (main wet-season rice)": {"month": 6,  "day": 15},
}


def _next_occurrence(month: int, day: int, as_of: date) -> date:
    candidate = date(as_of.year, month, day)
    if candidate < as_of:
        candidate = date(as_of.year + 1, month, day)
    return candidate


def planting_window_urgency(as_of: date, cfg: dict) -> dict:
    out = {}
    for label, md in PLANTING_WINDOWS.items():
        nxt = _next_occurrence(md["month"], md["day"], as_of)
        days_out = (nxt - as_of).days
        if days_out <= cfg["planting_window_urgency_days_critical"]:
            urgency = SignalState.CRITICAL
        elif days_out <= cfg["planting_window_urgency_days_elevated"]:
            urgency = SignalState.ELEVATED
        else:
            urgency = SignalState.WATCH if days_out <= 90 else SignalState.NORMAL
        out[label] = {"next_window": nxt.isoformat(), "days_out": days_out, "urgency": urgency.value}
    return out


def compute(as_of: date = None) -> ModuleResult:
    as_of = as_of or date.today()
    cfg = load_config("module5_fertilizer_clock")
    eia = EIASeriesProvider()
    obs = []
    metrics = {}
    missing = []
    dq_notes = []

    hh = eia.fetch("NG.RNGWHHD.D")
    obs.append(Observable("henry_hub_usd_mmbtu", hh.value, "$/MMBtu", hh.source, hh.obs_date,
                           hh.confidence, is_unknown=hh.is_unknown, note=hh.note))
    metrics["henry_hub_usd_mmbtu"] = hh.value
    if hh.is_unknown:
        missing.append("henry_hub")
        dq_notes.append(f"Henry Hub fetch failed: {hh.note}")

    windows = planting_window_urgency(as_of, cfg)
    metrics["planting_windows"] = windows
    most_urgent = min(windows.items(), key=lambda kv: kv[1]["days_out"])
    metrics["most_urgent_window"] = {"label": most_urgent[0], **most_urgent[1]}

    for stub_name, reason in [
        ("urea_price", "needs Argus/Fertecon/CRU subscription, no free feed"),
        ("ammonia_price", "same"),
        ("dap_price", "same"),
        ("map_price", "same"),
        ("potash_mop_price", "same"),
        ("sulfur_price", "same"),
        ("gulf_plant_operating_status", "needs manual maintenance from primary-source announcements (QAFCO/SABIC/OQ/ADNOC/etc.), not currently ingested as structured data"),
        ("india_tender_data", "needs a dedicated agri-tender data source (e.g. IFFCO/STC/MMTC tender bulletins), not scraped here"),
        ("brazil_import_data", "needs Brazilian customs/ANDA data, not wired"),
        ("bangladesh_pakistan_procurement", "needs national procurement bulletins, not wired"),
        ("china_russia_export_restrictions", "needs customs/MOFCOM bulletin tracking, not wired"),
        ("farm_credit_conditions", "needs a dedicated ag-credit data source"),
        ("application_rate_surveys", "needs USDA/FAO survey data, published with long lags and not API-accessible here"),
        ("planted_acreage_surveys", "same"),
        ("fertilizer_to_crop_price_ratio", "requires urea_price (unavailable) and a crop price index — cannot compute without the former"),
    ]:
        stub = UnavailableProvider(stub_name, reason)
        res = stub.fetch()
        obs.append(Observable(stub_name, None, None, res.source, None, res.confidence, is_unknown=True, note=res.note))
        missing.append(stub_name)
        dq_notes.append(f"{stub_name}: {res.note}")

    # ── Signal state — structurally capped, see module docstring ───────────
    price_pressure_watch = hh.value is not None and hh.value > 4.0  # elevated gas cost pressures ammonia margins
    any_planting_critical = any(w["urgency"] == SignalState.CRITICAL.value for w in windows.values())

    if price_pressure_watch or any_planting_critical:
        signal_state = SignalState.WATCH
    else:
        signal_state = SignalState.NORMAL

    dq_notes.append(
        "This module is structurally capped at WATCH: ELEVATED/CRITICAL require at least one "
        "non-price observable (tender failure, plant outage, application-rate drop) per the "
        "spec's own falsifier design, and none of those are wired to a live source in this "
        "deployment — see missing_data. Do NOT read a WATCH/NORMAL state here as 'fertilizer "
        "risk is low'; it means 'this module cannot currently see the procurement-failure "
        "evidence that would justify escalating past WATCH', which is a data-quality statement, "
        "not a risk statement. Cross-reference Intelligence Briefs/Kinetic & Financial Update - "
        "2026-08-18.md (JPMorgan fertilizer report) for the qualitative picture this module "
        "cannot yet quantify."
    )

    falsifiers = [
        "If major tenders clear near normal volumes and delivery schedules, plants restart, "
        "export restrictions ease, and application surveys are stable, classify fertilizer "
        "stress as a price shock, not an agricultural-production risk.",
        "This module never infers a crop-yield outcome from price/gas data alone — that "
        "requires procurement/application evidence none of which is currently wired.",
    ]

    return ModuleResult(
        module_id="M5_fertilizer_clock",
        purpose="Treat fertilizer risk as a seasonal procurement/application problem, not a "
                "price problem — structurally refuses to escalate past WATCH on price alone.",
        computed_at=now_iso(),
        signal_state=signal_state,
        confidence=ConfidenceTier.SPECULATIVE,
        metrics=metrics,
        observables=obs,
        falsifiers=falsifiers,
        data_quality_notes=dq_notes,
        missing_data=missing,
        source_freshness={"NG.RNGWHHD.D": hh.obs_date},
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
        print(f"M5 Fertilizer Clock — {result.signal_state.value} ({result.confidence.value})", file=sys.stderr)
        print(f"  Henry Hub: {result.metrics.get('henry_hub_usd_mmbtu')}", file=sys.stderr)
        print(f"  Most urgent planting window: {result.metrics.get('most_urgent_window')}", file=sys.stderr)

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
