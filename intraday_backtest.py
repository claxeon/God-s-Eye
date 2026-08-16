#!/usr/bin/env python3
"""Backtest harness for the DAY-TRADING track (separate mechanism from the
God's Eye framework -- 2026-08-13, user's explicit call: day trading uses
its own technical/quant signal logic, not the framework's l1-l9/l_cross
legs, and therefore needs its own gate before any live paper order is
placed. See project_state.md G-067).

Strategy implemented: Opening Range Breakout (ORB), long/short, single
instrument, single trade per side per day.
  - Opening range = the high/low of the first `or_bars` bars of the regular
    session (09:30 ET open).
  - After the opening range window closes, the FIRST bar whose CLOSE trades
    outside the range triggers an entry in that direction, at that bar's
    close. Only the first breakout of the day is taken (no re-entry).
  - Stop-loss: the opposite edge of the opening range (long stop = OR low,
    short stop = OR high), checked against each subsequent bar's low/high
    -- not just its close, since a stop can be hit intrabar.
  - Exit: stop-loss if hit, otherwise the last bar of the regular session
    (flat overnight, always -- no day-trading strategy should hold through
    a gap it didn't size for).

HONEST SCOPE (read before trusting any output):
  - No commissions, no slippage, no bid/ask spread modeled. Real intraday
    round-trips on a liquid ETF have modest but non-zero costs (SPY spread
    is usually a cent, but 2x/day = a real drag at high trade counts). This
    backtest is an UPPER BOUND on the strategy's edge, not a live estimate.
  - Entry/exit both assume fill AT the trigger price (close of breakout bar,
    or exact stop level). Real fills will be at the next tick, which is
    worse for both stops and breakouts (adverse selection -- the trigger
    print isn't guaranteed executable). This also inflates the backtest's
    apparent edge.
  - Data is Alpaca's free-tier IEX feed only, not the consolidated SIP tape
    -- IEX is a real, liquid venue but a MINORITY of total volume, so bar
    prices can differ slightly from what a SIP-fed retail broker would show.
  - This is one parameter set (or_bars, timeframe) on one symbol. A result
    here is a single data point, not a swept/optimized strategy -- do not
    treat it as more than "does the simplest honest version of this idea
    show a directional edge before spending any real gate-cycles on it."
  - Per G-067: this backtest existing is a PRECONDITION for building any
    live paper execution for day trading, not a substitute for the same
    "N closed trades, positive cumulative P&L" gate discipline used
    elsewhere in this vault (order_caps.json's untested-tier rule). A good
    backtest justifies spending paper-trading cycles on the idea; it does
    not itself satisfy the gate.

Run: python3 intraday_backtest.py --self-test
     python3 intraday_backtest.py --run --symbol SPY --start 2023-01-01 --end 2026-08-01 --or-bars 6
"""
import argparse
import os
import sys
from datetime import datetime
from statistics import mean, pstdev
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")


def _et_day_and_time(bar_ts):
    """bar_ts: RFC3339 UTC string, e.g. '2026-08-13T13:30:00Z'. Returns
    (date_string_in_ET, (hour, minute)_in_ET)."""
    dt = datetime.fromisoformat(bar_ts.replace("Z", "+00:00")).astimezone(ET)
    return dt.date().isoformat(), (dt.hour, dt.minute)


def group_by_session_day(bars):
    """Groups bars into regular-session trading days (09:30-16:00 ET),
    dropping any bar outside that window (pre/post-market) since ORB is a
    regular-session strategy. Returns dict[date_str] -> list of bars, in
    chronological order."""
    days = {}
    for b in bars:
        day, (h, m) = _et_day_and_time(b["t"])
        minutes_since_open = (h * 60 + m) - (9 * 60 + 30)
        minutes_since_close = (16 * 60) - (h * 60 + m)
        if minutes_since_open < 0 or minutes_since_close < 0:
            continue
        days.setdefault(day, []).append(b)
    return days


def orb_trades_for_day(day_bars, or_bars=6):
    """Returns a list of 0 or 1 trade dicts for one day's chronological
    bar list. or_bars: number of bars forming the opening range (e.g.
    6 x 5Min = 30 minutes)."""
    if len(day_bars) <= or_bars:
        return []
    opening = day_bars[:or_bars]
    or_high = max(b["h"] for b in opening)
    or_low = min(b["l"] for b in opening)
    rest = day_bars[or_bars:]

    direction = None
    entry_price = None
    entry_ts = None
    for b in rest:
        if b["c"] > or_high:
            direction, entry_price, entry_ts = "long", b["c"], b["t"]
            break
        if b["c"] < or_low:
            direction, entry_price, entry_ts = "short", b["c"], b["t"]
            break
    if direction is None:
        return []

    stop = or_low if direction == "long" else or_high
    after_entry = rest[rest.index(next(b for b in rest if b["t"] == entry_ts)) + 1:]

    exit_price, exit_ts, exit_reason = None, None, "session_close"
    for b in after_entry:
        if direction == "long" and b["l"] <= stop:
            exit_price, exit_ts, exit_reason = stop, b["t"], "stop"
            break
        if direction == "short" and b["h"] >= stop:
            exit_price, exit_ts, exit_reason = stop, b["t"], "stop"
            break
    if exit_price is None:
        last = day_bars[-1]
        exit_price, exit_ts = last["c"], last["t"]

    ret_pct = ((exit_price - entry_price) / entry_price * 100 if direction == "long"
               else (entry_price - exit_price) / entry_price * 100)
    return [{
        "day": day_bars[0]["t"][:10], "direction": direction,
        "entry_price": entry_price, "entry_ts": entry_ts,
        "exit_price": exit_price, "exit_ts": exit_ts, "exit_reason": exit_reason,
        "return_pct": ret_pct, "or_high": or_high, "or_low": or_low,
    }]


def run_backtest(bars, or_bars=6):
    days = group_by_session_day(bars)
    trades = []
    for day in sorted(days):
        trades.extend(orb_trades_for_day(days[day], or_bars=or_bars))
    return trades


def summarize(trades):
    if not trades:
        return {"count": 0}
    rets = [t["return_pct"] for t in trades]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    compounded = 1.0
    for r in rets:
        compounded *= (1 + r / 100)
    return {
        "count": len(trades),
        "win_rate": len(wins) / len(trades),
        "avg_return_pct": mean(rets),
        "stdev_return_pct": pstdev(rets) if len(rets) > 1 else 0.0,
        "sum_return_pct": sum(rets),
        "compounded_return_pct": (compounded - 1) * 100,
        "avg_win_pct": mean(wins) if wins else 0.0,
        "avg_loss_pct": mean(losses) if losses else 0.0,
        "stopped_out": sum(1 for t in trades if t["exit_reason"] == "stop"),
        "longs": sum(1 for t in trades if t["direction"] == "long"),
        "shorts": sum(1 for t in trades if t["direction"] == "short"),
    }


def self_test():
    passed = 0

    def bar(t, o, h, l, c):
        return {"t": t, "o": o, "h": h, "l": l, "c": c}

    # Case 1: clean long breakout, holds to close. 09:30-09:55 opening range
    # (6 x 5min bars), breakout bar at 10:00, price stays up all day.
    day1 = ("2026-08-13", 13)  # 13:30Z = 09:30 ET (EDT)
    bars1 = [
        bar("2026-08-13T13:30:00Z", 100, 101, 99, 100),
        bar("2026-08-13T13:35:00Z", 100, 100.5, 99.5, 100),
        bar("2026-08-13T13:40:00Z", 100, 100.5, 99.5, 100),
        bar("2026-08-13T13:45:00Z", 100, 100.5, 99.5, 100),
        bar("2026-08-13T13:50:00Z", 100, 100.5, 99.5, 100),
        bar("2026-08-13T13:55:00Z", 100, 100.5, 99.5, 100),  # OR = [99, 101]
        bar("2026-08-13T14:00:00Z", 100.5, 102, 100.5, 101.5),  # breakout close 101.5 > 101
        bar("2026-08-13T14:05:00Z", 101.5, 103, 101, 102.5),
        bar("2026-08-13T20:00:00Z", 102.5, 103, 102, 103),  # last bar, 16:00 ET close
    ]
    days1 = group_by_session_day(bars1)
    assert list(days1.keys()) == ["2026-08-13"], f"day grouping failed: {days1.keys()}"
    trades1 = orb_trades_for_day(days1["2026-08-13"], or_bars=6)
    assert len(trades1) == 1, f"expected 1 trade, got {len(trades1)}"
    assert trades1[0]["direction"] == "long"
    assert trades1[0]["entry_price"] == 101.5
    assert trades1[0]["exit_price"] == 103  # rides to session close
    assert trades1[0]["exit_reason"] == "session_close"
    assert abs(trades1[0]["return_pct"] - ((103 - 101.5) / 101.5 * 100)) < 1e-9
    passed += 1

    # Case 2: long breakout then reverses through OR low -- stop should fire.
    bars2 = [
        bar("2026-08-13T13:30:00Z", 100, 101, 99, 100),
        bar("2026-08-13T13:35:00Z", 100, 100.5, 99.5, 100),
        bar("2026-08-13T13:40:00Z", 100, 100.5, 99.5, 100),
        bar("2026-08-13T13:45:00Z", 100, 100.5, 99.5, 100),
        bar("2026-08-13T13:50:00Z", 100, 100.5, 99.5, 100),
        bar("2026-08-13T13:55:00Z", 100, 100.5, 99.5, 100),  # OR = [99, 101]
        bar("2026-08-13T14:00:00Z", 100.5, 102, 100.5, 101.5),  # breakout
        bar("2026-08-13T14:05:00Z", 101.5, 101.5, 98, 98.5),  # dumps through OR low=99
        bar("2026-08-13T20:00:00Z", 98.5, 99, 97, 97.5),
    ]
    days2 = group_by_session_day(bars2)
    trades2 = orb_trades_for_day(days2["2026-08-13"], or_bars=6)
    assert len(trades2) == 1
    assert trades2[0]["exit_reason"] == "stop"
    assert trades2[0]["exit_price"] == 99  # OR low
    assert trades2[0]["return_pct"] < 0
    passed += 1

    # Case 3: price never leaves the opening range -- no trade.
    bars3 = [
        bar("2026-08-13T13:30:00Z", 100, 101, 99, 100),
        bar("2026-08-13T13:35:00Z", 100, 100.5, 99.5, 100),
        bar("2026-08-13T13:40:00Z", 100, 100.5, 99.5, 100),
        bar("2026-08-13T13:45:00Z", 100, 100.5, 99.5, 100),
        bar("2026-08-13T13:50:00Z", 100, 100.5, 99.5, 100),
        bar("2026-08-13T13:55:00Z", 100, 100.5, 99.5, 100),  # OR = [99, 101]
        bar("2026-08-13T14:00:00Z", 100, 100.8, 99.5, 100.2),
        bar("2026-08-13T20:00:00Z", 100.2, 100.9, 99.6, 100.5),
    ]
    days3 = group_by_session_day(bars3)
    trades3 = orb_trades_for_day(days3["2026-08-13"], or_bars=6)
    assert trades3 == []
    passed += 1

    # Case 4: short breakout, symmetric to case 1.
    bars4 = [
        bar("2026-08-13T13:30:00Z", 100, 101, 99, 100),
        bar("2026-08-13T13:35:00Z", 100, 100.5, 99.5, 100),
        bar("2026-08-13T13:40:00Z", 100, 100.5, 99.5, 100),
        bar("2026-08-13T13:45:00Z", 100, 100.5, 99.5, 100),
        bar("2026-08-13T13:50:00Z", 100, 100.5, 99.5, 100),
        bar("2026-08-13T13:55:00Z", 100, 100.5, 99.5, 100),  # OR = [99, 101]
        bar("2026-08-13T14:00:00Z", 99.5, 99.5, 98, 98.5),  # breakout close 98.5 < 99
        bar("2026-08-13T20:00:00Z", 98.5, 99, 96, 96.5),
    ]
    days4 = group_by_session_day(bars4)
    trades4 = orb_trades_for_day(days4["2026-08-13"], or_bars=6)
    assert len(trades4) == 1
    assert trades4[0]["direction"] == "short"
    assert trades4[0]["exit_reason"] == "session_close"
    assert trades4[0]["return_pct"] > 0  # short + price fell = profit
    passed += 1

    # Case 5: pre/post-market bars are excluded from the session day.
    bars5 = [
        bar("2026-08-13T09:00:00Z", 100, 100, 100, 100),  # 05:00 ET, pre-market
        bar("2026-08-13T13:30:00Z", 100, 101, 99, 100),
        bar("2026-08-13T21:00:00Z", 100, 100, 100, 100),  # 17:00 ET, post-market
    ]
    days5 = group_by_session_day(bars5)
    assert len(days5["2026-08-13"]) == 1, "pre/post-market bars should be dropped"
    passed += 1

    # Case 6: summarize() aggregate math on a known trade list.
    fake_trades = [{"return_pct": 2.0, "exit_reason": "session_close", "direction": "long"},
                   {"return_pct": -1.0, "exit_reason": "stop", "direction": "short"}]
    s = summarize(fake_trades)
    assert s["count"] == 2 and s["win_rate"] == 0.5
    assert abs(s["avg_return_pct"] - 0.5) < 1e-9
    assert abs(s["sum_return_pct"] - 1.0) < 1e-9
    passed += 1

    print(f"self_test: {passed}/6 cases passed")
    return passed == 6


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--run", action="store_true")
    p.add_argument("--symbol", default="SPY")
    p.add_argument("--start", default="2022-01-01")
    p.add_argument("--end", default=None)
    p.add_argument("--or-bars", type=int, default=6)
    p.add_argument("--timeframe", default="5Min")
    args = p.parse_args()

    if args.self_test:
        ok = self_test()
        sys.exit(0 if ok else 1)

    if args.run:
        from alpaca_client import Alpaca
        client = Alpaca(paper=True)
        print(f"Fetching {args.symbol} {args.timeframe} bars {args.start} -> {args.end or 'now'} (IEX feed)...")
        bars = client.bars(args.symbol, timeframe=args.timeframe, start=args.start, end=args.end)
        print(f"{len(bars)} bars fetched.")
        trades = run_backtest(bars, or_bars=args.or_bars)
        s = summarize(trades)
        print(f"\n=== ORB backtest: {args.symbol}, {args.timeframe}, {args.or_bars}-bar opening range ===")
        for k, v in s.items():
            print(f"  {k}: {v}")
        n_days = len(group_by_session_day(bars))
        print(f"\n  trading days covered: {n_days}, trades/day: {s['count'] / n_days if n_days else 0:.2f}")
    elif not args.self_test:
        p.print_help()
