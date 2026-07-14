#!/usr/bin/env python3
"""Settlement-lock scanner: newly listed Kalshi markets whose rules windows
reach into ALREADY-OBSERVED history.

The class that produced the first real fill (2026-07-13): KXHORMUZAVG listed
2026-07-09 with rules windows opening 2025-07-06 — the settlement source (IMF
PortWatch) had already crossed every strike, but MMs quoted it as a forecast.

Method: paginate ALL open markets (no status filter — list objects carry
status 'active'; use min_close_ts to skip intraday noise), regex rules_primary
for past-dated window openers, keep markets that (a) listed recently,
(b) are not already priced as determined (ask < 0.95).

A hit here is a CANDIDATE, not a trade: verify against the named settlement
source directly (the PortWatch step) before presenting. Findings are appended
to ~/Library/Logs/SIAIS/rules_lock_findings.jsonl.

Run: python3 rules_lock_scan.py [days_back_for_open_time]
"""
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

LOG_PATH = os.path.expanduser("~/Library/Logs/SIAIS/rules_lock_findings.jsonl")
MONTHS = ("January|February|March|April|May|June|July|August|September|"
          "October|November|December")
PAST_DATE = re.compile(
    rf"(?:after|since|between)\s+({MONTHS})\s+\d{{1,2}},?\s+(20\d\d)", re.I)
MONTH_NUM = {m: i + 1 for i, m in enumerate(MONTHS.split("|"))}


def kget(path, retries=5):
    for i in range(retries):
        try:
            with urllib.request.urlopen(
                    "https://api.elections.kalshi.com/trade-api/v2" + path,
                    timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429 and i < retries - 1:
                time.sleep(12)
                continue
            raise


def main():
    days_back = int(sys.argv[1]) if len(sys.argv) > 1 else 14
    now = datetime.now(timezone.utc)
    open_cutoff = (now - timedelta(days=days_back)).strftime("%Y-%m-%d")
    # skip intraday/weekly noise: only markets closing 3+ weeks out
    min_close = int((now + timedelta(days=21)).timestamp())
    window_stale = now - timedelta(days=7)  # window opener must be ≥1wk past

    cursor, pages, cands = "", 0, []
    while pages < 120:
        d = kget(f"/markets?limit=1000&min_close_ts={min_close}"
                 + (f"&cursor={cursor}" if cursor else ""))
        pages += 1
        for m in d.get("markets", []):
            if m.get("status") not in ("active", "open"):
                continue
            if (m.get("open_time") or "") < open_cutoff:
                continue
            rules = m.get("rules_primary") or ""
            mt = PAST_DATE.search(rules)
            if not mt:
                continue
            mon, yr = MONTH_NUM[mt.group(1).capitalize()], int(mt.group(2))
            opener = datetime(yr, mon, 1, tzinfo=timezone.utc)
            if opener > window_stale:
                continue
            ya = m.get("yes_ask_dollars")
            ya = float(ya) if ya not in (None, "") else None
            if ya is not None and ya >= 0.95:
                continue
            cands.append({
                "ticker": m["ticker"], "open_time": m.get("open_time"),
                "yes_bid": m.get("yes_bid_dollars"),
                "yes_ask": m.get("yes_ask_dollars"),
                "window_opener": f"{yr}-{mon:02d}",
                "rules": rules[:220],
                "found_at": now.isoformat(),
            })
        cursor = d.get("cursor") or ""
        if not cursor:
            break
        time.sleep(1.0)

    if cands:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a") as f:
            for c in cands:
                f.write(json.dumps(c) + "\n")
        print(f"{len(cands)} candidate(s) — VERIFY AGAINST SETTLEMENT SOURCE "
              f"before presenting (logged to {LOG_PATH}):")
        for c in cands:
            print(f"  {c['ticker']}  open={c['open_time'][:10]}  "
                  f"bid/ask={c['yes_bid']}/{c['yes_ask']}  "
                  f"window opens {c['window_opener']}")
            print(f"    {c['rules'][:150]}")
    else:
        print(f"no settlement-lock candidates "
              f"(pages={pages}, listed since {open_cutoff}) at {now.isoformat()}")


if __name__ == "__main__":
    main()
