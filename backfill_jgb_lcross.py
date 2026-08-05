#!/usr/bin/env python3
"""
God's Eye — Re-run l_cross history for the jgb_fiscal_stress component.

WHY
---
On 2026-08-03 l_cross gained a sixth component, `jgb_fiscal_stress` (JGB 10Y
level, w=0.10), and the existing five were renormalized x0.90. Without a
backfill the l_cross series would have a silent discontinuity at that date.

THE TRANSFORM IS EXACT, NOT AN APPROXIMATION
--------------------------------------------
compute_leg scores a leg as sigmoid(sum_i z_i * w_i). Scaling every existing
weight by 0.90 and adding one new term is therefore exactly:

    z_sum_old  = logit(l_cross_old)                 # invert the sigmoid
    z_sum_new  = 0.90 * z_sum_old + 0.10 * z_jgb
    l_cross_new = sigmoid(z_sum_new)

So the history can be rebuilt from the STORED l_cross alone -- no need to
re-fetch every historical component, which is fortunate because several of them
(CFTC positioning, episode days) are not reproducible retrospectively.

JGB SOURCE PER DATE
-------------------
FRED IRLTLT01JPM156N (monthly, first-of-month dated) mapped to each row's
month. FRED's coverage ends before the most recent rows, so any month past its
last print falls back to --recent-jgb (default: the live 2561.T proxy value).
That is flagged per row in the output.

NOTE: l_cross is NOT part of the composite (compute_state_vector weights L1..L9
only), so this rewrites the l_cross column alone. Composite is untouched.

Usage:
    python3 backfill_jgb_lcross.py               # dry run, prints before/after
    python3 backfill_jgb_lcross.py --write       # emit SQL to apply
"""

import argparse
import csv
import io
import json
import math
import subprocess
import sys

SUPABASE_URL = "https://snykuqyceqpplnzmyksp.supabase.co"
SUPABASE_ANON = "sb_publishable_TJg65x5w56CulOEdWFJNyQ_89loJtit"

JGB_MU, JGB_SIGMA, JGB_W = 0.3293, 0.4961, 0.10
RENORM = 0.90          # existing five weights scaled by this
FRED_JGB = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=IRLTLT01JPM156N"

EPS = 1e-9


def logit(p):
    p = min(max(p, EPS), 1 - EPS)
    return math.log(p / (1 - p))


def sigmoid(x):
    return 1.0 / (1.0 + math.exp(-x))


def fred_jgb_monthly():
    r = subprocess.run(["curl", "-s", "--max-time", "45", FRED_JGB],
                       capture_output=True, text=True)
    out = {}
    for row in list(csv.reader(io.StringIO(r.stdout)))[1:]:
        if len(row) == 2 and row[1] not in (".", ""):
            out[row[0][:7]] = float(row[1])     # 'YYYY-MM' -> yield
    return out


def fetch_history():
    url = (f"{SUPABASE_URL}/rest/v1/state_vector_history"
           f"?select=obs_date,l_cross,composite&order=obs_date.asc")
    r = subprocess.run(["curl", "-s", "--max-time", "45", url,
                        "-H", f"apikey: {SUPABASE_ANON}",
                        "-H", f"Authorization: Bearer {SUPABASE_ANON}"],
                       capture_output=True, text=True)
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="emit SQL to apply")
    ap.add_argument("--recent-jgb", type=float, default=2.872,
                    help="JGB %% for months past FRED's last print (default: live 2561.T proxy)")
    args = ap.parse_args()

    jgb = fred_jgb_monthly()
    hist = fetch_history()
    if not jgb or not hist:
        print("ERROR: could not fetch JGB series or history", file=sys.stderr)
        return 2
    last_fred = max(jgb)
    print(f"FRED JGB monthly: {len(jgb)} months, last = {last_fred} ({jgb[last_fred]:.3f}%)")
    print(f"History rows: {len(hist)}")
    print(f"Fallback for months > {last_fred}: {args.recent_jgb:.3f}% (--recent-jgb)")
    print()
    print(f"{'date':12} {'l_cross_old':>11} {'jgb%':>7} {'z_jgb':>7} {'l_cross_new':>11} {'delta':>8}  src")
    print("-" * 74)

    updates = []
    for row in hist:
        d = row["obs_date"]
        old = row.get("l_cross")
        if old is None:
            continue
        old = float(old)
        mo = d[:7]
        if mo in jgb:
            y, src = jgb[mo], "fred"
        else:
            y, src = args.recent_jgb, "PROXY"
        z = (y - JGB_MU) / JGB_SIGMA
        new = sigmoid(RENORM * logit(old) + JGB_W * z)
        updates.append((d, old, round(new, 4)))
        print(f"{d:12} {old:11.4f} {y:7.3f} {z:+7.2f} {new:11.4f} {new-old:+8.4f}  {src}")

    if updates:
        deltas = [n - o for _, o, n in updates]
        print("-" * 74)
        print(f"rows: {len(updates)}   mean delta {sum(deltas)/len(deltas):+.4f}   "
              f"min {min(deltas):+.4f}   max {max(deltas):+.4f}")

    if not args.write:
        print("\nDry run — re-run with --write to emit SQL.")
        return 0

    print("\n-- SQL to apply (l_cross only; composite untouched) --")
    for d, old, new in updates:
        print(f"update state_vector_history set l_cross = {new} "
              f"where obs_date = '{d}';  -- was {old}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
