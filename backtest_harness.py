#!/usr/bin/env python3
"""Historical backtest harness for the God's Eye framework (G-026, 2026-07-15).

Runs the FRAMEWORK'S ACTUAL SCORING LOGIC (imported directly from
state_vector_compute.py — compute_leg, compute_state_vector, CALIB, zscore —
not reimplemented) against historical FRED data to generate quasi-resolutions,
instead of waiting years for real predictions to resolve.

HONEST SCOPE (read this before trusting the output):
  - Only components backed by FRED series with deep, free, no-API-key history
    are populated historically: brent_spot (DCOILBRENTEU), usd_jpy (DEXJPUS),
    usd_weakness (-DTWEXBGS), ustr_10y_spread (GS10 - IRLTLT01JPM156N),
    henry_hub (MHHNGSP). Everything else (SPR/inventory levels, CFTC
    positioning, BOJ policy rate history, hormuz_status, fund_gate_count,
    genius_act_status, defense spending, maritime incidents...) has no deep
    free-API history and is left None at every historical point. This means
    l3/l4/l6/l7/l8/l9 legs are neutral or constant throughout, and l1/l2/l5/
    l_cross are UNDER-weighted versions of the live legs. This is a backtest
    of "what the FRED-visible slice of the framework would have said", not
    the full 9-leg/52-prediction live framework. Treat findings as directional,
    not as a substitute for real graded resolutions.
  - Ground-truth outcome used: "Brent falls >=30% within the following 6
    months" — a mechanically checkable analog to the kind of crash/correction
    claims the live framework actually makes (e.g. P16's "Brent closes below
    $85"). Computed for EVERY historical month where 6-month-forward data
    exists — not cherry-picked episodes — so results aren't survivorship-
    biased toward known crashes (2008, 2014-16, 2020, 2022 all fall out
    naturally rather than being hand-selected).
  - Results are NOT written to calibration_history (that table is reserved
    for the live prediction track record — see calibration-tracker skill
    anti-patterns). Written to a local brief instead; a dedicated Supabase
    table is a follow-up requiring explicit user authorization for the
    migration (per the vault's standing constraint on infrastructure changes).

Run: python3 backtest_harness.py [--start 2000-01] [--end 2025-12]
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import date, datetime
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# Reuse the REAL production scoring logic, not a reimplementation.
from state_vector_compute import (  # noqa: E402
    compute_leg, compute_state_vector, CALIB, zscore, _constraint_band,
)

FRED_BASE = "https://fred.stlouisfed.org/graph/fredgraph.csv?id="

FRED_SERIES = {
    "brent_spot": "DCOILBRENTEU",
    "usd_jpy": "DEXJPUS",
    "dxy": "DTWEXBGS",           # feeds usd_weakness = -dxy
    "us10y": "GS10",             # feeds ustr_10y_spread = us10y - jp10y
    "jp10y": "IRLTLT01JPM156N",
    "henry_hub": "MHHNGSP",
}


def fred_history(series_id):
    """Full historical series via FRED's public CSV endpoint — no API key
    required (same endpoint state_vector_compute.py's fred_latest() uses,
    just parsing every row instead of only the last one)."""
    r = subprocess.run(
        ["curl", "-s", "--max-time", "30", FRED_BASE + series_id],
        capture_output=True, text=True,
    )
    if r.returncode != 0 or not r.stdout.strip():
        print(f"  WARN: FRED {series_id} fetch failed", file=sys.stderr)
        return {}
    out = {}
    lines = r.stdout.strip().split("\n")
    for line in lines[1:]:
        parts = line.split(",")
        if len(parts) != 2:
            continue
        d, v = parts[0].strip(), parts[1].strip()
        if v in ("", "."):
            continue
        try:
            out[d] = float(v)
        except ValueError:
            continue
    return out


def month_end_resample(series):
    """{date_str: value} daily/irregular -> {YYYY-MM: last value in month}."""
    by_month = {}
    for d, v in series.items():
        ym = d[:7]
        by_month[ym] = v  # dict insertion order + sorted date strings -> last write wins if iterated sorted
    return by_month


def build_monthly_frame(start, end):
    print("Fetching FRED history (no API key needed, public CSV endpoint)...", file=sys.stderr)
    raw = {}
    for key, sid in FRED_SERIES.items():
        h = fred_history(sid)
        print(f"  {key} ({sid}): {len(h)} observations", file=sys.stderr)
        raw[key] = month_end_resample(dict(sorted(h.items())))

    months = sorted({m for series in raw.values() for m in series
                      if start <= m <= end})
    frame = {}
    for m in months:
        frame[m] = {k: raw[k].get(m) for k in raw}
    return frame


def components_for_month(vals):
    c = {}
    c["brent_spot"] = vals.get("brent_spot")
    c["usd_jpy"] = vals.get("usd_jpy")
    c["usd_weakness"] = -vals["dxy"] if vals.get("dxy") is not None else None
    if vals.get("us10y") is not None and vals.get("jp10y") is not None:
        c["ustr_10y_spread"] = vals["us10y"] - vals["jp10y"]
    else:
        c["ustr_10y_spread"] = None
    c["henry_hub"] = vals.get("henry_hub")
    # everything else in CALIB explicitly None — no deep free history
    for k in CALIB:
        c.setdefault(k, None)
    return c


def bucket_for(prob):
    edges = [(0.0, 0.10, "0-10%"), (0.10, 0.30, "10-30%"), (0.30, 0.50, "30-50%"),
             (0.50, 0.70, "50-70%"), (0.70, 0.90, "70-90%"), (0.90, 1.01, "90-100%")]
    for lo, hi, name in edges:
        if lo <= prob < hi:
            return name
    return "90-100%"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2000-01")
    ap.add_argument("--end", default="2025-12")
    args = ap.parse_args()

    frame = build_monthly_frame(args.start, args.end)
    months = sorted(frame.keys())
    print(f"\n{len(months)} months with at least one FRED series populated "
          f"({months[0] if months else '—'} to {months[-1] if months else '—'})\n")

    # Composite score at each month via the REAL production function.
    scored = {}
    for m in months:
        comps = components_for_month(frame[m])
        L, details = compute_state_vector(comps)
        scored[m] = L

    # Quasi-resolutions: for every month with Brent(t) AND Brent(t+6mo),
    # outcome = 1 if a >=30% drawdown occurred within 6 months.
    brent_by_month = {m: frame[m].get("brent_spot") for m in months
                       if frame[m].get("brent_spot") is not None}
    idx = sorted(brent_by_month.keys())

    resolutions = []
    for i, m in enumerate(idx):
        if i + 6 >= len(idx):
            continue
        fwd_month = idx[i + 6]
        # only use if genuinely ~6 calendar months ahead (guards against gaps)
        y0, mo0 = int(m[:4]), int(m[5:7])
        y1, mo1 = int(fwd_month[:4]), int(fwd_month[5:7])
        delta = (y1 - y0) * 12 + (mo1 - mo0)
        if delta != 6:
            continue
        p0, p1 = brent_by_month[m], brent_by_month[fwd_month]
        if p0 is None or p1 is None or p0 <= 0:
            continue
        outcome = 1 if (p1 / p0) <= 0.70 else 0
        composite = scored.get(m, {}).get("composite")
        if composite is None:
            continue
        resolutions.append({
            "month": m, "brent_t": p0, "brent_t6": p1,
            "drawdown_pct": round((1 - p1 / p0) * 100, 1),
            "composite": composite, "outcome": outcome,
        })

    print(f"{len(resolutions)} quasi-resolutions generated "
          f"(months with composite score AND a clean 6-month-forward Brent read)\n")

    # Calibration curve: bucket by composite score (as a stand-in stress
    # probability, same 6-bucket convention as calibration-tracker), report
    # empirical hit-rate per bucket — no fitted/black-box mapping.
    buckets = defaultdict(list)
    for r in resolutions:
        buckets[bucket_for(r["composite"])].append(r["outcome"])

    print("=== Calibration curve: composite score bucket vs realized 6mo->=30% Brent drawdown ===")
    order = ["0-10%", "10-30%", "30-50%", "50-70%", "70-90%", "90-100%"]
    curve = []
    for b in order:
        outs = buckets.get(b, [])
        if not outs:
            continue
        freq = sum(outs) / len(outs)
        print(f"  {b}: n={len(outs)}  realized drawdown-event freq={freq:.3f}")
        curve.append({"bucket": b, "n": len(outs), "realized_freq": round(freq, 4)})

    n_events = sum(r["outcome"] for r in resolutions)
    print(f"\nBase rate: {n_events}/{len(resolutions)} months "
          f"({n_events/len(resolutions)*100:.1f}%) preceded a >=30% Brent drawdown within 6mo\n")

    worst_misses = sorted(
        [r for r in resolutions if r["outcome"] == 1],
        key=lambda r: r["composite"],
    )[:5]
    print("=== 5 lowest-composite months that STILL preceded a >=30% crash "
          "(where a FRED-only composite would have been most surprised) ===")
    for r in worst_misses:
        print(f"  {r['month']}: composite={r['composite']:.3f} "
              f"-> Brent {r['brent_t']:.1f} to {r['brent_t6']:.1f} "
              f"({r['drawdown_pct']}% drawdown by {r['month'][:4]}-+6mo)")

    brief = {
        "generated": datetime.now().isoformat(),
        "scope_note": "FRED-only components (brent_spot/usd_jpy/usd_weakness/"
                       "ustr_10y_spread/henry_hub); l3/l4/l6/l7/l8/l9 neutral "
                       "or constant. See module docstring for full scope caveat.",
        "months_scored": len(months),
        "resolutions": len(resolutions),
        "base_rate": round(n_events / len(resolutions), 4) if resolutions else None,
        "calibration_curve": curve,
        "worst_misses": worst_misses,
    }
    out_path = os.path.expanduser(
        "~/Downloads/SIAIS/Memory/briefs/2026-07-15-G-026-backtest-harness.md"
    )
    with open(out_path, "w") as f:
        f.write("# G-026 — Historical Backtest Harness, First Run\n\n")
        f.write("```json\n" + json.dumps(brief, indent=2) + "\n```\n")
    print(f"\nBrief written: {out_path}")


if __name__ == "__main__":
    main()
