#!/usr/bin/env python3
"""
Kalshi order execution wrapper (G-062 mechanism gate, 2026-08-12).

kalshi_client.py's place_order() is real and functional but has never been
exercised through any tested, repeatable path -- the one real fill on record
(order 75397a29, 2026-07-13) was a direct manual call. Before any autonomy is
granted, the MECHANISM needs its own evidence, separate from the edge gate
(paper ledger, >=10 graded resolutions) -- same discipline as the RSI ladder's
own correction rule: mechanism existing != mechanism demonstrated.

This wrapper is the mechanism under test. It adds, in front of every call to
Kalshi.place_order():
  1. Input validation (side/count/price sanity) -- fail closed.
  2. Cap enforcement in CODE against order_caps.json, not prose a human has
     to remember to check. An unlisted claim_class is refused, not
     defaulted to the absolute ceiling.
  3. A kill switch: if Scripts/.trading_disabled exists, every call is
     forced into dry-run regardless of what was requested.
  4. An audit log line for every attempt -- accepted, rejected, dry-run, or
     errored -- because "verify at source" applies to money more than
     anything else in this vault.
  5. Idempotent retries: the caller generates ONE client_order_id per
     logical order (new_client_order_id()) and reuses it on every retry, so
     a retry after an unknown-outcome timeout cannot double-fill. This
     required a matching change to kalshi_client.place_order() to accept a
     caller-supplied id instead of always minting a fresh uuid4 internally.

dry_run defaults to True everywhere. A live submission additionally requires
an explicit `client` (a real Kalshi() instance) -- so a dry run, including
every self-test case below, never needs Kalshi credentials to exist at all.

Usage:
    python3 order_executor.py --self-test
"""
import argparse
import json
import os
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
CAPS_PATH = os.path.join(HERE, "order_caps.json")
AUDIT_LOG = os.path.join(HERE, "..", "Logs", "order_audit.jsonl")
KILL_SWITCH_PATH = os.path.join(HERE, ".trading_disabled")

VALID_SIDES = {"bid", "ask"}


def new_client_order_id():
    """Call ONCE per logical order; pass the same value to every retry."""
    return str(uuid.uuid4())


def kill_switch_active(path=KILL_SWITCH_PATH):
    return os.path.exists(path)


def load_caps(path=CAPS_PATH):
    with open(path) as f:
        return json.load(f)


def validate_order(ticker, side, count, price_dollars):
    if not ticker or not isinstance(ticker, str):
        return False, f"invalid ticker: {ticker!r}"
    if side not in VALID_SIDES:
        return False, f"invalid side {side!r}, must be one of {VALID_SIDES}"
    if not (count > 0):
        return False, f"invalid count {count!r}, must be > 0"
    if not (0 < price_dollars < 1):
        return False, f"invalid price_dollars {price_dollars!r}, must be in (0, 1)"
    return True, ""


def check_cap(claim_class, order_dollars, bankroll_dollars, caps):
    if bankroll_dollars <= 0:
        return False, f"invalid bankroll_dollars {bankroll_dollars!r}"
    entry = caps.get("claim_classes", {}).get(claim_class)
    if entry is None:
        return False, (f"unknown claim_class {claim_class!r} -- refusing "
                        f"(fail closed; add it to order_caps.json deliberately first)")
    class_cap_pct = entry["max_pct_of_bankroll"]
    abs_cap_pct = caps["_absolute_ceiling_pct"]
    effective_cap_pct = min(class_cap_pct, abs_cap_pct)
    order_pct = (order_dollars / bankroll_dollars) * 100
    if order_pct > effective_cap_pct + 1e-9:
        return False, (f"order is {order_pct:.3f}% of bankroll, exceeds "
                        f"effective cap {effective_cap_pct:.3f}% "
                        f"(class cap {class_cap_pct}%, absolute ceiling {abs_cap_pct}%)")
    return True, f"{order_pct:.3f}% of bankroll, within {effective_cap_pct:.3f}% cap"


def check_drawdown_halt(unrealized_pl_dollars, bankroll_dollars, caps):
    """Aggregate drawdown circuit breaker (2026-08-13). Distinct from
    check_cap(): that limits how much a SINGLE order can add; this limits
    whether ANY new order is allowed at all once the book is already
    bleeding. Checked per venue (each venue's own unrealized P&L), not
    combined across venues -- true cross-venue aggregation isn't built.

    unrealized_pl_dollars: signed float, current aggregate unrealized P&L
    across the venue's open positions (negative = loss). Caller fetches
    this from the venue (Alpaca.unrealized_pl_dollars() /
    Oanda.unrealized_pl_dollars()) -- this function stays pure/testable,
    same as check_cap().

    Does NOT close any open position -- it only blocks new orders. A
    breached halt with existing open losing positions still sitting open
    is a known, accepted gap: per-position stop-loss is scoped (15-20% DD
    per position, per the 2026-08-13 decision) but not implemented."""
    if bankroll_dollars <= 0:
        return False, f"invalid bankroll_dollars {bankroll_dollars!r}"
    halt_pct = caps.get("_aggregate_drawdown_halt_pct")
    if halt_pct is None:
        return True, "no aggregate drawdown halt configured"
    if unrealized_pl_dollars >= 0:
        return True, f"book is flat/positive (unrealized P&L {unrealized_pl_dollars:+.2f})"
    drawdown_pct = abs(unrealized_pl_dollars) / bankroll_dollars * 100
    if drawdown_pct >= halt_pct:
        return False, (f"aggregate drawdown {drawdown_pct:.3f}% of bankroll "
                        f"(unrealized P&L {unrealized_pl_dollars:+.2f}) has reached "
                        f"the {halt_pct:.3f}% halt threshold -- ALL new orders on "
                        f"this venue refused until it recovers or positions are "
                        f"closed manually")
    return True, f"aggregate drawdown {drawdown_pct:.3f}%, below {halt_pct:.3f}% halt"


def _audit(entry, audit_path=AUDIT_LOG):
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **entry}
    os.makedirs(os.path.dirname(audit_path), exist_ok=True)
    with open(audit_path, "a") as f:
        f.write(json.dumps(entry) + "\n")


def execute_order(ticker, side, count, price_dollars, claim_class,
                   bankroll_dollars, client_order_id, client=None,
                   dry_run=True, caps=None, audit_path=AUDIT_LOG):
    """The single entry point. Never bypass this to call
    Kalshi.place_order() directly -- that's exactly the ad hoc path that
    left the mechanism unproven."""
    caps = caps if caps is not None else load_caps()
    order_dollars = count * price_dollars if isinstance(count, (int, float)) and isinstance(price_dollars, (int, float)) else None

    ok, reason = validate_order(ticker, side, count, price_dollars)
    if not ok:
        _audit({"event": "REJECTED_VALIDATION", "reason": reason, "ticker": ticker,
                "side": side, "count": count, "price_dollars": price_dollars,
                "claim_class": claim_class, "client_order_id": client_order_id},
               audit_path)
        return {"status": "rejected", "stage": "validation", "reason": reason}

    cap_ok, cap_reason = check_cap(claim_class, order_dollars, bankroll_dollars, caps)
    if not cap_ok:
        _audit({"event": "REJECTED_CAP", "reason": cap_reason, "ticker": ticker,
                "side": side, "count": count, "price_dollars": price_dollars,
                "order_dollars": order_dollars, "bankroll_dollars": bankroll_dollars,
                "claim_class": claim_class, "client_order_id": client_order_id},
               audit_path)
        return {"status": "rejected", "stage": "cap", "reason": cap_reason}

    forced_dry_run = (not dry_run) and kill_switch_active()
    effective_dry_run = dry_run or forced_dry_run

    intent = {"ticker": ticker, "side": side, "count": count,
              "price_dollars": price_dollars, "order_dollars": order_dollars,
              "claim_class": claim_class, "client_order_id": client_order_id,
              "cap_check": cap_reason}

    if effective_dry_run:
        _audit({"event": "DRY_RUN", "forced_by_kill_switch": forced_dry_run, **intent},
               audit_path)
        return {"status": "dry_run", "would_submit": intent,
                "forced_by_kill_switch": forced_dry_run}

    if client is None:
        raise ValueError("live submission (dry_run=False) requires a real "
                          "Kalshi client -- refusing to guess one")

    try:
        resp = client.place_order(ticker, side, count, price_dollars,
                                   client_order_id=client_order_id)
        _audit({"event": "SUBMITTED", "response_order_id":
                (resp or {}).get("order", {}).get("order_id"), **intent}, audit_path)
        return {"status": "submitted", "response": resp}
    except Exception as e:
        _audit({"event": "ERROR", "error": str(e), **intent}, audit_path)
        return {"status": "error", "error": str(e)}


# --------------------------------------------------------------------------
# Self-test (repo convention: liveness_check.py / protocol_check.py both use
# a --self-test flag rather than a separate pytest suite). All cases use a
# FakeKalshi and a temp audit log -- no network, no credentials, no real
# .env or .pem file is ever touched.
# --------------------------------------------------------------------------

class FakeKalshi:
    def __init__(self, raise_on_call=False):
        self.calls = []
        self.raise_on_call = raise_on_call

    def place_order(self, ticker, side, count, price_dollars,
                     tif="immediate_or_cancel", client_order_id=None):
        if self.raise_on_call:
            raise RuntimeError("simulated network/API failure")
        self.calls.append(dict(ticker=ticker, side=side, count=count,
                                price_dollars=price_dollars, tif=tif,
                                client_order_id=client_order_id))
        return {"order": {"order_id": f"fake-{len(self.calls)}",
                           "client_order_id": client_order_id, "status": "resting"}}


TEST_CAPS = {
    "_absolute_ceiling_pct": 10.0,
    "claim_classes": {
        "hormuz_normal_dec31": {"max_pct_of_bankroll": 4.0, "note": "test"},
        "misconfigured_over_ceiling": {"max_pct_of_bankroll": 15.0, "note": "test: class cap above the absolute ceiling on purpose"},
    },
}


def self_test():
    import tempfile
    results = []

    def check(name, cond):
        results.append((name, bool(cond)))

    audit_fd, audit_path = tempfile.mkstemp(suffix=".jsonl")
    os.close(audit_fd)
    os.remove(audit_path)  # start clean; execute_order creates it via makedirs+append

    def audit_line_count():
        if not os.path.exists(audit_path):
            return 0
        with open(audit_path) as f:
            return sum(1 for _ in f)

    # 1. Cap violation blocked, client never called.
    fk = FakeKalshi()
    r = execute_order("KXHORMUZAVG-A100", "bid", 1000, 0.30, "hormuz_normal_dec31",
                       bankroll_dollars=1000, client_order_id=new_client_order_id(),
                       client=fk, dry_run=False, caps=TEST_CAPS, audit_path=audit_path)
    check("1a. over-cap order rejected", r["status"] == "rejected" and r["stage"] == "cap")
    check("1b. client never called on cap rejection", len(fk.calls) == 0)
    check("1c. cap rejection was audited", audit_line_count() == 1)

    # 2. Absolute 10% ceiling overrides a misconfigured 15%-class cap.
    fk = FakeKalshi()
    r = execute_order("XYZ", "bid", 130, 0.90, "misconfigured_over_ceiling",
                       bankroll_dollars=1000, client_order_id=new_client_order_id(),
                       client=fk, dry_run=False, caps=TEST_CAPS, audit_path=audit_path)
    check("2. absolute ceiling wins over a misconfigured class cap", r["status"] == "rejected")

    # 3. Unknown claim_class refused (fail closed).
    r = execute_order("XYZ", "bid", 1, 0.30, "totally_unlisted_claim",
                       bankroll_dollars=1000, client_order_id=new_client_order_id(),
                       client=None, dry_run=True, caps=TEST_CAPS, audit_path=audit_path)
    check("3. unknown claim_class refused, not defaulted", r["status"] == "rejected")

    # 4-6. Malformed input rejected before any cap/client logic runs.
    for label, args in [
        ("4. negative count rejected", dict(count=-5, side="bid", price_dollars=0.3)),
        ("5. out-of-range price rejected", dict(count=5, side="bid", price_dollars=1.5)),
        ("6. invalid side rejected", dict(count=5, side="buy", price_dollars=0.3)),
    ]:
        r = execute_order("XYZ", args["side"], args["count"], args["price_dollars"],
                           "hormuz_normal_dec31", bankroll_dollars=1000,
                           client_order_id=new_client_order_id(), client=None,
                           dry_run=True, caps=TEST_CAPS, audit_path=audit_path)
        check(label, r["status"] == "rejected" and r["stage"] == "validation")

    # 7. Dry-run mode (default) never calls the client, even for a valid, in-cap order.
    fk = FakeKalshi()
    r = execute_order("KXHORMUZAVG-A100", "bid", 10, 0.30, "hormuz_normal_dec31",
                       bankroll_dollars=1000, client_order_id=new_client_order_id(),
                       client=fk, caps=TEST_CAPS, audit_path=audit_path)  # dry_run defaults True
    check("7a. dry-run does not call client.place_order", len(fk.calls) == 0)
    check("7b. dry-run reports status dry_run", r["status"] == "dry_run")

    # 7c. Dry-run works with client=None entirely -- no credentials needed for a demo.
    r = execute_order("KXHORMUZAVG-A100", "bid", 10, 0.30, "hormuz_normal_dec31",
                       bankroll_dollars=1000, client_order_id=new_client_order_id(),
                       client=None, caps=TEST_CAPS, audit_path=audit_path)
    check("7c. dry-run needs no client at all", r["status"] == "dry_run")

    # 8. Live mode DOES call the client, with the caller's client_order_id forwarded.
    fk = FakeKalshi()
    coid = new_client_order_id()
    r = execute_order("KXHORMUZAVG-A100", "bid", 10, 0.30, "hormuz_normal_dec31",
                       bankroll_dollars=1000, client_order_id=coid,
                       client=fk, dry_run=False, caps=TEST_CAPS, audit_path=audit_path)
    check("8a. live submission calls client once", len(fk.calls) == 1)
    check("8b. client_order_id forwarded unchanged", fk.calls and fk.calls[0]["client_order_id"] == coid)
    check("8c. submission audited", r["status"] == "submitted")

    # 9. Kill switch forces dry-run even when the caller asked for a live submission.
    open(KILL_SWITCH_PATH, "w").close()
    try:
        fk = FakeKalshi()
        r = execute_order("KXHORMUZAVG-A100", "bid", 10, 0.30, "hormuz_normal_dec31",
                           bankroll_dollars=1000, client_order_id=new_client_order_id(),
                           client=fk, dry_run=False, caps=TEST_CAPS, audit_path=audit_path)
        check("9a. kill switch blocks a live submission", len(fk.calls) == 0)
        check("9b. kill switch result reports status dry_run", r["status"] == "dry_run")
        check("9c. kill switch flagged in the result", r.get("forced_by_kill_switch") is True)
    finally:
        os.remove(KILL_SWITCH_PATH)

    # 10. Idempotent retry: same client_order_id reused across two live attempts
    #     (simulating a caller retrying after an unknown-outcome timeout) reaches
    #     Kalshi with the SAME id both times -- Kalshi's own dedupe can catch it.
    fk = FakeKalshi()
    coid = new_client_order_id()
    execute_order("KXHORMUZAVG-A100", "bid", 10, 0.30, "hormuz_normal_dec31",
                  bankroll_dollars=1000, client_order_id=coid, client=fk,
                  dry_run=False, caps=TEST_CAPS, audit_path=audit_path)
    execute_order("KXHORMUZAVG-A100", "bid", 10, 0.30, "hormuz_normal_dec31",
                  bankroll_dollars=1000, client_order_id=coid, client=fk,
                  dry_run=False, caps=TEST_CAPS, audit_path=audit_path)
    check("10. retry with same client_order_id sends the same id both times",
          len(fk.calls) == 2 and fk.calls[0]["client_order_id"] == fk.calls[1]["client_order_id"] == coid)

    # 11. A client-side error (network/API failure) is caught and audited, not raised.
    fk = FakeKalshi(raise_on_call=True)
    r = execute_order("KXHORMUZAVG-A100", "bid", 10, 0.30, "hormuz_normal_dec31",
                       bankroll_dollars=1000, client_order_id=new_client_order_id(),
                       client=fk, dry_run=False, caps=TEST_CAPS, audit_path=audit_path)
    check("11. client exception caught and reported as status=error", r["status"] == "error")

    # 12. real order_caps.json (not TEST_CAPS) loads and both real claim classes
    #     are enforceable -- catches a typo/schema drift in the actual policy file.
    real_caps = load_caps()
    for cc in ("hormuz_normal_dec31", "recession_2026"):
        r = execute_order("X", "bid", 1000, 0.90, cc, bankroll_dollars=1000,
                           client_order_id=new_client_order_id(), client=None,
                           dry_run=True, caps=real_caps, audit_path=audit_path)
        check(f"12. real order_caps.json enforces {cc}", r["status"] == "rejected" and r["stage"] == "cap")

    os.remove(audit_path)

    passed = sum(1 for _, ok in results if ok)
    for name, ok in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    print(f"\n  {passed}/{len(results)} passed")
    return passed == len(results)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        print("=== order_executor.py SELF-TEST ===")
        ok = self_test()
        raise SystemExit(0 if ok else 1)
    ap.print_help()


if __name__ == "__main__":
    main()
