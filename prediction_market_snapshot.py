#!/usr/bin/env python3
"""
Prediction Market Snapshot — June 2026 natural experiment
Pulls live YES prices from Polymarket Gamma API for the framework-vs-market
ledger and (optionally) appends them to Supabase `market_prob_snapshots`.

Usage:
    python3 prediction_market_snapshot.py                 # print table
    python3 prediction_market_snapshot.py --csv out.csv   # also write CSV
    SUPABASE_URL=... SUPABASE_KEY=... python3 prediction_market_snapshot.py --push

Notes:
  - Slug list must stay in sync with `framework_predictions.market_slug`
    in the SPR DATA Supabase project.
  - A scheduled Cowork task also snapshots every 4 days through June 30;
    this script is the manual fallback.
"""
import argparse
import csv
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

GAMMA = "https://gamma-api.polymarket.com"

# Keep in sync with framework_predictions.market_slug
WATCHLIST = [
    "bank-of-japan-increases-interest-rates-by-25-bps-after-the-june-2026-meeting",
    "bank-of-japan-increases-interest-rates-by-50-bps-after-the-june-2026-meeting",
    "no-change-in-bank-of-japans-interest-rates-after-the-june-2026-meeting",
    "will-sp-500-spx-hit-6500-low-in-june-283",
    "spx-hit-6300-low-jun-2026-743-323",
    "will-sp-500-spx-hit-6700-low-in-june-989",
    "fed-emergency-rate-cut-before-2027",
    "us-recession-by-end-of-2026",
    "strait-of-hormuz-traffic-returns-to-normal-by-end-of-june",
    "strait-of-hormuz-traffic-returns-to-normal-by-december-31",
    "will-donald-trump-announce-that-the-united-states-blockade-of-the-strait-of-hormuz-has-been-lifted-by-june-30-2026-159-962",
    "us-x-iran-permanent-peace-deal-by-june-30-2026-837-641-896-877-363-892-537-597",
    "us-x-iran-permanent-peace-deal-by-december-31-2026-961-587-341-574-555-817",
]


def fetch_market(slug: str):
    url = f"{GAMMA}/markets?slug={slug}"
    with urllib.request.urlopen(url, timeout=30) as r:
        data = json.load(r)
    if not data:
        return None
    m = data[0]
    prices = m.get("outcomePrices")
    if isinstance(prices, str):
        prices = json.loads(prices)
    yes = float(prices[0]) if prices else None
    return {
        "market_slug": slug,
        "question": (m.get("question") or "")[:120],
        "yes_prob": yes,
        "end_date": (m.get("endDate") or "")[:10] or None,
    }


def push_supabase(rows):
    base, key = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY")
    if not base or not key:
        print("SUPABASE_URL / SUPABASE_KEY not set — skipping push.", file=sys.stderr)
        return False
    req = urllib.request.Request(
        f"{base}/rest/v1/market_prob_snapshots",
        data=json.dumps(rows).encode(),
        headers={"apikey": key, "Authorization": f"Bearer {key}",
                 "Content-Type": "application/json", "Prefer": "return=minimal"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=30)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", help="also write rows to this CSV path")
    ap.add_argument("--push", action="store_true", help="insert into Supabase")
    args = ap.parse_args()

    now = datetime.now(timezone.utc).isoformat()
    rows = []
    print(f"{'YES':>7}  {'ends':<12} question")
    for slug in WATCHLIST:
        try:
            row = fetch_market(slug)
        except Exception as e:
            print(f"   ERR  {slug}: {e}", file=sys.stderr)
            continue
        if not row or row["yes_prob"] is None:
            print(f"   ---  {slug} (no price / closed?)", file=sys.stderr)
            continue
        row["source"] = "polymarket"
        rows.append(row)
        print(f"{row['yes_prob']:>7.3f}  {row['end_date'] or '':<12} {row['question']}")

    if args.csv and rows:
        new = not os.path.exists(args.csv)
        with open(args.csv, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["snapshot_at"] + list(rows[0].keys()))
            if new:
                w.writeheader()
            for r in rows:
                w.writerow({"snapshot_at": now, **r})
        print(f"\nAppended {len(rows)} rows to {args.csv}")

    if args.push and rows:
        if push_supabase(rows):
            print(f"Pushed {len(rows)} rows to Supabase.")


if __name__ == "__main__":
    main()
