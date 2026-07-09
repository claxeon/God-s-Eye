#!/usr/bin/env python3
"""
God's Eye — Kalshi structural-arbitrage scanner (READ-ONLY, 2026-07-09).

Detects fee-adjusted riskless structures on Kalshi ladders; LOGS them (no
order placement — execution is a separately-authorized step). Strategy
classes ported from the user's polymarket-arb-bot (April 2026) NegRisk logic:

  1. PARTITION LONG : Σ YES-asks over a mutually-exclusive band ladder < $1
  2. PARTITION SHORT: Σ YES-bids > $1  (executed as buy-NO-everywhere)
  3. MONOTONICITY   : ask YES(above k_lo) < bid YES(above k_hi), k_lo < k_hi
  4. BINARY         : yes_ask + no_ask < $1 on a single market

Fee model: Kalshi taker fee ceil(7·P·(1-P))¢ per contract on EVERY leg
(maker fills are cheaper — margins here are worst-case). Partition checks
only run on events whose markets are bands+tails with two-sided books on
every leg; overlapping threshold ladders (KXFED, KXCPI, …) are NEVER summed
(they are not partitions — that was the false-positive lesson of 2026-07-09).

Output: human-readable report to stdout; findings with margin > MIN_MARGIN
appended as JSON lines to ~/Library/Logs/SIAIS/arb_findings.jsonl.

Run: python3 arb_scan_kalshi.py [series ...]   (default: liquid macro set)
"""
import json
import math
import os
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

MIN_MARGIN = 0.005      # 0.5% after fees to be worth logging
LOG_PATH = os.path.expanduser("~/Library/Logs/SIAIS/arb_findings.jsonl")
DEFAULT_SERIES = ["KXWTIW", "KXWTI", "KXUSDJPY", "KXINX", "KXNASDAQ100",
                  "KXGOLDW", "KXFED", "KXCPI", "KXU3", "KXPAYROLLS"]


def kget(path):
    with urllib.request.urlopen(
            "https://api.elections.kalshi.com/trade-api/v2" + path, timeout=25) as r:
        return json.loads(r.read())


def fee(p):
    """Kalshi taker fee per contract, dollars (worst case)."""
    return math.ceil(7 * max(min(p, 0.99), 0.01) * (1 - max(min(p, 0.99), 0.01))) / 100.0


def top_of_book(ticker):
    ob = kget(f"/markets/{ticker}/orderbook").get("orderbook_fp") or {}
    ys = [(float(p), float(q)) for p, q in (ob.get("yes_dollars") or [])]
    ns = [(float(p), float(q)) for p, q in (ob.get("no_dollars") or [])]
    if not ys or not ns:
        return None
    yb, ybq = max(ys)
    nb, nbq = max(ns)
    return dict(yb=yb, ya=1 - nb, ybq=ybq, naq=nbq)


def main():
    series = sys.argv[1:] or DEFAULT_SERIES
    now = datetime.now(timezone.utc).isoformat()
    events = defaultdict(list)
    for s in series:
        for m in kget(f"/markets?series_ticker={s}&status=open&limit=100").get("markets", []):
            events[(s, m["event_ticker"])].append(m)

    findings = []
    for (s, ev), ms in sorted(events.items()):
        if len(ms) < 2:
            continue
        legs = {}
        for m in ms:
            try:
                b = top_of_book(m["ticker"])
            except Exception:
                b = None
            if b:
                b.update(typ=m["strike_type"], fl=m.get("floor_strike"),
                         cap=m.get("cap_strike"))
                legs[m["ticker"]] = b

        # 4) binary check per market
        for t, l in legs.items():
            m_bin = 1 - (l["ya"] + (1 - l["yb"])) - fee(l["ya"]) - fee(1 - l["yb"])
            if m_bin > MIN_MARGIN:
                findings.append(dict(kind="binary", series=s, event=ev, ticker=t,
                                     margin=round(m_bin, 4),
                                     size=min(l["naq"], l["ybq"]), at=now))

        # 1/2) partition checks — bands+tails events with ALL legs two-sided
        types = {l["typ"] for l in legs.values()}
        if "between" in types and len(legs) == len(ms):
            sum_ask = sum(l["ya"] for l in legs.values())
            sum_bid = sum(l["yb"] for l in legs.values())
            lm = 1 - sum_ask - sum(fee(l["ya"]) for l in legs.values())
            sm = sum_bid - 1 - sum(fee(1 - l["yb"]) for l in legs.values())
            sz_l = min(l["naq"] for l in legs.values())
            sz_s = min(l["ybq"] for l in legs.values())
            if lm > MIN_MARGIN:
                findings.append(dict(kind="partition_long", series=s, event=ev,
                                     legs=len(legs), margin=round(lm, 4), size=sz_l, at=now))
            if sm > MIN_MARGIN:
                findings.append(dict(kind="partition_short", series=s, event=ev,
                                     legs=len(legs), margin=round(sm, 4), size=sz_s, at=now))
            print(f"  {s}/{ev}: partition legs={len(legs)} "
                  f"long={lm*100:+.1f}% short={sm*100:+.1f}%")

        # 3) monotonicity on 'greater' strikes
        gs = sorted((l for l in legs.values()
                     if l["typ"] == "greater" and l["fl"] is not None),
                    key=lambda x: x["fl"])
        for i in range(len(gs)):
            for j in range(i + 1, len(gs)):
                lo, hi = gs[i], gs[j]
                mg = hi["yb"] - lo["ya"] - fee(lo["ya"]) - fee(1 - hi["yb"])
                if mg > MIN_MARGIN:
                    findings.append(dict(kind="monotonicity", series=s, event=ev,
                                         k_lo=lo["fl"], k_hi=hi["fl"],
                                         margin=round(mg, 4),
                                         size=min(lo["naq"], hi["ybq"]), at=now))

    if findings:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a") as f:
            for x in findings:
                f.write(json.dumps(x) + "\n")
        print(f"\n{len(findings)} FINDINGS (margin>{MIN_MARGIN:.1%}) — logged to {LOG_PATH}")
        for x in findings:
            print("  " + json.dumps(x))
    else:
        print(f"\nno fee-adjusted arbs above {MIN_MARGIN:.1%} at {now}")


if __name__ == "__main__":
    main()
