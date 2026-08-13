#!/usr/bin/env python3
"""OANDA v20 API client — auth, reads, and order placement.

Mirrors kalshi_client.py's shape deliberately (same _load_env() pattern,
same reads-then-writes structure) so order_executor.py's caps/dry-run/audit
wrapper can be extended to this venue without re-deriving the safety
pattern from scratch.

PRACTICE BY DEFAULT (2026-08-13): base_url defaults to the fxPractice
environment. Live trading requires passing practice=False explicitly —
never inferred, never a config-file toggle that could be flipped
accidentally. This mirrors the kill-switch-defaults-safe posture in
order_executor.py.

Units convention (OANDA-native, NOT Alpaca's side+qty): positive units buy
the base currency, negative units sell it. A short USD/JPY (long yen)
position -- the framework's l_cross carry-unwind expression -- is placed
as a NEGATIVE units value on the USD_JPY instrument, not a "sell side".
Get this backwards and the direction of the trade inverts silently.
"""
import json
import os
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PRACTICE_BASE = "https://api-fxpractice.oanda.com"
LIVE_BASE = "https://api-fxtrade.oanda.com"


def _load_env():
    env = {}
    with open(os.path.join(HERE, ".env")) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


class Oanda:
    def __init__(self, practice=True):
        env = _load_env()
        self.token = env["OANDA_API_TOKEN"]
        self.account_id = env["OANDA_ACCOUNT_ID"]
        self.base = PRACTICE_BASE if practice else LIVE_BASE
        self.practice = practice

    def req(self, method, path, body=None):
        r = urllib.request.Request(
            self.base + path, method=method,
            data=json.dumps(body).encode() if body else None)
        r.add_header("Authorization", f"Bearer {self.token}")
        r.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(r, timeout=15) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"{e.code}: {e.read().decode()[:300]}") from None

    # -- reads --
    def account_summary(self):
        return self.req("GET", f"/v3/accounts/{self.account_id}/summary")

    def pricing(self, instruments):
        """instruments: list of strings, e.g. ['USD_JPY']."""
        q = "instruments=" + ",".join(instruments)
        return self.req("GET", f"/v3/accounts/{self.account_id}/pricing?{q}")

    def unrealized_pl_dollars(self):
        """OANDA reports this directly at account level -- no need to sum
        per-position like Alpaca."""
        return float(self.account_summary()["account"]["unrealizedPL"])

    # -- writes (user-confirmed contexts only) --
    def place_order(self, instrument, units, client_order_id=None,
                     time_in_force="FOK", order_type="MARKET"):
        """units: signed int/str. Positive buys the base currency (long),
        negative sells it (short). Returns OANDA v20 order response."""
        order = {
            "instrument": instrument,
            "units": f"{units}",
            "timeInForce": time_in_force,
            "type": order_type,
            "positionFill": "DEFAULT",
        }
        if client_order_id:
            order["clientExtensions"] = {"id": client_order_id}
        return self.req("POST", f"/v3/accounts/{self.account_id}/orders",
                         {"order": order})


if __name__ == "__main__":
    o = Oanda(practice=True)
    summary = o.account_summary().get("account", {})
    print("practice account balance:", summary.get("balance"))
    print("currency:", summary.get("currency"))
    print("marginAvailable:", summary.get("marginAvailable"))
    print("USD/JPY pricing:", json.dumps(o.pricing(["USD_JPY"]), indent=2)[:500])
