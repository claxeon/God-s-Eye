#!/usr/bin/env python3
"""
God's Eye — EIA SPR Data Pull
==============================
Pulls the correct SPR-specific series alongside the commercial crude series
to resolve the WCSSTUS1 vs WCSSPUS1 series disambiguation.

Series:
    WCSSPUS1 — US Strategic Petroleum Reserve (SPR only), kb
    WCSSTUS1 — US crude oil stocks (commercial only, excl. SPR), kb
    WCESTUS1 — US crude oil commercial stocks (alt label), kb

Run this the day EIA weekly petroleum status drops (Wednesdays ~10:30am ET).

Usage:
    python3 eia_spr_pull.py

Output:
    - Prints latest 12 weeks for each series side-by-side
    - Confirms which series the framework has been tracking
    - Computes draw rate and runway for the SPR-only series
    - Saves results to ../Intelligence Briefs/EIA_pull_<date>.md
"""

import json
import urllib.request
from datetime import date, datetime, timedelta

EIA_API_KEY = "6JlB2qAQoHxNGL6kEiiZ6fIRt8cU5FlqR8ReVWYE"
EIA_BASE    = "https://api.eia.gov/v2/petroleum/sum/sndw/data/"

# Series to pull
SERIES = {
    "WCSSPUS1": "SPR only (kb)",
    "WCSSTUS1": "Framework series — verify what this is (kb)",
    "WCESTUS1": "Commercial crude excl. SPR (kb)",
}

# SPR floor models
FLOORS = {
    "Nominal DOE":        250_000,   # kb
    "DOE min operating":  273_000,
    "Heel-corrected":     330_000,   # 4 sites × ~20 mmbbl heel
    "Functional (380)":   380_000,   # other analyst's floor
}

DRAW_RATE_KBD = 1_285   # kb/day — 9 mmbbl/week ÷ 7


def fetch_series(series_id: str, n: int = 20) -> list[dict]:
    params = (
        f"?frequency=weekly"
        f"&data[0]=value"
        f"&facets[series][]={series_id}"
        f"&sort[0][column]=period"
        f"&sort[0][direction]=desc"
        f"&length={n}"
        f"&api_key={EIA_API_KEY}"
    )
    url = EIA_BASE + params
    with urllib.request.urlopen(url, timeout=15) as r:
        payload = json.loads(r.read())
    rows = payload.get("response", {}).get("data", [])
    return sorted(rows, key=lambda x: x["period"])


def print_series(label: str, rows: list[dict]):
    print(f"\n{'─'*60}")
    print(f"  {label}")
    print(f"{'─'*60}")
    print(f"  {'Date':<14} {'Value (kb)':>12}  {'Wk Δ (kb)':>12}  {'Draw (kb/d)':>12}")
    prev = None
    for r in rows[-12:]:
        val = r.get("value")
        if val is None:
            continue
        val = int(float(val))
        delta = val - prev if prev is not None else 0
        rate  = delta / 7
        flag  = ""
        if delta < -7_000:
            flag = "🔴"
        elif delta < -3_500:
            flag = "⚠️"
        elif delta < 0:
            flag = "🟡"
        print(f"  {r['period']:<14} {val:>12,}  {delta:>+12,}  {rate:>+12.0f}  {flag}")
        prev = val
    return rows[-1].get("value")


def runway_table(current_kb: float):
    current_kb = int(float(current_kb))
    print(f"\n{'─'*60}")
    print(f"  SPR RUNWAY TABLE  (current: {current_kb:,} kb = {current_kb/1000:.1f} mmbbl)")
    print(f"  Draw rate: {DRAW_RATE_KBD:,} kb/d  ({DRAW_RATE_KBD*7/1000:.1f} mmbbl/week)")
    print(f"{'─'*60}")
    print(f"  {'Floor Model':<22} {'Floor (kb)':>12} {'Buffer (kb)':>12} {'Days':>8} {'Est. Date':<14}")
    today = date.today()
    for name, floor_kb in FLOORS.items():
        buf  = current_kb - floor_kb
        days = buf / DRAW_RATE_KBD if buf > 0 else 0
        est  = (today + timedelta(days=days)).strftime("%b %d, %Y") if days > 0 else "ALREADY PAST"
        print(f"  {name:<22} {floor_kb:>12,} {buf:>+12,} {days:>8.0f}  {est}")


def save_markdown(series_data: dict[str, list]):
    today_str = date.today().isoformat()
    lines = [
        f"---",
        f"tags: [gods-eye, eia, spr, pull, confirmed-data]",
        f"date: {today_str}",
        f"source: EIA API v2 — petroleum/sum/sndw",
        f"series: WCSSPUS1, WCSSTUS1, WCESTUS1",
        f"---",
        f"",
        f"# EIA SPR Pull — {today_str}",
        f"",
        f"> Series disambiguation pull. Confirm which series the framework has been tracking.",
        f"",
    ]
    for sid, rows in series_data.items():
        lines.append(f"## {sid} — {SERIES[sid]}")
        lines.append(f"")
        lines.append(f"| Date | Value (kb) | Wk Δ | Draw (kb/d) |")
        lines.append(f"|---|---|---|---|")
        prev = None
        for r in rows[-12:]:
            val = r.get("value")
            if val is None:
                continue
            val = int(float(val))
            delta = val - prev if prev is not None else 0
            rate  = delta / 7
            lines.append(f"| {r['period']} | {val:,} | {delta:+,} | {rate:+.0f} |")
            prev = val
        lines.append(f"")

    # Runway for WCSSPUS1
    spr_rows = series_data.get("WCSSPUS1", [])
    if spr_rows:
        current = int(float(spr_rows[-1]["value"]))
        lines.append(f"## SPR Runway (WCSSPUS1)")
        lines.append(f"")
        lines.append(f"Current: **{current:,} kb ({current/1000:.1f} mmbbl)**  ")
        lines.append(f"Draw rate: **{DRAW_RATE_KBD:,} kb/d ({DRAW_RATE_KBD*7/1000:.1f} mmbbl/wk)**")
        lines.append(f"")
        lines.append(f"| Floor Model | Floor (mmbbl) | Buffer (mmbbl) | Days | Est. Floor Date |")
        lines.append(f"|---|---|---|---|---|")
        today = date.today()
        for name, floor_kb in FLOORS.items():
            buf  = current - floor_kb
            days = buf / DRAW_RATE_KBD if buf > 0 else 0
            est  = (today + timedelta(days=days)).strftime("%b %d, %Y") if days > 0 else "**ALREADY PAST**"
            lines.append(f"| {name} | {floor_kb//1000} | {buf//1000:+} | {days:.0f} | {est} |")
        lines.append(f"")

    out_path = f"/Users/leehutton/Downloads/God's Eye/Intelligence Briefs/EIA_pull_{today_str}.md"
    with open(out_path, "w") as f:
        f.write("\n".join(lines))
    print(f"\n  ✅ Saved → Intelligence Briefs/EIA_pull_{today_str}.md")


if __name__ == "__main__":
    print("\n🛢  God's Eye — EIA Series Pull")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    series_data = {}
    for sid, label in SERIES.items():
        try:
            rows = fetch_series(sid)
            series_data[sid] = rows
            last = print_series(f"{sid} — {label}", rows)
        except Exception as e:
            print(f"\n  ⚠️  {sid} fetch failed: {e}")
            series_data[sid] = []
            last = None

    # Runway off SPR-only series
    spr_rows = series_data.get("WCSSPUS1", [])
    if spr_rows:
        runway_table(spr_rows[-1]["value"])

    save_markdown(series_data)
