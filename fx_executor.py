#!/usr/bin/env python3
"""
OANDA (FX) order execution wrapper (G-062 mechanism gate, extended
2026-08-13 to a third venue).

Reuses order_executor.py's caps/dry-run/audit/kill-switch primitives.
OANDA's order shape is `units` (signed: positive = buy base currency /
long, negative = sell base currency / short) rather than a side flag --
this wrapper takes `units` directly rather than translating a side+qty
pair, specifically so the sign convention that determines trade direction
can never get silently flipped by a translation step. The l_cross
carry-unwind expression (long yen / short USD/JPY) is a NEGATIVE units
value on USD_JPY -- get the sign wrong and the position is backwards.

dry_run defaults to True everywhere. A live submission additionally
requires an explicit `client` (a real Oanda() instance, practice=True by
default in oanda_client.py).

DOLLAR-NOTIONAL CAVEAT (bug found and fixed 2026-08-13, before the first
real order): OANDA's `units` are denominated in the PAIR'S BASE currency,
not always USD. For USD_JPY (base=USD), units ARE already USD notional --
multiplying by the JPY rate again overcounts the dollar notional by ~160x,
which would have made a correctly-sized order look like it blew through
the cap by orders of magnitude. This wrapper defaults `usd_is_base=True`
(correct for USD_JPY, the only pair in use so far) and computes
order_dollars = abs(units) directly. For a pair where USD is the QUOTE
currency instead (e.g. EUR_USD, base=EUR), call with usd_is_base=False so
order_dollars = abs(units) * ref_price (units in EUR, converted via the
rate) -- do not add a new pair without setting this deliberately.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from order_executor import (  # noqa: E402
    AUDIT_LOG, CAPS_PATH, check_cap, check_drawdown_halt, kill_switch_active,
    load_caps, new_client_order_id, _audit,
)


def validate_order(instrument, units, ref_price):
    if not instrument or not isinstance(instrument, str):
        return False, f"invalid instrument: {instrument!r}"
    if units == 0:
        return False, "units must be non-zero (positive=buy/long, negative=sell/short)"
    if not (ref_price > 0):
        return False, f"invalid ref_price {ref_price!r}, must be > 0"
    return True, ""


def execute_fx_order(instrument, units, ref_price, claim_class,
                      bankroll_dollars, client_order_id, client=None,
                      dry_run=True, caps=None, audit_path=AUDIT_LOG,
                      usd_is_base=True):
    """The single entry point for OANDA orders. Never call
    Oanda.place_order() directly -- that bypasses cap enforcement.

    usd_is_base: True (default) means `units` are already USD notional
    (correct for USD_JPY). Set False for a pair where USD is the quote
    currency instead -- see module docstring."""
    caps = caps if caps is not None else load_caps()
    if isinstance(units, (int, float)) and isinstance(ref_price, (int, float)):
        order_dollars = abs(units) if usd_is_base else abs(units) * ref_price
    else:
        order_dollars = None

    ok, reason = validate_order(instrument, units, ref_price)
    if not ok:
        _audit({"venue": "oanda", "event": "REJECTED_VALIDATION", "reason": reason,
                "instrument": instrument, "units": units, "ref_price": ref_price,
                "claim_class": claim_class, "client_order_id": client_order_id},
               audit_path)
        return {"status": "rejected", "stage": "validation", "reason": reason}

    if client is not None:
        pl = client.unrealized_pl_dollars()
        dd_ok, dd_reason = check_drawdown_halt(pl, bankroll_dollars, caps)
        if not dd_ok:
            _audit({"venue": "oanda", "event": "REJECTED_DRAWDOWN_HALT", "reason": dd_reason,
                    "instrument": instrument, "units": units, "ref_price": ref_price,
                    "unrealized_pl_dollars": pl, "bankroll_dollars": bankroll_dollars,
                    "claim_class": claim_class, "client_order_id": client_order_id},
                   audit_path)
            return {"status": "rejected", "stage": "drawdown_halt", "reason": dd_reason}

    cap_ok, cap_reason = check_cap(claim_class, order_dollars, bankroll_dollars, caps)
    if not cap_ok:
        _audit({"venue": "oanda", "event": "REJECTED_CAP", "reason": cap_reason,
                "instrument": instrument, "units": units, "ref_price": ref_price,
                "order_dollars": order_dollars, "bankroll_dollars": bankroll_dollars,
                "claim_class": claim_class, "client_order_id": client_order_id},
               audit_path)
        return {"status": "rejected", "stage": "cap", "reason": cap_reason}

    forced_dry_run = (not dry_run) and kill_switch_active()
    effective_dry_run = dry_run or forced_dry_run

    direction = "long/buy" if units > 0 else "short/sell"
    intent = {"venue": "oanda", "instrument": instrument, "units": units,
              "direction": direction, "ref_price": ref_price,
              "order_dollars": order_dollars, "claim_class": claim_class,
              "client_order_id": client_order_id, "cap_check": cap_reason}

    if effective_dry_run:
        _audit({"event": "DRY_RUN", "forced_by_kill_switch": forced_dry_run, **intent},
               audit_path)
        return {"status": "dry_run", "would_submit": intent,
                "forced_by_kill_switch": forced_dry_run}

    if client is None:
        raise ValueError("live submission (dry_run=False) requires a real "
                          "Oanda client -- refusing to guess one")

    try:
        resp = client.place_order(instrument, units, client_order_id=client_order_id)
        order_fill = (resp or {}).get("orderFillTransaction", {})
        _audit({"event": "SUBMITTED", "response_order_id": order_fill.get("id"),
                **intent}, audit_path)
        return {"status": "submitted", "response": resp}
    except Exception as e:
        _audit({"event": "ERROR", "error": str(e), **intent}, audit_path)
        return {"status": "error", "error": str(e)}


# --------------------------------------------------------------------------
# Self-test -- same FakeClient/temp-audit-log pattern as the other two
# executors. No network, no credentials.
# --------------------------------------------------------------------------

class FakeOanda:
    def __init__(self, raise_on_call=False, unrealized_pl=0.0):
        self.calls = []
        self.raise_on_call = raise_on_call
        self._unrealized_pl = unrealized_pl

    def unrealized_pl_dollars(self):
        return self._unrealized_pl

    def place_order(self, instrument, units, client_order_id=None):
        if self.raise_on_call:
            raise RuntimeError("simulated network/API failure")
        self.calls.append(dict(instrument=instrument, units=units,
                                client_order_id=client_order_id))
        return {"orderFillTransaction": {"id": f"fake-{len(self.calls)}"}}


TEST_CAPS = {
    "_absolute_ceiling_pct": 10.0,
    "_aggregate_drawdown_halt_pct": 10.0,
    "claim_classes": {
        "jpy_carry_unwind": {"max_pct_of_bankroll": 2.0, "note": "test"},
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
    fk = FakeOanda()
    r = execute_fx_order("USD_JPY", -100000, 159.5, "jpy_carry_unwind",
                          bankroll_dollars=1000, client_order_id=new_client_order_id(),
                          client=fk, dry_run=False, caps=TEST_CAPS, audit_path=audit_path)
    check("1a. over-cap order rejected", r["status"] == "rejected" and r["stage"] == "cap")
    check("1b. client never called on cap rejection", len(fk.calls) == 0)
    check("1c. cap rejection audited", audit_line_count() == 1)

    # 2. Unknown claim_class refused (fail closed).
    r = execute_fx_order("EUR_USD", -1000, 1.08, "totally_unlisted_claim",
                          bankroll_dollars=1000, client_order_id=new_client_order_id(),
                          client=None, dry_run=True, caps=TEST_CAPS, audit_path=audit_path)
    check("2. unknown claim_class refused, not defaulted", r["status"] == "rejected")

    # 3-4. Malformed input rejected.
    for label, args in [
        ("3. zero units rejected", dict(units=0, ref_price=159.5)),
        ("4. zero ref_price rejected", dict(units=-100, ref_price=0)),
    ]:
        r = execute_fx_order("USD_JPY", args["units"], args["ref_price"],
                              "jpy_carry_unwind", bankroll_dollars=1000,
                              client_order_id=new_client_order_id(), client=None,
                              dry_run=True, caps=TEST_CAPS, audit_path=audit_path)
        check(label, r["status"] == "rejected" and r["stage"] == "validation")

    # 5. Direction sign preserved end to end -- a short (negative units) stays
    #    negative all the way to the client call. This is the one bug class
    #    this wrapper exists specifically to prevent.
    fk = FakeOanda()
    coid = new_client_order_id()
    r = execute_fx_order("USD_JPY", -50, 159.5, "jpy_carry_unwind",
                          bankroll_dollars=10000, client_order_id=coid,
                          client=fk, dry_run=False, caps=TEST_CAPS, audit_path=audit_path)
    check("5a. live submission calls client once", len(fk.calls) == 1)
    check("5b. negative (short) units preserved unchanged to the client call",
          fk.calls and fk.calls[0]["units"] == -50)
    check("5c. client_order_id forwarded unchanged", fk.calls and fk.calls[0]["client_order_id"] == coid)
    check("5d. submission audited", r["status"] == "submitted")

    # 6. Dry-run never calls the client, works with client=None.
    fk = FakeOanda()
    r = execute_fx_order("USD_JPY", -50, 159.5, "jpy_carry_unwind",
                          bankroll_dollars=10000, client_order_id=new_client_order_id(),
                          client=fk, caps=TEST_CAPS, audit_path=audit_path)
    check("6a. dry-run does not call client.place_order", len(fk.calls) == 0)
    r2 = execute_fx_order("USD_JPY", -50, 159.5, "jpy_carry_unwind",
                           bankroll_dollars=10000, client_order_id=new_client_order_id(),
                           client=None, caps=TEST_CAPS, audit_path=audit_path)
    check("6b. dry-run needs no client at all", r2["status"] == "dry_run")

    # 7. Kill switch forces dry-run even when a live submission was requested.
    from order_executor import KILL_SWITCH_PATH
    open(KILL_SWITCH_PATH, "w").close()
    try:
        fk = FakeOanda()
        r = execute_fx_order("USD_JPY", -50, 159.5, "jpy_carry_unwind",
                              bankroll_dollars=10000, client_order_id=new_client_order_id(),
                              client=fk, dry_run=False, caps=TEST_CAPS, audit_path=audit_path)
        check("7a. kill switch blocks a live submission", len(fk.calls) == 0)
        check("7b. kill switch result reports status dry_run", r["status"] == "dry_run")
    finally:
        os.remove(KILL_SWITCH_PATH)

    # 8. Client exception caught and reported, not raised.
    fk = FakeOanda(raise_on_call=True)
    r = execute_fx_order("USD_JPY", -50, 159.5, "jpy_carry_unwind",
                          bankroll_dollars=10000, client_order_id=new_client_order_id(),
                          client=fk, dry_run=False, caps=TEST_CAPS, audit_path=audit_path)
    check("8. client exception caught and reported as status=error", r["status"] == "error")

    # 9. Aggregate drawdown halt: book already down 10%+ of bankroll blocks
    #    a brand new order that would otherwise be well within its own cap.
    fk = FakeOanda(unrealized_pl=-1000.0)  # -10% of a 10000 bankroll
    r = execute_fx_order("USD_JPY", -50, 159.5, "jpy_carry_unwind",
                          bankroll_dollars=10000, client_order_id=new_client_order_id(),
                          client=fk, dry_run=False, caps=TEST_CAPS, audit_path=audit_path)
    check("9a. drawdown halt blocks a new order despite cap headroom",
          r["status"] == "rejected" and r["stage"] == "drawdown_halt")
    check("9b. client never called when drawdown-halted", len(fk.calls) == 0)

    # 10. Below the halt threshold: order proceeds normally despite being
    #     in a loss.
    fk = FakeOanda(unrealized_pl=-400.0)  # -4% of a 10000 bankroll, under 10% halt
    r = execute_fx_order("USD_JPY", -50, 159.5, "jpy_carry_unwind",
                          bankroll_dollars=10000, client_order_id=new_client_order_id(),
                          client=fk, dry_run=False, caps=TEST_CAPS, audit_path=audit_path)
    check("10. sub-threshold drawdown does not block a new order", r["status"] == "submitted")

    # 11. No client (pure dry-run demo) skips the drawdown check entirely.
    r = execute_fx_order("USD_JPY", -50, 159.5, "jpy_carry_unwind",
                          bankroll_dollars=10000, client_order_id=new_client_order_id(),
                          client=None, caps=TEST_CAPS, audit_path=audit_path)
    check("11. drawdown check skipped cleanly when client=None", r["status"] == "dry_run")

    os.remove(audit_path)

    passed = sum(1 for _, ok in results if ok)
    for name, ok in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    print(f"\n  {passed}/{len(results)} passed")
    return passed == len(results)


if __name__ == "__main__":
    print("=== fx_executor.py SELF-TEST ===")
    ok = self_test()
    raise SystemExit(0 if ok else 1)
