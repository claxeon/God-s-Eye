#!/usr/bin/env python3
"""Kalshi Trade API v2 client — auth, reads, and V2 order placement.

Lessons hardened here (2026-07-13, first real fill session):
- Legacy POST /portfolio/orders returns 410; use POST /portfolio/events/orders
  (single YES-denominated book: side=bid buys YES, side=ask sells YES;
  fixed-point strings for count/price).
- Signature covers {ts}{METHOD}/trade-api/v2{path WITHOUT query string};
  including the query fails with INCORRECT_API_KEY_SIGNATURE.
- Orders require in-session user confirmation per the permission gate —
  place_order() must only be called from a user-confirmed context.
"""
import base64
import json
import os
import time
import urllib.error
import urllib.request
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = "https://api.elections.kalshi.com/trade-api/v2"


def _load_env():
    env = {}
    with open(os.path.join(HERE, ".env")) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


class Kalshi:
    def __init__(self):
        from cryptography.hazmat.primitives import serialization
        env = _load_env()
        self.key_id = env["KALSHI_API_KEY_ID"]
        with open(env["KALSHI_PRIVATE_KEY"], "rb") as f:
            self._key = serialization.load_pem_private_key(f.read(), password=None)

    def _sign(self, ts, method, path):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        sign_path = path.split("?")[0]
        msg = f"{ts}{method}/trade-api/v2{sign_path}".encode()
        return base64.b64encode(self._key.sign(
            msg, padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                             salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256())).decode()

    def req(self, method, path, body=None, retries=3):
        for i in range(retries):
            ts = str(int(time.time() * 1000))
            r = urllib.request.Request(
                BASE + path, method=method,
                data=json.dumps(body).encode() if body else None)
            r.add_header("KALSHI-ACCESS-KEY", self.key_id)
            r.add_header("KALSHI-ACCESS-SIGNATURE", self._sign(ts, method, path))
            r.add_header("KALSHI-ACCESS-TIMESTAMP", ts)
            r.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(r, timeout=25) as resp:
                    return json.loads(resp.read())
            except urllib.error.HTTPError as e:
                if e.code == 429 and i < retries - 1:
                    time.sleep(8)
                    continue
                raise RuntimeError(f"{e.code}: {e.read().decode()[:300]}") from None

    # -- reads --
    def balance_cents(self):
        return self.req("GET", "/portfolio/balance")["balance"]

    def positions(self, ticker=None):
        q = f"?ticker={ticker}" if ticker else ""
        return self.req("GET", f"/portfolio/positions{q}")

    def market(self, ticker):
        return self.req("GET", f"/markets/{ticker}")["market"]

    def top_of_book(self, ticker):
        ob = self.req("GET", f"/markets/{ticker}/orderbook").get("orderbook_fp") or {}
        ys = [(float(p), float(q)) for p, q in (ob.get("yes_dollars") or [])]
        ns = [(float(p), float(q)) for p, q in (ob.get("no_dollars") or [])]
        yes_bid = max(ys)[0] if ys else None
        yes_ask = round(1 - max(ns)[0], 4) if ns else None
        return {"yes_bid": yes_bid, "yes_ask": yes_ask}

    # -- writes (user-confirmed contexts only) --
    def place_order(self, ticker, side, count, price_dollars,
                    tif="immediate_or_cancel"):
        """side: 'bid' buys YES, 'ask' sells YES. Returns V2 response."""
        return self.req("POST", "/portfolio/events/orders", {
            "ticker": ticker,
            "client_order_id": str(uuid.uuid4()),
            "side": side,
            "count": f"{count:.2f}",
            "price": f"{price_dollars:.4f}",
            "time_in_force": tif,
            "self_trade_prevention_type": "taker_at_cross",
        })


if __name__ == "__main__":
    k = Kalshi()
    print("balance_cents:", k.balance_cents())
    print("positions:", json.dumps(k.positions(), indent=2)[:600])
