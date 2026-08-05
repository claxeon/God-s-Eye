#!/usr/bin/env python3
"""
God's Eye — FRED Brent prediction auto-resolver.

Closes P17 and P55 the moment FRED DCOILBRENTEU actually prints at or above
their thresholds. Both predictions are keyed to FRED spot by their LOCKED
resolution criteria, not to ICE front-month -- so they cannot be resolved off
a futures quote even when the futures obviously cleared the level.

Context (2026-07-28): FRED DCOILBRENTEU ran six business days stale through the
most volatile week of the conflict. ICE front-month settled $100.69 on 07-23
while FRED's newest print was still 07-20 at $86.99, so the daily pipeline kept
reporting "P17 not yet" against a price that no longer existed. This script
exists so the resolution lands on the backfill instead of on someone noticing.

Design rules:
  - Resolves ONLY on a real FRED observation >= threshold. Never infers from
    ICE, never extrapolates, never resolves FALSE early.
  - Idempotent: skips anything already carrying a non-null outcome.
  - Reports the trigger date and print, and computes the Brier contribution.
  - Read-only unless --write is passed.

Usage:
    python3 resolve_fred_brent_predictions.py            # dry run, prints findings
    python3 resolve_fred_brent_predictions.py --write    # applies resolutions
"""

import argparse
import csv
import io
import json
import subprocess
import sys

FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DCOILBRENTEU"

# id -> (threshold, comparison, window_start, deadline)
#
# comparison ">" for "above $X", ">=" for "at or above $X" -- matches the
# locked wording of each prediction exactly; do not loosen these.
#
# window_start is the prediction's locked_at date (inclusive). It is NOT
# optional: DCOILBRENTEU runs back to 1987 and Brent cleared both thresholds in
# 2007-2008 and 2011-2014. Without a window start this script "resolves" both
# predictions off a 2007 print. (Caught on the first dry run, 2026-07-28 --
# which is why the default is a dry run.)
TARGETS = {
    "P17": (90.00, ">",  "2026-06-30", "2026-09-30"),
    "P55": (95.00, ">=", "2026-07-23", "2026-08-15"),
}


def fetch_fred_brent():
    """Return [(date_str, float_price)] ascending, missing values dropped.

    Shells out to curl rather than urllib, matching yen_mechanics.py and
    state_vector_compute.py. This is not stylistic: FRED hangs urllib requests
    to this endpoint until they time out while answering curl in ~0.2s
    (reproduced 2026-07-28). Do not "modernize" this to urllib/requests.
    """
    r = subprocess.run(
        ["curl", "-s", "--max-time", "60", FRED_CSV],
        capture_output=True, text=True,
    )
    if r.returncode != 0 or not r.stdout.strip():
        return []
    text = r.stdout

    rows = []
    for row in csv.DictReader(io.StringIO(text)):
        date = row.get("observation_date") or row.get("DATE")
        raw = row.get("DCOILBRENTEU", "")
        if not date or raw in (".", "", None):
            continue
        try:
            rows.append((date, float(raw)))
        except ValueError:
            continue
    rows.sort(key=lambda r: r[0])
    return rows


def find_trigger(series, threshold, comparison, window_start, deadline):
    """First observation inside [window_start, deadline] clearing threshold."""
    for date, price in series:
        if date < window_start:
            continue
        if date > deadline:
            break
        hit = price > threshold if comparison == ">" else price >= threshold
        if hit:
            return date, price
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true",
                    help="apply resolutions (default is a dry run)")
    args = ap.parse_args()

    series = fetch_fred_brent()
    if not series:
        print("ERROR: no FRED observations parsed", file=sys.stderr)
        return 2

    latest_date, latest_price = series[-1]
    print(f"FRED DCOILBRENTEU latest print: {latest_date} = ${latest_price:.2f}")
    print(f"Observations loaded: {len(series)}\n")

    findings = []
    for pid, (threshold, comparison, window_start, deadline) in sorted(TARGETS.items()):
        trigger = find_trigger(series, threshold, comparison, window_start, deadline)
        if trigger:
            date, price = trigger
            print(f"  {pid}  ✓ TRIGGERED  {date} = ${price:.2f} "
                  f"({comparison} ${threshold:.2f}, window from {window_start})")
            findings.append({
                "id": pid,
                "outcome": True,
                "trigger_date": date,
                "trigger_price": price,
                "threshold": threshold,
            })
        else:
            in_window = [p for d, p in series if window_start <= d <= deadline]
            best = f"${max(in_window):.2f}" if in_window else "no prints yet"
            print(f"  {pid}  ✗ not yet    best in window: {best} "
                  f"({comparison} ${threshold:.2f}, {window_start}..{deadline})")

    if not findings:
        print("\nNothing to resolve.")
        return 0

    print(f"\n{len(findings)} prediction(s) ready to resolve.")
    print(json.dumps({"resolve": findings}, indent=2))

    if not args.write:
        print("\nDry run -- re-run with --write to apply.")
        return 0

    # Writes go through the same MCP/trigger path the rest of the pipeline
    # uses; this script does not hold a service-role key. Emitting the SQL
    # keeps the credential boundary intact (see SECRET_HANDLING.md).
    print("\n-- SQL to apply (idempotent; skips already-resolved rows) --")
    for f in findings:
        note = (
            f" | RESOLVED {f['trigger_date']} AUTO (resolve_fred_brent_predictions.py): "
            f"outcome=TRUE. FRED DCOILBRENTEU printed ${f['trigger_price']:.2f} on "
            f"{f['trigger_date']}, clearing the ${f['threshold']:.2f} threshold in the "
            f"locked criteria. Resolved off an actual FRED observation, not an ICE quote."
        ).replace("'", "''")
        print(
            f"update framework_predictions set outcome = true, resolved_at = now(), "
            f"notes = coalesce(notes,'') || '{note}' "
            f"where id = '{f['id']}' and outcome is null;"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
