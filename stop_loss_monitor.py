#!/usr/bin/env python3
"""
Per-position stop-loss monitor (2026-08-13, item 2 of the G-062 drawdown
work -- scoped earlier this session, built here).

Different shape from order_executor.py / equity_executor.py / fx_executor.py
on purpose: those are ENTRY gates, checked once when a new order is
proposed. A stop-loss has to look at positions that already exist and
decide whether to exit them -- there's no "order being placed" to hook
into. This is a single PASS over current account state: check every open
position across Alpaca and OANDA, close any whose drawdown from its own
COST BASIS (not bankroll -- see order_caps.json's _aggregate_drawdown_halt
for the bankroll-relative check, which is a different, already-built
thing) has crossed the threshold.

NOT continuous. Nothing in this vault runs a persistent background
process -- run this periodically (cron/launchd/manual) the same way
daily_loop.sh is scheduled, not as a daemon. A gap between runs is a real,
accepted limitation: a position can move past the threshold and back
before the next pass ever sees it.

Deliberately does NOT call check_cap() or check_drawdown_halt() from
order_executor.py -- those are entry gates on how much NEW risk to add;
closing a losing position is an exit and must never be blocked by an
entry cap. It still validates inputs, defaults to dry-run, and audits
every check (not just every close) to the same order_audit.jsonl.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from order_executor import AUDIT_LOG, load_caps, _audit  # noqa: E402

STOP_LOSS_KEY = "_per_position_stop_loss_pct"


def alpaca_stop_loss_candidates(client, threshold_pct):
    """Uses Alpaca's own unrealized_plpc (fraction, e.g. -0.15 = -15%)
    directly rather than recomputing from market_value/cost_basis --
    that's the exact number Alpaca itself uses for P&L, no room for a
    unit-mismatch bug like the OANDA one caught earlier this session."""
    candidates = []
    for p in client.positions():
        plpc = float(p["unrealized_plpc"]) * 100
        if plpc <= -threshold_pct:
            candidates.append({
                "venue": "alpaca", "symbol": p["symbol"], "qty": p["qty"],
                "dd_pct": plpc, "unrealized_pl": float(p["unrealized_pl"]),
                "market_value": float(p["market_value"]),
            })
    return candidates


def oanda_stop_loss_candidates(client, threshold_pct, usd_is_base=True):
    """usd_is_base: True (default, correct for USD_JPY) means currentUnits
    are already USD notional, so dd_pct = unrealizedPL / abs(units) * 100.
    Same caveat as fx_executor.py's execute_fx_order -- a pair where USD
    is the quote currency needs this False and a price-based conversion
    instead. Don't add a new pair without setting this deliberately."""
    candidates = []
    for t in client.open_trades():
        units = float(t["currentUnits"])
        pl = float(t["unrealizedPL"])
        notional = abs(units) if usd_is_base else abs(units) * float(t["price"])
        if notional <= 0:
            continue
        dd_pct = (pl / notional) * 100
        if dd_pct <= -threshold_pct:
            candidates.append({
                "venue": "oanda", "trade_id": t["id"], "instrument": t["instrument"],
                "units": units, "dd_pct": dd_pct, "unrealized_pl": pl,
                "notional": notional,
            })
    return candidates


def run_stop_loss_pass(alpaca_client=None, oanda_client=None, threshold_pct=None,
                        caps=None, dry_run=True, audit_path=AUDIT_LOG):
    """The single entry point. Returns a list of results, one per position
    checked -- 'ok' (within threshold), 'dry_run' (would close), or
    'closed' (actually closed). Never raises on a single position's
    failure -- one bad close attempt must not stop the rest of the pass
    from running."""
    caps = caps if caps is not None else load_caps()
    threshold_pct = threshold_pct if threshold_pct is not None else caps.get(STOP_LOSS_KEY)
    if threshold_pct is None:
        raise ValueError(f"no {STOP_LOSS_KEY} configured in order_caps.json and none passed explicitly")

    results = []

    if alpaca_client is not None:
        for c in alpaca_stop_loss_candidates(alpaca_client, threshold_pct):
            _audit({"venue": "alpaca", "event": "STOP_LOSS_TRIGGERED", "symbol": c["symbol"],
                    "dd_pct": c["dd_pct"], "unrealized_pl": c["unrealized_pl"],
                    "threshold_pct": threshold_pct, "dry_run": dry_run}, audit_path)
            if dry_run:
                results.append({"status": "dry_run", **c})
                continue
            try:
                resp = alpaca_client.close_position(c["symbol"])
                _audit({"venue": "alpaca", "event": "STOP_LOSS_CLOSED", "symbol": c["symbol"],
                        "dd_pct": c["dd_pct"], "response_order_id": (resp or {}).get("id")},
                       audit_path)
                results.append({"status": "closed", "response": resp, **c})
            except Exception as e:
                _audit({"venue": "alpaca", "event": "STOP_LOSS_CLOSE_ERROR", "symbol": c["symbol"],
                        "error": str(e)}, audit_path)
                results.append({"status": "error", "error": str(e), **c})

    if oanda_client is not None:
        for c in oanda_stop_loss_candidates(oanda_client, threshold_pct):
            _audit({"venue": "oanda", "event": "STOP_LOSS_TRIGGERED", "trade_id": c["trade_id"],
                    "instrument": c["instrument"], "dd_pct": c["dd_pct"],
                    "unrealized_pl": c["unrealized_pl"], "threshold_pct": threshold_pct,
                    "dry_run": dry_run}, audit_path)
            if dry_run:
                results.append({"status": "dry_run", **c})
                continue
            try:
                resp = oanda_client.close_trade(c["trade_id"])
                _audit({"venue": "oanda", "event": "STOP_LOSS_CLOSED", "trade_id": c["trade_id"],
                        "instrument": c["instrument"], "dd_pct": c["dd_pct"]}, audit_path)
                results.append({"status": "closed", "response": resp, **c})
            except Exception as e:
                _audit({"venue": "oanda", "event": "STOP_LOSS_CLOSE_ERROR", "trade_id": c["trade_id"],
                        "error": str(e)}, audit_path)
                results.append({"status": "error", "error": str(e), **c})

    return results


# --------------------------------------------------------------------------
# Self-test -- same FakeClient/temp-audit-log pattern as the other three
# executors. No network, no credentials.
# --------------------------------------------------------------------------

class FakeAlpacaPositions:
    def __init__(self, positions, raise_on_close=False):
        self._positions = positions
        self.close_calls = []
        self.raise_on_close = raise_on_close

    def positions(self):
        return self._positions

    def close_position(self, symbol):
        if self.raise_on_close:
            raise RuntimeError("simulated close failure")
        self.close_calls.append(symbol)
        return {"id": f"close-{len(self.close_calls)}", "symbol": symbol}


class FakeOandaTrades:
    def __init__(self, trades, raise_on_close=False):
        self._trades = trades
        self.close_calls = []
        self.raise_on_close = raise_on_close

    def open_trades(self):
        return self._trades

    def close_trade(self, trade_id):
        if self.raise_on_close:
            raise RuntimeError("simulated close failure")
        self.close_calls.append(trade_id)
        return {"id": trade_id, "closed": True}


def _pos(symbol, plpc, pl=-1.0, mv=100.0, qty="1"):
    return {"symbol": symbol, "qty": qty, "unrealized_plpc": str(plpc), "unrealized_pl": str(pl), "market_value": str(mv)}


def _trade(trade_id, instrument, units, pl, price="159.5"):
    return {"id": trade_id, "instrument": instrument, "currentUnits": str(units),
            "unrealizedPL": str(pl), "price": price}


def self_test():
    import tempfile
    results = []

    def check(name, cond):
        results.append((name, bool(cond)))

    audit_fd, audit_path = tempfile.mkstemp(suffix=".jsonl")
    os.close(audit_fd)
    os.remove(audit_path)
    TEST_CAPS = {"_per_position_stop_loss_pct": 15.0}

    # 1. Alpaca position breaching -15% gets flagged as a dry-run close.
    fa = FakeAlpacaPositions([_pos("GLD", -0.16)])
    r = run_stop_loss_pass(alpaca_client=fa, caps=TEST_CAPS, dry_run=True, audit_path=audit_path)
    check("1a. breaching position flagged", len(r) == 1 and r[0]["status"] == "dry_run")
    check("1b. dry-run never calls close_position", len(fa.close_calls) == 0)

    # 2. Alpaca position within threshold is left alone entirely.
    fa = FakeAlpacaPositions([_pos("ITA", -0.05)])
    r = run_stop_loss_pass(alpaca_client=fa, caps=TEST_CAPS, dry_run=True, audit_path=audit_path)
    check("2. within-threshold position not flagged", len(r) == 0)

    # 3. A WINNING position is never touched, obviously.
    fa = FakeAlpacaPositions([_pos("XLE", 0.20, pl=200.0)])
    r = run_stop_loss_pass(alpaca_client=fa, caps=TEST_CAPS, dry_run=True, audit_path=audit_path)
    check("3. winning position not flagged", len(r) == 0)

    # 4. Live mode actually calls close_position via the dedicated endpoint.
    fa = FakeAlpacaPositions([_pos("TBF", -0.18)])
    r = run_stop_loss_pass(alpaca_client=fa, caps=TEST_CAPS, dry_run=False, audit_path=audit_path)
    check("4a. live mode closes the position", fa.close_calls == ["TBF"])
    check("4b. result reports closed", r[0]["status"] == "closed")

    # 5. Mixed book: only the breaching position closes, the other is untouched.
    fa = FakeAlpacaPositions([_pos("GLD", -0.16), _pos("ITA", -0.03)])
    r = run_stop_loss_pass(alpaca_client=fa, caps=TEST_CAPS, dry_run=False, audit_path=audit_path)
    check("5a. exactly one position closed", fa.close_calls == ["GLD"])
    check("5b. exactly one result returned", len(r) == 1)

    # 6. Close failure caught and reported, not raised, and doesn't stop
    #    the rest of the pass.
    fa = FakeAlpacaPositions([_pos("GLD", -0.20), _pos("ITA", -0.20)], raise_on_close=True)
    r = run_stop_loss_pass(alpaca_client=fa, caps=TEST_CAPS, dry_run=False, audit_path=audit_path)
    check("6. both errors caught, pass completes", len(r) == 2 and all(x["status"] == "error" for x in r))

    # 7. OANDA: USD-base pair (USD_JPY), dd_pct computed as pl/abs(units),
    #    NOT pl/(abs(units)*price) -- the exact bug class fx_executor.py
    #    fixed earlier this session, now guarded here too.
    fo = FakeOandaTrades([_trade("9", "USD_JPY", -1000, -160.0)])  # -16% of 1000 USD notional
    r = run_stop_loss_pass(oanda_client=fo, caps=TEST_CAPS, dry_run=True, audit_path=audit_path)
    check("7a. USD-base dd_pct computed correctly (not inflated ~160x by price)",
          len(r) == 1 and abs(r[0]["dd_pct"] - (-16.0)) < 0.01)

    # 8. OANDA live close calls the dedicated close-trade endpoint with the trade ID.
    fo = FakeOandaTrades([_trade("9", "USD_JPY", -1000, -200.0)])
    r = run_stop_loss_pass(oanda_client=fo, caps=TEST_CAPS, dry_run=False, audit_path=audit_path)
    check("8. live mode closes via trade ID", fo.close_calls == ["9"])

    # 9. Threshold falls back to order_caps.json's real configured value
    #    when none is passed explicitly.
    real_caps = load_caps()
    check("9. real order_caps.json has the stop-loss threshold configured",
          real_caps.get(STOP_LOSS_KEY) == 15.0)

    os.remove(audit_path)

    passed = sum(1 for _, ok in results if ok)
    for name, ok in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    print(f"\n  {passed}/{len(results)} passed")
    return passed == len(results)


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--run", action="store_true", help="run a real pass against live paper/practice accounts")
    ap.add_argument("--live", action="store_true", help="actually close breaching positions (default: dry-run report only)")
    a = ap.parse_args()

    if a.self_test:
        print("=== stop_loss_monitor.py SELF-TEST ===")
        ok = self_test()
        raise SystemExit(0 if ok else 1)

    if a.run:
        from alpaca_client import Alpaca
        from oanda_client import Oanda
        results = run_stop_loss_pass(alpaca_client=Alpaca(paper=True), oanda_client=Oanda(practice=True),
                                      dry_run=not a.live)
        if not results:
            print("No positions breaching the stop-loss threshold.")
        for r in results:
            print(r)
        return

    ap.print_help()


if __name__ == "__main__":
    main()
