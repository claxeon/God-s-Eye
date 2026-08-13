#!/usr/bin/env python3
"""Alpaca Trading API client — auth, reads, and order placement.

Mirrors kalshi_client.py's shape deliberately (same _load_env() pattern,
same reads-then-writes structure, same "writes are a user-confirmed context
only" discipline) so order_executor.py's caps/dry-run/audit wrapper can be
extended to this venue without re-deriving the safety pattern from scratch.

PAPER BY DEFAULT (2026-08-13): base_url defaults to paper-api.alpaca.markets.
Live trading requires passing paper=False explicitly — never inferred, never
a config-file toggle that could be flipped accidentally. This mirrors the
kill-switch-defaults-safe posture in order_executor.py.
"""
import json
import os
import urllib.error
import urllib.request
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
PAPER_BASE = "https://paper-api.alpaca.markets"
LIVE_BASE = "https://api.alpaca.markets"
DATA_BASE = "https://data.alpaca.markets"   # market data lives on a separate host from trading, same auth


def _load_env():
    env = {}
    with open(os.path.join(HERE, ".env")) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


class Alpaca:
    def __init__(self, paper=True):
        env = _load_env()
        self.key_id = env["ALPACA_API_KEY"]
        self.secret_key = env["ALPACA_SECRET_KEY"]
        self.base = PAPER_BASE if paper else LIVE_BASE
        self.paper = paper

    def req(self, method, path, body=None):
        r = urllib.request.Request(
            self.base + path, method=method,
            data=json.dumps(body).encode() if body else None)
        r.add_header("APCA-API-KEY-ID", self.key_id)
        r.add_header("APCA-API-SECRET-KEY", self.secret_key)
        r.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(r, timeout=15) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"{e.code}: {e.read().decode()[:300]}") from None

    # -- reads --
    def account(self):
        return self.req("GET", "/v2/account")

    def positions(self):
        return self.req("GET", "/v2/positions")

    def unrealized_pl_dollars(self):
        """Sum of unrealized_pl across all open positions. Assumes this
        account is dedicated to claim-class-governed positions -- if it
        ever holds unrelated positions too, this would need filtering by
        a symbol -> claim_class mapping, which doesn't exist yet."""
        return sum(float(p["unrealized_pl"]) for p in self.positions())

    def clock(self):
        return self.req("GET", "/v2/clock")

    def latest_trade(self, symbol):
        """Market data lives on DATA_BASE, not self.base -- same auth headers."""
        r = urllib.request.Request(f"{DATA_BASE}/v2/stocks/{symbol}/trades/latest")
        r.add_header("APCA-API-KEY-ID", self.key_id)
        r.add_header("APCA-API-SECRET-KEY", self.secret_key)
        try:
            with urllib.request.urlopen(r, timeout=15) as resp:
                return json.loads(resp.read())["trade"]
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"{e.code}: {e.read().decode()[:300]}") from None

    # -- writes (user-confirmed contexts only) --
    def place_order(self, symbol, side, qty, order_type="market",
                     time_in_force="day", client_order_id=None,
                     limit_price=None):
        """side: 'buy' or 'sell'. Returns Alpaca order response."""
        body = {
            "symbol": symbol,
            "qty": f"{qty}",
            "side": side,
            "type": order_type,
            "time_in_force": time_in_force,
            "client_order_id": client_order_id or str(uuid.uuid4()),
        }
        if limit_price is not None:
            body["limit_price"] = f"{limit_price:.2f}"
        return self.req("POST", "/v2/orders", body)


if __name__ == "__main__":
    a = Alpaca(paper=True)
    acct = a.account()
    print("paper account status:", acct.get("status"))
    print("buying_power:", acct.get("buying_power"))
    print("cash:", acct.get("cash"))
    print("portfolio_value:", acct.get("portfolio_value"))
    print("currency:", acct.get("currency"))
    print("clock:", json.dumps(a.clock(), indent=2))
