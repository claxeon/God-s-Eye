#!/usr/bin/env python3
"""
Alpaca (equities/ETF) order execution wrapper (G-062 mechanism gate,
extended 2026-08-13 to a second venue).

Reuses order_executor.py's caps/dry-run/audit/kill-switch primitives
rather than re-deriving them -- same validation-first, fail-closed-on-
unknown-claim_class, audit-everything discipline as the Kalshi wrapper.
The only venue-specific piece is the order shape: Alpaca trades symbol +
side (buy/sell) + qty, not Kalshi's ticker + side (bid/ask) + count +
price. Market orders have no price at submission time, so the caller
supplies a reference price (last quote/close) for the cap check -- this
function does not fetch one itself, keeping it pure and testable.

dry_run defaults to True everywhere. A live submission additionally
requires an explicit `client` (a real Alpaca() instance, paper=True by
default in alpaca_client.py) -- a dry run never needs credentials to
exist at all.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from order_executor import (  # noqa: E402
    AUDIT_LOG, CAPS_PATH, check_cap, check_drawdown_halt, kill_switch_active,
    load_caps, new_client_order_id, _audit,
)

VALID_SIDES = {"buy", "sell"}


def validate_order(symbol, side, qty, ref_price):
    if not symbol or not isinstance(symbol, str):
        return False, f"invalid symbol: {symbol!r}"
    if side not in VALID_SIDES:
        return False, f"invalid side {side!r}, must be one of {VALID_SIDES}"
    if not (qty > 0):
        return False, f"invalid qty {qty!r}, must be > 0"
    if not (ref_price > 0):
        return False, f"invalid ref_price {ref_price!r}, must be > 0"
    return True, ""


def execute_equity_order(symbol, side, qty, ref_price, claim_class,
                          bankroll_dollars, client_order_id, client=None,
                          dry_run=True, caps=None, audit_path=AUDIT_LOG):
    """The single entry point for Alpaca orders. Never call
    Alpaca.place_order() directly -- that bypasses cap enforcement."""
    caps = caps if caps is not None else load_caps()
    order_dollars = qty * ref_price if isinstance(qty, (int, float)) and isinstance(ref_price, (int, float)) else None

    ok, reason = validate_order(symbol, side, qty, ref_price)
    if not ok:
        _audit({"venue": "alpaca", "event": "REJECTED_VALIDATION", "reason": reason,
                "symbol": symbol, "side": side, "qty": qty, "ref_price": ref_price,
                "claim_class": claim_class, "client_order_id": client_order_id},
               audit_path)
        return {"status": "rejected", "stage": "validation", "reason": reason}

    if client is not None:
        pl = client.unrealized_pl_dollars()
        dd_ok, dd_reason = check_drawdown_halt(pl, bankroll_dollars, caps)
        if not dd_ok:
            _audit({"venue": "alpaca", "event": "REJECTED_DRAWDOWN_HALT", "reason": dd_reason,
                    "symbol": symbol, "side": side, "qty": qty, "ref_price": ref_price,
                    "unrealized_pl_dollars": pl, "bankroll_dollars": bankroll_dollars,
                    "claim_class": claim_class, "client_order_id": client_order_id},
                   audit_path)
            return {"status": "rejected", "stage": "drawdown_halt", "reason": dd_reason}

    cap_ok, cap_reason = check_cap(claim_class, order_dollars, bankroll_dollars, caps)
    if not cap_ok:
        _audit({"venue": "alpaca", "event": "REJECTED_CAP", "reason": cap_reason,
                "symbol": symbol, "side": side, "qty": qty, "ref_price": ref_price,
                "order_dollars": order_dollars, "bankroll_dollars": bankroll_dollars,
                "claim_class": claim_class, "client_order_id": client_order_id},
               audit_path)
        return {"status": "rejected", "stage": "cap", "reason": cap_reason}

    forced_dry_run = (not dry_run) and kill_switch_active()
    effective_dry_run = dry_run or forced_dry_run

    intent = {"venue": "alpaca", "symbol": symbol, "side": side, "qty": qty,
              "ref_price": ref_price, "order_dollars": order_dollars,
              "claim_class": claim_class, "client_order_id": client_order_id,
              "cap_check": cap_reason}

    if effective_dry_run:
        _audit({"event": "DRY_RUN", "forced_by_kill_switch": forced_dry_run, **intent},
               audit_path)
        return {"status": "dry_run", "would_submit": intent,
                "forced_by_kill_switch": forced_dry_run}

    if client is None:
        raise ValueError("live submission (dry_run=False) requires a real "
                          "Alpaca client -- refusing to guess one")

    try:
        resp = client.place_order(symbol, side, qty, client_order_id=client_order_id)
        _audit({"event": "SUBMITTED", "response_order_id": (resp or {}).get("id"),
                **intent}, audit_path)
        return {"status": "submitted", "response": resp}
    except Exception as e:
        _audit({"event": "ERROR", "error": str(e), **intent}, audit_path)
        return {"status": "error", "error": str(e)}


# --------------------------------------------------------------------------
# Self-test -- same FakeClient/temp-audit-log pattern as order_executor.py.
# No network, no credentials, real order_caps.json is NOT required to
# contain equity claim classes yet (see TEST_CAPS) -- this proves the code
# path, not the real position-size policy, which is a separate decision.
# --------------------------------------------------------------------------

class FakeAlpaca:
    def __init__(self, raise_on_call=False, unrealized_pl=0.0):
        self.calls = []
        self.raise_on_call = raise_on_call
        self._unrealized_pl = unrealized_pl

    def unrealized_pl_dollars(self):
        return self._unrealized_pl

    def place_order(self, symbol, side, qty, client_order_id=None):
        if self.raise_on_call:
            raise RuntimeError("simulated network/API failure")
        self.calls.append(dict(symbol=symbol, side=side, qty=qty,
                                client_order_id=client_order_id))
        return {"id": f"fake-{len(self.calls)}", "client_order_id": client_order_id,
                "status": "accepted"}


TEST_CAPS = {
    "_absolute_ceiling_pct": 10.0,
    "_aggregate_drawdown_halt_pct": 10.0,
    "claim_classes": {
        "gold_reserve_diversification": {"max_pct_of_bankroll": 3.0, "note": "test"},
    },
}


def self_test():
    import tempfile
    results = []

    def check(name, cond):
        results.append((name, bool(cond)))

    audit_fd, audit_path = tempfile.mkstemp(suffix=".jsonl")
    os.close(audit_fd)
    os.remove(audit_path)

    def audit_line_count():
        if not os.path.exists(audit_path):
            return 0
        with open(audit_path) as f:
            return sum(1 for _ in f)

    # 1. Over-cap order rejected, client never called.
    fk = FakeAlpaca()
    r = execute_equity_order("GLD", "buy", 1000, 250.0, "gold_reserve_diversification",
                              bankroll_dollars=1000, client_order_id=new_client_order_id(),
                              client=fk, dry_run=False, caps=TEST_CAPS, audit_path=audit_path)
    check("1a. over-cap order rejected", r["status"] == "rejected" and r["stage"] == "cap")
    check("1b. client never called on cap rejection", len(fk.calls) == 0)
    check("1c. cap rejection audited", audit_line_count() == 1)

    # 2. Unknown claim_class refused (fail closed).
    r = execute_equity_order("XLE", "buy", 1, 90.0, "totally_unlisted_claim",
                              bankroll_dollars=1000, client_order_id=new_client_order_id(),
                              client=None, dry_run=True, caps=TEST_CAPS, audit_path=audit_path)
    check("2. unknown claim_class refused, not defaulted", r["status"] == "rejected")

    # 3-5. Malformed input rejected.
    for label, args in [
        ("3. negative qty rejected", dict(qty=-5, side="buy", ref_price=250.0)),
        ("4. zero ref_price rejected", dict(qty=5, side="buy", ref_price=0)),
        ("5. invalid side rejected", dict(qty=5, side="short", ref_price=250.0)),
    ]:
        r = execute_equity_order("GLD", args["side"], args["qty"], args["ref_price"],
                                  "gold_reserve_diversification", bankroll_dollars=1000,
                                  client_order_id=new_client_order_id(), client=None,
                                  dry_run=True, caps=TEST_CAPS, audit_path=audit_path)
        check(label, r["status"] == "rejected" and r["stage"] == "validation")

    # 6. Dry-run (default) never calls the client, even for a valid in-cap order.
    fk = FakeAlpaca()
    r = execute_equity_order("GLD", "buy", 1, 250.0, "gold_reserve_diversification",
                              bankroll_dollars=10000, client_order_id=new_client_order_id(),
                              client=fk, caps=TEST_CAPS, audit_path=audit_path)
    check("6a. dry-run does not call client.place_order", len(fk.calls) == 0)
    check("6b. dry-run needs no client at all",
          execute_equity_order("GLD", "buy", 1, 250.0, "gold_reserve_diversification",
                                bankroll_dollars=10000, client_order_id=new_client_order_id(),
                                client=None, caps=TEST_CAPS, audit_path=audit_path)["status"] == "dry_run")

    # 7. Live mode calls the client with the caller's client_order_id forwarded.
    fk = FakeAlpaca()
    coid = new_client_order_id()
    r = execute_equity_order("GLD", "buy", 1, 250.0, "gold_reserve_diversification",
                              bankroll_dollars=10000, client_order_id=coid,
                              client=fk, dry_run=False, caps=TEST_CAPS, audit_path=audit_path)
    check("7a. live submission calls client once", len(fk.calls) == 1)
    check("7b. client_order_id forwarded unchanged", fk.calls and fk.calls[0]["client_order_id"] == coid)
    check("7c. submission audited", r["status"] == "submitted")

    # 8. Kill switch forces dry-run even when a live submission was requested.
    from order_executor import KILL_SWITCH_PATH
    open(KILL_SWITCH_PATH, "w").close()
    try:
        fk = FakeAlpaca()
        r = execute_equity_order("GLD", "buy", 1, 250.0, "gold_reserve_diversification",
                                  bankroll_dollars=10000, client_order_id=new_client_order_id(),
                                  client=fk, dry_run=False, caps=TEST_CAPS, audit_path=audit_path)
        check("8a. kill switch blocks a live submission", len(fk.calls) == 0)
        check("8b. kill switch result reports status dry_run", r["status"] == "dry_run")
    finally:
        os.remove(KILL_SWITCH_PATH)

    # 9. Client exception caught and reported, not raised.
    fk = FakeAlpaca(raise_on_call=True)
    r = execute_equity_order("GLD", "buy", 1, 250.0, "gold_reserve_diversification",
                              bankroll_dollars=10000, client_order_id=new_client_order_id(),
                              client=fk, dry_run=False, caps=TEST_CAPS, audit_path=audit_path)
    check("9. client exception caught and reported as status=error", r["status"] == "error")

    # 10. Aggregate drawdown halt: book already down 10%+ of bankroll blocks
    #     a brand new order that would otherwise be well within its own cap.
    fk = FakeAlpaca(unrealized_pl=-1000.0)  # -10% of a 10000 bankroll
    r = execute_equity_order("GLD", "buy", 1, 250.0, "gold_reserve_diversification",
                              bankroll_dollars=10000, client_order_id=new_client_order_id(),
                              client=fk, dry_run=False, caps=TEST_CAPS, audit_path=audit_path)
    check("10a. drawdown halt blocks a new order despite cap headroom",
          r["status"] == "rejected" and r["stage"] == "drawdown_halt")
    check("10b. client never called when drawdown-halted", len(fk.calls) == 0)

    # 11. Below the halt threshold: order proceeds normally despite being
    #     in a loss.
    fk = FakeAlpaca(unrealized_pl=-400.0)  # -4% of a 10000 bankroll, under 10% halt
    r = execute_equity_order("GLD", "buy", 1, 250.0, "gold_reserve_diversification",
                              bankroll_dollars=10000, client_order_id=new_client_order_id(),
                              client=fk, dry_run=False, caps=TEST_CAPS, audit_path=audit_path)
    check("11. sub-threshold drawdown does not block a new order", r["status"] == "submitted")

    # 12. No client (pure dry-run demo) skips the drawdown check entirely --
    #     dry-run must still work without any account to query.
    r = execute_equity_order("GLD", "buy", 1, 250.0, "gold_reserve_diversification",
                              bankroll_dollars=10000, client_order_id=new_client_order_id(),
                              client=None, caps=TEST_CAPS, audit_path=audit_path)
    check("12. drawdown check skipped cleanly when client=None", r["status"] == "dry_run")

    os.remove(audit_path)

    passed = sum(1 for _, ok in results if ok)
    for name, ok in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    print(f"\n  {passed}/{len(results)} passed")
    return passed == len(results)


if __name__ == "__main__":
    print("=== equity_executor.py SELF-TEST ===")
    ok = self_test()
    raise SystemExit(0 if ok else 1)
