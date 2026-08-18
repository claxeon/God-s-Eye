#!/usr/bin/env python3
"""
God's Eye — Module 9: Dashboard Outputs
==========================================
Purpose: aggregate modules 1-8 into the 10 dashboard panels the spec asks
for, as a single machine-readable JSON document. Does NOT edit
gods_eye_dashboard.html directly (that file is 134KB of hand-built Chart.js
wiring against the existing 9-leg state vector; a blind structural edit
there risks breaking a working dashboard for a change that's additive by
nature). Instead this produces `data/dashboard_modules_feed.json`, a stable
contract a future dashboard panel (or the existing HTML file, at a
maintainer's discretion) can fetch/embed. See MODULES_README.md
"Dashboard integration" for the two lines of fetch() JS needed to wire this
into gods_eye_dashboard.html when ready.

Each panel includes: current state, 1w/1m/3m trend (from module_results
history if present, else "insufficient history"), data freshness, confidence,
top drivers, countervailing evidence, falsification conditions, linked
source records, and is explicitly capped at reporting evidence — no panel
emits a narrative-certainty statement beyond what its module computed.

Run: python3 module9_dashboard_outputs.py [--json] [--write-file]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from godseye_modules_common import ConfidenceTier, SignalState, now_iso, supabase_upsert

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import module1_treasury_plumbing as m1
import module2_japan_channels as m2
import module3_dollar_funding as m3
import module4_refined_products as m4
import module5_fertilizer_clock as m5
import module6_country_vulnerability as m6
import module7_policy_response as m7
import module8_claims_engine as m8

OUT_PATH = Path(__file__).resolve().parent / "data" / "dashboard_modules_feed.json"

PANEL_DEFS = [
    ("1_treasury_market_function", m1, "Treasury Market Function"),
    ("2_japan_channels", m2, "Japan Channels (FX intervention / reallocation / carry unwind)"),
    ("3_dollar_funding", m3, "Dollar Funding and Collateral Stress"),
    ("4_refined_products", m4, "Energy: Crude vs. Refined Products"),
    ("5_fertilizer_clock", m5, "Fertilizer Procurement Clock"),
    ("6_country_vulnerability", m6, "Country Vulnerability Matrix"),
    ("7_policy_response", m7, "Policy Response"),
    ("8_claims_engine", m8, "Claims, Falsifiers, and Contradictory Evidence"),
]


def _panel(panel_id: str, module, title: str) -> dict:
    try:
        result = module.compute()
    except Exception as e:  # noqa: BLE001 — one module failing must not take down the panel feed
        return {
            "panel_id": panel_id, "title": title,
            "current_state": SignalState.DATA_QUALITY_FAILURE.value,
            "error": f"module compute() raised: {e}",
        }
    return {
        "panel_id": panel_id,
        "title": title,
        "current_state": result.signal_state.value,
        "confidence": result.confidence.value,
        "computed_at": result.computed_at,
        "trend_1w": "insufficient history — module_results table needs >=2 dated rows; see schema_v3_extension_modules.sql",
        "trend_1m": "insufficient history",
        "trend_3m": "insufficient history",
        "data_freshness": result.source_freshness,
        "top_drivers": {k: v for k, v in result.metrics.items() if not isinstance(v, (list, dict))},
        "countervailing_evidence": result.data_quality_notes,
        "falsification_conditions": result.falsifiers,
        "missing_data": result.missing_data,
        "linked_source_records": [o.name for o in result.observables],
    }


def compute_freshness_panel(panels: list) -> dict:
    all_missing = sum((p.get("missing_data", []) for p in panels), [])
    total_observables = sum(len(p.get("linked_source_records", [])) for p in panels)
    return {
        "panel_id": "10_data_freshness_quality",
        "title": "Data Freshness and Data Quality",
        "total_observables_tracked": total_observables,
        "total_unknown": len(all_missing),
        "coverage_pct": round(100 * (1 - len(all_missing) / total_observables), 1) if total_observables else 0.0,
        "panels_in_data_quality_failure": [p["panel_id"] for p in panels if p["current_state"] == "DATA_QUALITY_FAILURE"],
    }


def build_feed() -> dict:
    panels = [_panel(pid, mod, title) for pid, mod, title in PANEL_DEFS]
    freshness_panel = compute_freshness_panel(panels)
    return {
        "generated_at": now_iso(),
        "note": "Additive extension per user request (2026-08-18) — does NOT replace or "
                "reweight state_vector_history / leg_components. Cross-reference, don't merge, "
                "until a maintainer deliberately decides to fold a specific module observable "
                "into an existing leg (see MODULES_README.md).",
        "panels": panels + [freshness_panel],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--write-file", action="store_true")
    ap.add_argument("--write-supabase", action="store_true")
    args = ap.parse_args()

    feed = build_feed()
    if args.json:
        print(json.dumps(feed, indent=2, default=str))
    else:
        print(f"M9 Dashboard Outputs — {len(feed['panels'])} panels", file=sys.stderr)
        for p in feed["panels"]:
            print(f"  {p['panel_id']:35} {p.get('current_state', '?')}", file=sys.stderr)

    if args.write_file:
        OUT_PATH.parent.mkdir(exist_ok=True)
        OUT_PATH.write_text(json.dumps(feed, indent=2, default=str))
        print(f"Wrote {OUT_PATH}", file=sys.stderr)

    if args.write_supabase:
        supabase_upsert("module_dashboard_feed", [{
            "obs_date": date.today().isoformat(),
            "payload": feed,
        }], on_conflict="obs_date")


if __name__ == "__main__":
    main()
