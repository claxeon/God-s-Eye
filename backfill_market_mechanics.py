#!/usr/bin/env python3
"""
God's Eye — Market Mechanics Backfill
========================================
One-time historical backfill of market_mechanics_daily (currently 0 rows —
the daily cron for this table has apparently never successfully landed a
row, live or historical). Reuses the exact divergence/classification logic
from market_mechanics.py (compute_divergence) so backfilled rows match
what the live script would have produced.

Also backfills inventory_levels (the table market_mechanics.py depends on
for physical_tightness/divergence_score) using the same 5-year seasonal
z-score methodology as inventory_tracker.py, since inventory_levels only
had 5 rows/series (from 2026-06-19) before this ran — without it,
physical_tightness would be null for the whole pre-June window.

Window: 2025-11-04 through today, one row per Brent-FRED trading day.
CFTC WTI COT and EIA weekly flows/levels are forward-filled to their
latest value as of each day (report/period date <= as_of_date — dated by
the COT positions-as-of date, not the Friday publish date, per standard
COT-series convention).

Run: python3 backfill_market_mechanics.py
"""

import json
import math
import os
import subprocess
import sys
import urllib.parse
from collections import defaultdict
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from market_mechanics import compute_divergence, EIA_FLOW_BASELINES, BRENT_MU, BRENT_SIGMA, WTI_CONTRACT
from yen_mechanics import fred_series

SUPA_URL = "https://snykuqyceqpplnzmyksp.supabase.co"
SUPA_KEY = "sb_publishable_TJg65x5w56CulOEdWFJNyQ_89loJtit"
EIA_KEY = os.environ.get("EIA_API_KEY", "6JlB2qAQoHxNGL6kEiiZ6fIRt8cU5FlqR8ReVWYE")
EIA_BASE = "https://api.eia.gov/v2/petroleum/sum/sndw/data/"
CFTC_WTI_URL = "https://publicreporting.cftc.gov/resource/kh3c-gbw2.json"

START_DATE = date(2025, 11, 4)
END_DATE = date.today()

INVENTORY_SERIES = {
    "WCESTUS1":              "US Crude Oil Stocks excl. SPR",
    "W_EPC0_SAX_YCUOK_MBBL": "Cushing OK Crude Stocks",
    "WGTSTUS1":              "US Gasoline Stocks",
    "WDISTUS1":              "US Distillate Fuel Oil Stocks",
    "WTTSTUS1":              "US Total Petroleum Stocks excl. SPR",
}


def curl_json(url: str, max_time: int = 30):
    r = subprocess.run(["curl", "-s", "--max-time", str(max_time), "-g", url],
                        capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return None


def eia_series_history(series_id: str, n_weeks: int, scale: float = 1.0) -> list:
    """Ascending list of {period, value} for an EIA weekly series, value*scale."""
    url = (EIA_BASE
           + f"?frequency=weekly&data%5B0%5D=value"
           + f"&facets%5Bseries%5D%5B%5D={series_id}"
           + f"&sort%5B0%5D%5Bcolumn%5D=period&sort%5B0%5D%5Bdirection%5D=desc"
           + f"&length={n_weeks}&api_key={EIA_KEY}")
    payload = curl_json(url)
    if not payload:
        return []
    rows = payload.get("response", {}).get("data", [])
    out = []
    for r in rows:
        if r.get("value") not in (None, "", "."):
            try:
                out.append({"period": r["period"], "value": round(float(r["value"]) * scale, 4)})
            except (ValueError, TypeError):
                pass
    return sorted(out, key=lambda x: x["period"])


def fetch_cftc_wti_history(start_date_str: str) -> list:
    where = (f"contract_market_name='{WTI_CONTRACT}' AND futonly_or_combined='Combined'"
             f" AND report_date_as_yyyy_mm_dd >= '{start_date_str}T00:00:00'")
    url = (CFTC_WTI_URL
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
        mm_l, mm_s = _i("m_money_positions_long_all"), _i("m_money_positions_short_all")
        swap_l, swap_s = _i("swap_positions_long_all"), _i("swap__positions_short_all")
        prod_l, prod_s = _i("prod_merc_positions_long"), _i("prod_merc_positions_short")
        mm_net, swap_net, prod_net = mm_l - mm_s, swap_l - swap_s, prod_l - prod_s
        out.append({
            "report_date": r.get("report_date_as_yyyy_mm_dd", "")[:10],
            "open_interest": oi,
            "mm_long": mm_l, "mm_short": mm_s, "mm_net": mm_net,
            "mm_pct_oi": round(mm_net / oi * 100, 2) if oi else None,
            "swap_long": swap_l, "swap_short": swap_s, "swap_net": swap_net,
            "swap_pct_oi": round(swap_net / oi * 100, 2) if oi else None,
            "producer_long": prod_l, "producer_short": prod_s, "producer_net": prod_net,
            "producer_pct_oi": round(prod_net / oi * 100, 2) if oi else None,
        })
    return sorted(out, key=lambda x: x["report_date"])


def latest_on_or_before(sorted_records: list, key: str, as_of: str):
    best = None
    for rec in sorted_records:
        if rec[key] <= as_of:
            best = rec
        else:
            break
    return best


def seasonal_baseline_for_year(rows: list, cutoff_year: int) -> dict:
    """5-year seasonal baseline by ISO week, using only years <= cutoff_year.
    Mirrors inventory_tracker.compute_seasonal_baseline but with an explicit
    cutoff so it can be evaluated 'as of' any historical backfill date,
    not just 'as of today'."""
    by_week = defaultdict(list)
    for row in rows:
        try:
            d = date.fromisoformat(row["period"])
        except ValueError:
            continue
        if d.year > cutoff_year:
            continue
        wk = d.isocalendar()[1]
        by_week[wk].append(row["value"])
    baseline = {}
    for wk, vals in by_week.items():
        if len(vals) < 2:
            continue
        avg = sum(vals) / len(vals)
        variance = sum((v - avg) ** 2 for v in vals) / len(vals)
        baseline[wk] = {"avg": avg, "stddev": math.sqrt(variance)}
    return baseline


def upsert_batch(table: str, rows: list, conflict_col: str) -> bool:
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
             SUPA_URL + f"/rest/v1/{table}?on_conflict={conflict_col}"],
            capture_output=True, text=True
        )
        ok = (r.returncode == 0)
        ok_all = ok_all and ok
        print(f"    [{table}] chunk {i//100 + 1}: {'✓' if ok else '⚠️ FAILED ' + r.stderr[:200]} ({len(chunk)} rows)")
    return ok_all


def main():
    print(f"\n  God's Eye — Market Mechanics BACKFILL  ({START_DATE} → {END_DATE})")
    print("  " + "─" * 60)

    print("  Fetching Brent daily history (FRED DCOILBRENTEU, n=1500)...", end=" ", flush=True)
    brent_rows = fred_series("DCOILBRENTEU", n_rows=1500)
    print(f"{len(brent_rows)} rows, {brent_rows[0][0]} → {brent_rows[-1][0]}")

    print("  Fetching CFTC WTI COT history (since 2025-06-01)...", end=" ", flush=True)
    cot_history = fetch_cftc_wti_history("2025-06-01")
    print(f"{len(cot_history)} weekly reports" if cot_history else "FAILED")

    print("  Fetching EIA weekly flow series (imports/exports/util/demand/refiner input)...")
    flow_history = {}
    for sid in EIA_FLOW_BASELINES:
        rows = eia_series_history(sid, n_weeks=100, scale=1.0)
        flow_history[sid] = rows
        print(f"    {sid}: {len(rows)} weekly rows")

    print("  Fetching EIA inventory LEVEL series (for seasonal z-scores, ~5.75yr history)...")
    inv_history = {}
    for sid in INVENTORY_SERIES:
        rows = eia_series_history(sid, n_weeks=310, scale=0.001)  # kbbl -> mmbbl
        inv_history[sid] = rows
        print(f"    {sid}: {len(rows)} weekly rows, {rows[0]['period'] if rows else '—'} → {rows[-1]['period'] if rows else '—'}")

    # Precompute seasonal baselines per (series, cutoff_year) — cutoff_year only
    # changes once per calendar year crossed in the backfill window.
    years_in_window = list(range(START_DATE.year, END_DATE.year + 1))
    baselines = {}
    for sid, rows in inv_history.items():
        for yr in years_in_window:
            baselines[(sid, yr - 1)] = seasonal_baseline_for_year(rows, yr - 1)

    backfill_dates = [d for d, _ in brent_rows
                      if START_DATE.isoformat() <= d <= END_DATE.isoformat()]
    print(f"\n  Backfilling {len(backfill_dates)} trading days...")

    brent_lookup = dict(brent_rows)
    mm_rows_out = []
    inv_rows_out = []

    for idx, d in enumerate(backfill_dates):
        d_date = date.fromisoformat(d)
        brent = brent_lookup.get(d)

        cot = latest_on_or_before(cot_history, "report_date", d)

        flows = {}
        for sid, meta in EIA_FLOW_BASELINES.items():
            rec = latest_on_or_before(flow_history[sid], "period", d)
            val = rec["value"] if rec else None
            z = round((val - meta["mu"]) / meta["sigma"], 3) if val is not None else None
            flows[sid] = {"value": val, "z": z, **meta}
        for s in ("WCRIMUS2", "WCREXUS2", "WCRRIUS2", "WRPUPUS2"):
            if flows.get(s, {}).get("value") is not None:
                flows[s]["value_mbd"] = round(flows[s]["value"] / 1000.0, 2)

        # Inventory z-scores "as of" this backfill date
        cutoff_year = d_date.year - 1
        inv_z = {}
        for sid, rows in inv_history.items():
            rec = latest_on_or_before(rows, "period", d)
            if not rec:
                continue
            rec_date = date.fromisoformat(rec["period"])
            wk = rec_date.isocalendar()[1]
            base = baselines.get((sid, cutoff_year), {}).get(wk)
            if not base or base["stddev"] == 0:
                continue
            z = round((rec["value"] - base["avg"]) / base["stddev"], 3)
            inv_z[sid] = {"z": z, "value": rec["value"]}
            inv_rows_out.append({
                "as_of_date": rec["period"],
                "series_id": sid,
                "series_name": INVENTORY_SERIES[sid],
                "value_mbbl": rec["value"],
                "avg_5yr_mbbl": round(base["avg"], 3),
                "stddev_5yr": round(base["stddev"], 3),
                "z_vs_5yr": z,
                "pct_vs_5yr": round((rec["value"] - base["avg"]) / base["avg"] * 100, 2) if base["avg"] else None,
                "source_tag": "EIA_backfill",
            })

        d_calc = compute_divergence(inv_z, brent, cot, flows)

        mm_rows_out.append({
            "as_of_date": d,
            "cot_report_date": cot["report_date"] if cot else None,
            "wti_open_interest": cot["open_interest"] if cot else None,
            "mm_long": cot["mm_long"] if cot else None,
            "mm_short": cot["mm_short"] if cot else None,
            "mm_net": cot["mm_net"] if cot else None,
            "mm_pct_oi": cot["mm_pct_oi"] if cot else None,
            "swap_long": cot["swap_long"] if cot else None,
            "swap_short": cot["swap_short"] if cot else None,
            "swap_net": cot["swap_net"] if cot else None,
            "swap_pct_oi": cot["swap_pct_oi"] if cot else None,
            "producer_long": cot["producer_long"] if cot else None,
            "producer_short": cot["producer_short"] if cot else None,
            "producer_net": cot["producer_net"] if cot else None,
            "producer_pct_oi": cot["producer_pct_oi"] if cot else None,
            "crude_imports_mbd": flows.get("WCRIMUS2", {}).get("value_mbd"),
            "crude_exports_mbd": flows.get("WCREXUS2", {}).get("value_mbd"),
            "net_import_mbd": d_calc.get("net_import_mbd"),
            "refinery_utilization": flows.get("WPULEUS3", {}).get("value"),
            "product_demand_mbd": flows.get("WRPUPUS2", {}).get("value_mbd"),
            "physical_tightness": d_calc.get("physical_tightness"),
            "price_z": d_calc.get("price_z"),
            "divergence_score": d_calc.get("divergence_score"),
            "supply_signal": d_calc["supply_signal"],
            "financing_signal": d_calc["financing_signal"],
            "intervention_signal": d_calc["intervention_signal"],
            "dominant_mechanic": d_calc["dominant_mechanic"],
            "hypothesis_notes": d_calc.get("hypothesis_notes"),
            "source_tag": "backfill",
        })

        if (idx + 1) % 40 == 0 or idx == len(backfill_dates) - 1:
            print(f"    computed {idx+1}/{len(backfill_dates)} ({d})")

    # Dedup inv_rows_out (same series/date pair may recur across consecutive
    # backfill days that forward-fill to the same EIA report week)
    dedup = {}
    for r in inv_rows_out:
        dedup[(r["as_of_date"], r["series_id"])] = r
    inv_rows_out = list(dedup.values())

    print(f"\n  Upserting {len(mm_rows_out)} rows to market_mechanics_daily...")
    ok1 = upsert_batch("market_mechanics_daily", mm_rows_out, "as_of_date")

    print(f"\n  Upserting {len(inv_rows_out)} rows to inventory_levels...")
    ok2 = upsert_batch("inventory_levels", inv_rows_out, "as_of_date,series_id")

    print(f"\n  {'✓ DONE' if (ok1 and ok2) else '⚠️  Some chunks failed — see above'}")

    strong_intervention = [r for r in mm_rows_out if r["intervention_signal"] == "strong"]
    print(f"\n  'Strong' intervention-signal days in backfilled window: {len(strong_intervention)}")
    for r in strong_intervention[:15]:
        print(f"    {r['as_of_date']}  swap%OI={r['swap_pct_oi']}  producer%OI={r['producer_pct_oi']}")


if __name__ == "__main__":
    main()
