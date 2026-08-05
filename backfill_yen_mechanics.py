#!/usr/bin/env python3
"""
God's Eye — USD/JPY Mechanics Backfill
=========================================
One-time historical backfill of yen_mechanics_daily, reusing the exact
scoring/classification logic from yen_mechanics.py (analyze_key_levels,
classify_signal) so backfilled rows are computed identically to how the
daily cron would have computed them, day by day, had it been running
since the start date.

Window: 2025-11-04 (earliest CFTC JPY COT report in the war-relevant
period) through today. Existing rows (2026-06-30 onward) are recomputed
and overwritten via upsert so the whole series uses one consistent
methodology — no discontinuity between "live" and "backfilled" rows.

Run: python3 backfill_yen_mechanics.py
"""

import json
import os
import subprocess
import sys
import urllib.parse
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from yen_mechanics import (
    fred_series,
    analyze_key_levels,
    classify_signal,
    KEY_LEVELS,
    INTERVENTION_THRESHOLD_PCT,
)

SUPA_URL = "https://snykuqyceqpplnzmyksp.supabase.co"
SUPA_KEY = "sb_publishable_TJg65x5w56CulOEdWFJNyQ_89loJtit"
CFTC_JPY_URL = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"

START_DATE = date(2025, 11, 4)
END_DATE = date.today()


def curl_json(url: str, max_time: int = 30):
    r = subprocess.run(["curl", "-s", "--max-time", str(max_time), url],
                        capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return None


def fetch_cftc_jpy_history(start_date_str: str) -> list:
    """Ascending list of JPY COT reports from start_date_str to present."""
    where = (f"contract_market_name='JAPANESE YEN' AND futonly_or_combined='FutOnly'"
              f" AND report_date_as_yyyy_mm_dd >= '{start_date_str}T00:00:00'")
    url = (CFTC_JPY_URL
           + "?%24limit=200&%24order=report_date_as_yyyy_mm_dd%20ASC"
           + "&%24where=" + urllib.parse.quote(where))
    rows = curl_json(url)
    if not isinstance(rows, list):
        return []
    out = []
    for r in rows:
        def _i(k):
            return int(r.get(k) or 0)
        oi = _i("open_interest_all")
        nc_long = _i("noncomm_positions_long_all")
        nc_short = _i("noncomm_positions_short_all")
        nc_net = nc_long - nc_short
        out.append({
            "report_date": r.get("report_date_as_yyyy_mm_dd", "")[:10],
            "open_interest": oi,
            "nc_long": nc_long,
            "nc_short": nc_short,
            "nc_net": nc_net,
            "nc_pct_oi": round(nc_net / oi * 100, 2) if oi else None,
        })
    return sorted(out, key=lambda x: x["report_date"])


def latest_on_or_before(sorted_records: list, key: str, as_of: str):
    """Return the last record whose [key] <= as_of, or None."""
    best = None
    for rec in sorted_records:
        if rec[key] <= as_of:
            best = rec
        else:
            break
    return best


def upsert_batch(rows: list) -> bool:
    if not rows:
        return True
    ok_all = True
    for i in range(0, len(rows), 100):
        chunk = rows[i:i + 100]
        r = subprocess.run(
            ["curl", "-s", "--max-time", "60", "-X", "POST",
             "-H", "Content-Type: application/json",
             "-H", f"apikey: {SUPA_KEY}",
             "-H", f"Authorization: Bearer {SUPA_KEY}",
             "-H", "Prefer: resolution=merge-duplicates,return=minimal",
             "-d", json.dumps(chunk),
             SUPA_URL + "/rest/v1/yen_mechanics_daily?on_conflict=as_of_date"],
            capture_output=True, text=True
        )
        ok = (r.returncode == 0)
        ok_all = ok_all and ok
        print(f"    chunk {i//100 + 1}: {'✓' if ok else '⚠️ FAILED ' + r.stderr[:200]} ({len(chunk)} rows)")
    return ok_all


def main():
    print(f"\n  God's Eye — Yen Mechanics BACKFILL  ({START_DATE} → {END_DATE})")
    print("  " + "─" * 60)

    print("  Fetching USD/JPY daily history (FRED DEXJPUS, n=1500)...", end=" ", flush=True)
    usdjpy_rows = fred_series("DEXJPUS", n_rows=1500)
    print(f"{len(usdjpy_rows)} rows, {usdjpy_rows[0][0]} → {usdjpy_rows[-1][0]}")

    print("  Fetching US 10yr history (FRED DGS10, n=1500)...", end=" ", flush=True)
    us10_rows = fred_series("DGS10", n_rows=1500)
    print(f"{len(us10_rows)} rows")

    print("  Fetching JGB 10yr history (FRED IRLTLT01JPM156N, n=200 monthly)...", end=" ", flush=True)
    jgb10_rows = fred_series("IRLTLT01JPM156N", n_rows=200)
    print(f"{len(jgb10_rows)} rows")

    print("  Fetching CFTC JPY COT history (since 2025-06-01 for forward-fill margin)...", end=" ", flush=True)
    cot_history = fetch_cftc_jpy_history("2025-06-01")
    print(f"{len(cot_history)} weekly reports, {cot_history[0]['report_date']} → {cot_history[-1]['report_date']}" if cot_history else "FAILED")

    us10_by_date = {d: v for d, v in us10_rows}
    jgb10_sorted = [{"date": d, "value": v} for d, v in jgb10_rows]

    backfill_dates = [d for d, _ in usdjpy_rows
                      if START_DATE.isoformat() <= d <= END_DATE.isoformat()]
    print(f"\n  Backfilling {len(backfill_dates)} trading days...")

    rows_out = []
    last_us10 = None
    for idx, d in enumerate(backfill_dates):
        prefix = [(dt, v) for dt, v in usdjpy_rows if dt <= d]
        spot = prefix[-1][1]
        daily_change = None
        daily_pct = None
        if len(prefix) >= 2:
            prev = prefix[-2][1]
            daily_change = round(spot - prev, 3)
            daily_pct = round((spot - prev) / prev * 100, 3) if prev else None

        us10 = us10_by_date.get(d, last_us10)
        if us10 is not None:
            last_us10 = us10
        jgb10_rec = latest_on_or_before(jgb10_sorted, "date", d)
        jgb10 = jgb10_rec["value"] if jgb10_rec else None
        rate_diff = round(us10 - jgb10, 3) if (us10 is not None and jgb10 is not None) else None

        cot = latest_on_or_before(cot_history, "report_date", d)

        level_stats = analyze_key_levels(prefix)
        sig = classify_signal(spot, daily_pct, rate_diff,
                               cot["nc_pct_oi"] if cot else None, level_stats)

        lv160 = level_stats["levels"].get(160.0, {})
        lv161 = level_stats["levels"].get(161.0, {})
        lv162 = level_stats["levels"].get(162.0, {})

        row = {
            "as_of_date": d,
            "usdjpy_spot": spot,
            "usdjpy_daily_change": daily_change,
            "usdjpy_daily_change_pct": daily_pct,
            "above_160": (spot or 0) >= 160.0,
            "above_161": (spot or 0) >= 161.0,
            "above_162": (spot or 0) >= 162.0,
            "episodes_above_160": lv160.get("episode_count"),
            "episodes_above_161": lv161.get("episode_count"),
            "episodes_above_162": lv162.get("episode_count"),
            "current_episode_days": lv160.get("current_episode_days", 0),
            "max_sustained_above_160": lv160.get("max_sustained_days"),
            "intervention_flag": sig["today_intervention"],
            "intervention_magnitude": daily_pct if sig["today_intervention"] else None,
            "us_10yr_yield": us10,
            "jgb_10yr_yield": jgb10,
            "rate_differential": rate_diff,
            "cot_report_date": cot["report_date"] if cot else None,
            "jpy_nc_long": cot["nc_long"] if cot else None,
            "jpy_nc_short": cot["nc_short"] if cot else None,
            "jpy_nc_net": cot["nc_net"] if cot else None,
            "jpy_open_interest": cot["open_interest"] if cot else None,
            "jpy_nc_pct_oi": cot["nc_pct_oi"] if cot else None,
            "yen_signal": sig["yen_signal"],
            "dominant_pressure": sig["dominant_pressure"],
            "hypothesis_notes": sig.get("hypothesis_notes"),
        }
        rows_out.append(row)
        if (idx + 1) % 40 == 0 or idx == len(backfill_dates) - 1:
            print(f"    computed {idx+1}/{len(backfill_dates)} ({d})")

    print(f"\n  Upserting {len(rows_out)} rows to yen_mechanics_daily...")
    ok = upsert_batch(rows_out)
    print(f"\n  {'✓ DONE' if ok else '⚠️  Some chunks failed — see above'}")

    interventions = [r for r in rows_out if r["intervention_flag"]]
    print(f"\n  Intervention days detected in backfilled window: {len(interventions)}")
    for r in interventions:
        print(f"    {r['as_of_date']}  USD/JPY {r['usdjpy_spot']:.3f}  {r['usdjpy_daily_change_pct']:+.2f}%")


if __name__ == "__main__":
    main()
