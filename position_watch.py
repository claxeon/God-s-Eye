#!/usr/bin/env python3
"""Watch held Kalshi positions for the risks that actually threaten them.

Per held market:
- rules_primary HASH vs stored baseline — an amendment to a settlement-locked
  position is an EXIT ALERT (sell into the bid before the clarification bites)
- market status (active/closed/settled) — early settlement detection
- top-of-book drift vs baseline

State: position_watch_state.json next to this script (seeded on first run).
Alerts print to stdout AND append to ~/Library/Logs/SIAIS/position_alerts.jsonl.
Read-only against the API; never places orders.

Run: python3 position_watch.py
"""
import hashlib
import json
import os
from datetime import datetime, timezone

from kalshi_client import Kalshi

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(HERE, "position_watch_state.json")
ALERT_LOG = os.path.expanduser("~/Library/Logs/SIAIS/position_alerts.jsonl")


def rules_hash(market):
    blob = (market.get("rules_primary") or "") + "\n" + (market.get("rules_secondary") or "")
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def alert(payload):
    payload["at"] = datetime.now(timezone.utc).isoformat()
    os.makedirs(os.path.dirname(ALERT_LOG), exist_ok=True)
    with open(ALERT_LOG, "a") as f:
        f.write(json.dumps(payload) + "\n")
    print(f"ALERT: {json.dumps(payload)}")


def main():
    k = Kalshi()
    state = {}
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            state = json.load(f)

    held = [p for p in k.positions().get("market_positions", [])
            if float(p.get("position_fp") or 0) != 0]
    if not held:
        print("no open positions")
        return

    alerts = 0
    for p in held:
        t = p["ticker"]
        m = k.market(t)
        book = k.top_of_book(t)
        now = {
            "rules_hash": rules_hash(m),
            "status": m.get("status"),
            "result": m.get("result") or "",
            "yes_bid": book["yes_bid"],
            "yes_ask": book["yes_ask"],
            "checked": datetime.now(timezone.utc).isoformat(),
        }
        base = state.get(t)
        if base is None:
            print(f"{t}: baseline seeded (rules {now['rules_hash']}, "
                  f"bid/ask {now['yes_bid']}/{now['yes_ask']}, status {now['status']})")
        else:
            if now["rules_hash"] != base["rules_hash"]:
                alert({"ticker": t, "kind": "RULES_AMENDED",
                       "old": base["rules_hash"], "new": now["rules_hash"],
                       "action": "REVIEW IMMEDIATELY — consider exit into bid",
                       "yes_bid": now["yes_bid"]})
                alerts += 1
            if now["status"] != base["status"] or now["result"] != base.get("result", ""):
                alert({"ticker": t, "kind": "STATUS_CHANGE",
                       "old": f"{base['status']}/{base.get('result','')}",
                       "new": f"{now['status']}/{now['result']}"})
                alerts += 1
            moved = (base.get("yes_bid") is not None and now["yes_bid"] is not None
                     and abs(now["yes_bid"] - base["yes_bid"]) >= 0.10)
            if moved:
                alert({"ticker": t, "kind": "QUOTE_DRIFT",
                       "old_bid": base["yes_bid"], "new_bid": now["yes_bid"]})
                alerts += 1
            if not alerts:
                print(f"{t}: OK (rules unchanged, status {now['status']}, "
                      f"bid/ask {now['yes_bid']}/{now['yes_ask']})")
        # drift baseline forward for quotes, keep first-seen rules hash sticky
        keep = dict(now)
        if base is not None:
            keep["rules_hash"] = base["rules_hash"]  # amendments must alert every run until acknowledged
        state[t] = keep

    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


if __name__ == "__main__":
    main()
