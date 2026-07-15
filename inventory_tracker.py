#!/usr/bin/env python3
"""
God's Eye — Weekly Inventory Tracker
=====================================
Pulls EIA petroleum inventory series, computes seasonal z-scores vs 5-year
baseline, and upserts to Supabase inventory_levels table.

Physical inventories are the upstream signal in the supply chain cascade:
  inventory draw → backwardation widens → spot reprices → inflation → macro

Series tracked:
  WCESTUS1   US crude oil stocks excl. SPR     (mmbbl)
  WCSSTCUS1  Cushing, OK crude stocks           (mmbbl) ← runoff threshold
  WGTSTUS1   US total gasoline stocks           (mmbbl)
  WDISTUS1   US distillate fuel oil stocks      (mmbbl)
  WTTSTUS1   US total petroleum stocks excl. SPR (mmbbl)

Run: python3 inventory_tracker.py
Schedule: daily via state_vector_daily.sh (EIA updates Wednesdays ~10:30 ET)
"""

import json
import math
import os
import subprocess
import sys
from datetime import date, timedelta
from typing import Optional

# ── Config ────────────────────────────────────────────────────────────────────
SUPA_URL = "https://snykuqyceqpplnzmyksp.supabase.co"
SUPA_KEY = "sb_publishable_TJg65x5w56CulOEdWFJNyQ_89loJtit"
EIA_KEY  = os.environ.get("EIA_API_KEY", "6JlB2qAQoHxNGL6kEiiZ6fIRt8cU5FlqR8ReVWYE")
EIA_BASE = "https://api.eia.gov/v2/petroleum/sum/sndw/data/"
TODAY    = date.today().isoformat()

# Series definitions: id → human name
# Note: Cushing uses long-form ID W_EPC0_SAX_YCUOK_MBBL (EIA v2 API naming)
SERIES = {
    "WCESTUS1":            "US Crude Oil Stocks excl. SPR",
    "W_EPC0_SAX_YCUOK_MBBL": "Cushing OK Crude Stocks",
    "WGTSTUS1":            "US Gasoline Stocks",
    "WDISTUS1":            "US Distillate Fuel Oil Stocks",
    "WTTSTUS1":            "US Total Petroleum Stocks excl. SPR",
}

# Critical thresholds that trigger runoff alerts
RUNOFF_THRESHOLDS = {
    "W_EPC0_SAX_YCUOK_MBBL": 25.0,  # mmbbl — below 25 = delivery mechanism stress (WTI runoff)
    "WCESTUS1":               380.0, # mmbbl — below 380 = 2022-era tightness
    "WDISTUS1":                90.0, # mmbbl — below 90 = heating oil / diesel stress
}


# ── EIA fetch ─────────────────────────────────────────────────────────────────

def eia_fetch_series(series_id: str, n_weeks: int = 270) -> list:
    """
    Fetch n_weeks of weekly EIA data for a petroleum series.
    Returns sorted list of {period: 'YYYY-MM-DD', value: float} dicts.
    EIA weekly petroleum series use date format YYYY-MM-DD.
    """
    url = (EIA_BASE
           + f"?frequency=weekly"
           + f"&data%5B0%5D=value"
           + f"&facets%5Bseries%5D%5B%5D={series_id}"
           + f"&sort%5B0%5D%5Bcolumn%5D=period"
           + f"&sort%5B0%5D%5Bdirection%5D=desc"
           + f"&length={n_weeks}"
           + f"&api_key={EIA_KEY}")
    r = subprocess.run(["curl", "-s", "--max-time", "30", "-g", url],
                       capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        print(f"  ⚠️  EIA curl failed for {series_id}: {r.stderr[:80]}", file=sys.stderr)
        return []
    try:
        raw = json.loads(r.stdout)
        rows = raw["response"]["data"]
        # Convert thousands of barrels → million barrels; filter nulls
        parsed = []
        for row in rows:
            if row.get("value") not in (None, "", "."):
                try:
                    parsed.append({
                        "period": row["period"],
                        "value":  round(float(row["value"]) / 1000.0, 3)  # kbbl → mmbbl
                    })
                except (ValueError, TypeError):
                    pass
        return sorted(parsed, key=lambda x: x["period"])
    except Exception as e:
        print(f"  ⚠️  EIA parse error for {series_id}: {e}", file=sys.stderr)
        return []


# ── Seasonal baseline ─────────────────────────────────────────────────────────

def compute_seasonal_baseline(rows: list) -> dict:
    """
    For each week-of-year (1–53), compute 5-year average and stddev
    from the prior 4 complete years of data (excludes current year).

    Returns dict: week_of_year → {avg, stddev, n}
    """
    from collections import defaultdict
    import datetime

    cutoff_year = date.today().year - 1  # exclude current year in baseline

    by_week = defaultdict(list)
    for row in rows:
        try:
            d = date.fromisoformat(row["period"])
        except ValueError:
            continue
        if d.year > cutoff_year:
            continue  # exclude current-year data from baseline
        # ISO week number
        wk = d.isocalendar()[1]
        by_week[wk].append(row["value"])

    baseline = {}
    for wk, vals in by_week.items():
        if len(vals) < 2:
            continue
        avg = sum(vals) / len(vals)
        variance = sum((v - avg) ** 2 for v in vals) / len(vals)
        stddev = math.sqrt(variance)
        baseline[wk] = {"avg": round(avg, 3), "stddev": round(stddev, 3), "n": len(vals)}
    return baseline


# ── Supabase upsert ───────────────────────────────────────────────────────────

def upsert_rows(rows: list) -> bool:
    """Batch upsert to inventory_levels via Supabase REST."""
    if not rows:
        return True
    r = subprocess.run(
        ["curl", "-s", "--max-time", "30", "-X", "POST",
         "-H", "Content-Type: application/json",
         "-H", f"apikey: {SUPA_KEY}",
         "-H", f"Authorization: Bearer {SUPA_KEY}",
         "-H", "Prefer: resolution=merge-duplicates,return=minimal",
         "-d", json.dumps(rows),
         SUPA_URL + "/rest/v1/inventory_levels?on_conflict=as_of_date,series_id"],
        capture_output=True, text=True
    )
    return r.returncode == 0


# ── Ripple dashboard print ────────────────────────────────────────────────────

def _bar(z: float, width: int = 20) -> str:
    """ASCII z-score bar. Center = 0. Left = below avg (stress). Right = surplus."""
    center = width // 2
    pos = int(round(center + z * 2))
    pos = max(0, min(width - 1, pos))
    bar = ["-"] * width
    bar[center] = "|"
    if pos != center:
        bar[pos] = "█"
    return "".join(bar)


def print_ripple_dashboard(results: list):
    """Print the supply-chain initial wave dashboard."""
    print("\n" + "═" * 72)
    print("  SUPPLY CHAIN INITIAL WAVE — Inventory Levels vs 5-Year Seasonal")
    print("═" * 72)
    print(f"  {'Series':<14} {'Value':>8}  {'Δwk':>7}  {'5yr avg':>8}  {'%dev':>7}  {'z':>5}  Signal")
    print("  " + "─" * 68)

    alerts = []
    for r in results:
        sid    = r["series_id"]
        name   = r["series_name"][:20]
        val    = r["value_mbbl"]
        delta  = r["delta_mbbl"]
        avg5   = r["avg_5yr_mbbl"]
        pct    = r["pct_vs_5yr"]
        z      = r["z_vs_5yr"]

        if val is None:
            print(f"  {sid:<14}  N/A")
            continue

        delta_str = f"{delta:+.1f}" if delta is not None else "  N/A"
        pct_str   = f"{pct:+.1f}%" if pct is not None else "  N/A"
        z_str     = f"{z:+.2f}"   if z   is not None else "  N/A"
        avg_str   = f"{avg5:.1f}"  if avg5 is not None else "  N/A"

        # Signal tag
        if z is not None:
            if z <= -2.0:
                signal = "🔴 CRITICAL"
                alerts.append((sid, val, z, "critical draw"))
            elif z <= -1.0:
                signal = "🟠 TIGHT"
                alerts.append((sid, val, z, "below seasonal"))
            elif z >= 1.5:
                signal = "🟢 SURPLUS"
            else:
                signal = "⚪ normal"
        else:
            signal = "   —"

        # Runoff threshold check
        thresh = RUNOFF_THRESHOLDS.get(sid)
        if thresh and val is not None and val < thresh:
            signal += f" ⚠️ RUNOFF<{thresh}"
            alerts.append((sid, val, z, f"below runoff threshold {thresh} mmbbl"))

        print(f"  {sid:<14} {val:>8.1f}  {delta_str:>7}  {avg_str:>8}  {pct_str:>7}  {z_str:>5}  {signal}")

    print()
    if alerts:
        print(f"  ⚠️  ALERT — {len(alerts)} inventory stress signal(s):")
        for sid, val, z, reason in alerts:
            print(f"     {sid}: {val:.1f} mmbbl — {reason} (z={z:+.2f})")
    else:
        print("  ✅  All inventory levels within normal seasonal range.")

    print()
    print("  Ripple cascade logic:")
    print("  [Step 1] Inventory draw ← you are here")
    print("  [Step 2] Backwardation widens  (brent_backwardation in CALIB)")
    print("  [Step 3] Spot reprices         (brent_spot in CALIB, FRED DCOILBRENTEU)")
    print("  [Step 4] Inflation pass-through (CPI energy, not yet modeled)")
    print("  [Step 5] Macro impact          (recession risk, L3/L9)")
    print("═" * 72)


# ── Main ──────────────────────────────────────────────────────────────────────

def run_inventory_tracker() -> list:
    """
    Fetch all series, compute seasonals, upsert to Supabase, return result dicts.
    """
    print(f"\n  God's Eye — Inventory Tracker  ({TODAY})")
    print("  " + "─" * 50)

    all_rows_to_upsert = []
    results = []

    for series_id, series_name in SERIES.items():
        print(f"  Fetching {series_id} ({series_name})...", end=" ", flush=True)
        rows = eia_fetch_series(series_id, n_weeks=300)  # ~5.75 years

        if not rows:
            print("FAILED")
            results.append({"series_id": series_id, "series_name": series_name,
                             "value_mbbl": None, "delta_mbbl": None,
                             "avg_5yr_mbbl": None, "pct_vs_5yr": None, "z_vs_5yr": None})
            continue

        # Latest and previous week
        latest   = rows[-1]
        prev     = rows[-2] if len(rows) >= 2 else None
        val      = latest["value"]
        prev_val = prev["value"] if prev else None
        delta    = round(val - prev_val, 3) if prev_val is not None else None

        # Seasonal baseline
        baseline = compute_seasonal_baseline(rows)
        latest_date = date.fromisoformat(latest["period"])
        wk = latest_date.isocalendar()[1]
        b  = baseline.get(wk)

        avg5   = b["avg"]    if b else None
        std5   = b["stddev"] if b else None
        pct    = round((val - avg5) / avg5 * 100, 2) if avg5 else None
        z      = round((val - avg5) / std5, 3)       if (avg5 and std5 and std5 > 0) else None

        print(f"{val:.1f} mmbbl  Δ{delta:+.1f}  z={z:+.2f}" if z is not None else f"{val:.1f} mmbbl")

        row = {
            "as_of_date":    latest["period"],
            "series_id":     series_id,
            "series_name":   series_name,
            "value_mbbl":    val,
            "prev_week_mbbl": prev_val,
            "delta_mbbl":    delta,
            "avg_5yr_mbbl":  avg5,
            "pct_vs_5yr":    pct,
            "z_vs_5yr":      z,
            "stddev_5yr":    round(std5, 3) if std5 else None,
            "source_tag":    "EIA",
        }
        all_rows_to_upsert.append(row)
        results.append({**row, "series_name": series_name})

    # Upsert to Supabase
    if all_rows_to_upsert:
        ok = upsert_rows(all_rows_to_upsert)
        print(f"\n  {'✓' if ok else '⚠️ FAILED'} Supabase upsert: {len(all_rows_to_upsert)} rows")

    # Print ripple dashboard
    print_ripple_dashboard(results)

    return results


if __name__ == "__main__":
    run_inventory_tracker()
