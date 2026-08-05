#!/usr/bin/env python3
"""
God's Eye — Carry Trade Mechanics: hedged-yield spread + yen-borrow distribution

Measures CHANNEL B of the carry trade, which the framework had no instrument for.
See Framework/Reverse Carry Trade - Mechanics.md for the full decomposition.

  Channel A  FX carry. Leveraged, unhedged, unwinds on YEN STRENGTH.
             Framework already tracks it: jpy_spec_short (w=0.270),
             yen_episode_days (w=0.225) -- about half of l_cross.
  Channel B  Institutional reallocation. ~$3tn+ of FX-HEDGED foreign bonds held
             by life insurers, pensions, banks, GPIF. Unwinds on JGB YIELD
             LEVEL and needs no yen move at all. Previously unmeasured.

PART 1 -- HEDGED-YIELD SPREAD
-----------------------------
Channel B holdings are liability-matching, so they are usually FX-hedged. The
flow is therefore governed NOT by the raw yield gap but by:

    hedged_UST = US10Y - (US_3M - JPY_3M) - basis
    spread     = JGB10Y - hedged_UST        # positive = JGBs win = repatriate

The raw gap still shows ~+1.9pp in Treasuries' favour, which is why the
differential framing misleads: hedging costs ~2.3% and inverts the answer.

PART 2 -- WHERE THE YEN BORROW SITS
-----------------------------------
BIS Locational Banking Statistics, JPY-denominated cross-border claims by
counterparty country. Quarterly, back to 1977-Q4. This is the closest thing to
a real exposure measure for yen funding -- and it dwarfs the CFTC futures book
the framework currently leans on (~$13bn notional vs ~$2.26tn outstanding).

Usage:
    python3 carry_mechanics.py
    python3 carry_mechanics.py --json
"""

import argparse
import csv
import io
import json
import subprocess
import sys
from datetime import date

FRED = "https://fred.stlouisfed.org/graph/fredgraph.csv?id="
BIS_LBS = ("https://stats.bis.org/api/v2/data/dataflow/BIS/WS_LBS_D_PUB/1.0/"
           "Q.S.C.A.JPY.A.5J.A.5A.A.{cp}.N?format=csv")

# Counterparty countries worth resolving individually. KY (Cayman) matters most:
# it is where leveraged vehicles domicile, so it is the best available proxy for
# the leveraged slice of yen funding.
BIS_COUNTRIES = {
    "5J": "ALL counterparties",
    "KY": "Cayman Islands (leveraged vehicles)",
    "GB": "United Kingdom",
    "US": "United States",
    "FR": "France",
    "SG": "Singapore",
    "HK": "Hong Kong",
    "DE": "Germany",
    "AU": "Australia",
    "CN": "China",
}

# Cross-currency basis is not freely observable; JPY basis has historically run
# 0 to -50bp against the JPY-based hedger. Reported as a band, not a point.
BASIS_BAND = (0.0, -0.25)


# ── JPY 3M short rate ────────────────────────────────────────────────────────
#
# FRED's 3M TIBOR (IR3TIB01JPM156N) is MONTHLY and lags badly -- on 2026-08-03
# its newest print was 2026-05-01 at 1.274%, from before the BOJ's June hike to
# 1.00%. Using it stale understates the hedge cost and overstates the JGB
# advantage in Part 1.
#
# Fix: rebuild it as BOJ_POLICY + (TIBOR - call rate) spread. The policy rate is
# known exactly; only the spread has to be estimated.
#
# ⚠️ The spread is NOT stable -- it widens as the BOJ hikes, because 3M TIBOR
# prices in the NEXT move:
#     2025 (policy 0.477%)      spread ~30bp
#     2026 H1 (policy 0.727%)   spread ~50-55bp
# So an 18-month median (33.7bp) is wrong for the current regime. A trailing-6
# median (~51bp) reflects the hiking cycle actually in force.
BOJ_POLICY_RATE = 1.00      # %, confirmed after the 2026-06-16 hike
TIBOR_SPREAD_WINDOW = 6     # months of TIBOR-minus-call to median over
TIBOR_STALE_DAYS = 45       # beyond this, prefer the derived estimate


def jpy_3m_rate():
    """Best available JPY 3M. Returns (rate_pct, meta)."""
    from datetime import datetime as _dt

    def series(s):
        try:
            r = subprocess.run(["curl", "-s", "--max-time", "25", FRED + s],
                               capture_output=True, text=True)
            return {d: float(v) for d, v in
                    [x for x in csv.reader(io.StringIO(r.stdout))][1:]
                    if v not in (".", "")}
        except Exception:
            return {}

    tib, call = series("IR3TIB01JPM156N"), series("IRSTCI01JPM156N")
    if not tib:
        return None, {"source": "unavailable"}

    tib_date = max(tib)
    age = (date.today() - _dt.strptime(tib_date, "%Y-%m-%d").date()).days

    spread = None
    if call:
        common = sorted(set(tib) & set(call))[-TIBOR_SPREAD_WINDOW:]
        sp = sorted((tib[d] - call[d]) for d in common)
        if sp:
            n = len(sp)
            spread = sp[n // 2] if n % 2 else (sp[n // 2 - 1] + sp[n // 2]) / 2

    if age <= TIBOR_STALE_DAYS:
        return tib[tib_date], {"source": "TIBOR (fresh)", "date": tib_date,
                               "age_days": age}
    if spread is not None:
        est = BOJ_POLICY_RATE + spread
        return round(est, 4), {
            "source": "derived: BOJ policy + trailing-%d median TIBOR spread"
                      % TIBOR_SPREAD_WINDOW,
            "policy": BOJ_POLICY_RATE, "spread_bp": round(spread * 100, 1),
            "stale_tibor": tib[tib_date], "stale_tibor_date": tib_date,
            "stale_tibor_age_days": age,
        }
    return tib[tib_date], {"source": "TIBOR (STALE, no spread available)",
                           "date": tib_date, "age_days": age}


def fred_last(series):
    try:
        r = subprocess.run(["curl", "-s", "--max-time", "25", FRED + series],
                           capture_output=True, text=True)
        rows = [x for x in csv.reader(io.StringIO(r.stdout))][1:]
        vals = [(d, float(v)) for d, v in rows if v not in (".", "")]
        return vals[-1] if vals else (None, None)
    except Exception:
        return (None, None)


def jgb_proxy():
    """Reuse yen_mechanics' validated 2561.T proxy rather than duplicating it."""
    try:
        sys.path.insert(0, __file__.rsplit("/", 1)[0])
        from yen_mechanics import jgb_daily_proxy, fred_latest
        anchor = fred_latest("IRLTLT01JPM156N")
        est, meta = jgb_daily_proxy(anchor)
        return (est, meta) if est is not None else (anchor, {"fallback": "FRED monthly"})
    except Exception:
        d, v = fred_last("IRLTLT01JPM156N")
        return v, {"fallback": "FRED monthly"}


def bis_jpy_claims(cp, last_n=None):
    """JPY-denominated cross-border claims on counterparty `cp`, USD millions."""
    url = BIS_LBS.format(cp=cp)
    if last_n:
        url += f"&lastNObservations={last_n}"
    try:
        r = subprocess.run(["curl", "-sL", "--max-time", "60", url],
                           capture_output=True, text=True)
        rows = list(csv.DictReader(io.StringIO(r.stdout)))
        out = [(x["TIME_PERIOD"], float(x["OBS_VALUE"]))
               for x in rows if x.get("OBS_VALUE")]
        out.sort()
        return out
    except Exception:
        return []


def cohort_breakeven(spot):
    """Flow-weighted USD/JPY entry rate by vintage of yen borrowing.

    BIS reports JPY-denominated claims in USD, so raw USD deltas mix real
    borrowing with pure FX revaluation. Converting the stock back to JPY
    (usd_stock * usdjpy) first isolates genuine new borrowing; each quarter's
    net-new borrowing is then weighted by that quarter's average USD/JPY.

    The result is the level at which each vintage's aggregate carry position
    goes underwater — a mechanically derived pain threshold rather than a
    round number or an option barrier."""
    bis = bis_jpy_claims("5J")
    if not bis:
        return []
    try:
        r = subprocess.run(["curl", "-s", "--max-time", "60", FRED + "DEXJPUS"],
                           capture_output=True, text=True)
        fx = {}
        for d, v in [x for x in csv.reader(io.StringIO(r.stdout))][1:]:
            if v in (".", ""):
                continue
            q = f"{d[:4]}-Q{(int(d[5:7]) - 1)//3 + 1}"
            fx.setdefault(q, []).append(float(v))
        fxq = {k: sum(v) / len(v) for k, v in fx.items()}
    except Exception:
        return []

    rows = [(t, u, fxq[t]) for t, u in bis if t in fxq]
    flows = []
    for i in range(1, len(rows)):
        t, u, x = rows[i]
        _, pu, px = rows[i - 1]
        d = u * x - pu * px          # change in JPY-denominated stock
        if d > 0:
            flows.append((t, d, x))
    if not flows:
        return []
    tot = sum(f for _, f, _ in flows)
    out = []
    for lo, hi, lab in [(1978, 2014, "pre-2015"), (2015, 2019, "2015-19"),
                        (2020, 2022, "2020-22"), (2023, 2026, "2023-26")]:
        s = [(f, x) for t, f, x in flows if lo <= int(t[:4]) <= hi]
        if not s:
            continue
        tt = sum(f for f, _ in s)
        avg = sum(f * x for f, x in s) / tt
        out.append({"cohort": lab, "avg_entry": round(avg, 1),
                    "share_pct": round(100 * tt / tot, 1),
                    "pnl_at_spot_pct": round(100 * (spot / avg - 1), 1),
                    "breakeven_usdjpy": round(avg, 1)})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--skip-bis", action="store_true", help="hedged spread only")
    args = ap.parse_args()

    # ── Part 1: hedged-yield spread ──────────────────────────────────────────
    d10, us10 = fred_last("DGS10")
    d3m, us3m = fred_last("DTB3")
    jpy3m, jpy3m_meta = jpy_3m_rate()
    jgb10, jgb_meta = jgb_proxy()

    if None in (us10, us3m, jpy3m, jgb10):
        print("ERROR: missing rate inputs", file=sys.stderr)
        return 2

    rows = []
    for basis in BASIS_BAND:
        hc = us3m - jpy3m - basis
        hedged = us10 - hc
        rows.append({"basis": basis, "hedge_cost": round(hc, 3),
                     "hedged_ust": round(hedged, 3),
                     "spread_vs_jgb": round(jgb10 - hedged, 3)})
    spread_mid = round(sum(r["spread_vs_jgb"] for r in rows) / len(rows), 3)

    result = {
        "as_of": date.today().isoformat(),
        "us_10y": us10, "us_10y_date": d10,
        "us_3m": us3m, "us_3m_date": d3m,
        "jpy_3m": jpy3m, "jpy_3m_source": jpy3m_meta,
        "jgb_10y": jgb10, "jgb_source": jgb_meta,
        "raw_gap_us_minus_jgb": round(us10 - jgb10, 3),
        "hedged": rows,
        "spread_mid_pp": spread_mid,
        "repatriation_favoured": spread_mid > 0,
    }

    # ── Part 2: BIS yen-borrow distribution ──────────────────────────────────
    if not args.skip_bis:
        dist, hist = {}, []
        for cp, label in BIS_COUNTRIES.items():
            series = bis_jpy_claims(cp, last_n=1 if cp != "5J" else None)
            if not series:
                continue
            if cp == "5J":
                hist = series
                dist[cp] = {"label": label, "period": series[-1][0],
                            "usd_bn": round(series[-1][1] / 1000, 1)}
            else:
                dist[cp] = {"label": label, "period": series[-1][0],
                            "usd_bn": round(series[-1][1] / 1000, 1)}
        result["bis_jpy_claims"] = dist
        if hist:
            peak = max(hist, key=lambda r: r[1])
            result["bis_history"] = {
                "first": hist[0][0], "last": hist[-1][0], "n_quarters": len(hist),
                "peak_period": peak[0], "peak_usd_bn": round(peak[1] / 1000, 1),
                "latest_usd_bn": round(hist[-1][1] / 1000, 1),
                "pct_off_peak": round(100 * (hist[-1][1] / peak[1] - 1), 1),
            }

    # ── Part 3: cohort breakeven ─────────────────────────────────────────────
    if not args.skip_bis:
        try:
            import yfinance as _yf, warnings as _w
            _w.filterwarnings("ignore")
            spot = float(_yf.Ticker("USDJPY=X").history(period="5d")["Close"].dropna().iloc[-1])
        except Exception:
            spot = None
        if spot:
            result["usdjpy_spot"] = round(spot, 2)
            result["cohort_breakeven"] = cohort_breakeven(spot)

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    print("=" * 72)
    print(f"  Carry Trade Mechanics — Channel B          {result['as_of']}")
    print("=" * 72)
    print("\n  ── HEDGED-YIELD SPREAD ──")
    print(f"    US 10Y  {us10:.2f}%  ({d10})     JGB 10Y {jgb10:.3f}%  "
          f"({jgb_meta.get('ticker', jgb_meta.get('fallback',''))})")
    print(f"    US 3M   {us3m:.2f}%  ({d3m})     JPY 3M  {jpy3m:.3f}%")
    _m = jpy3m_meta
    print(f"      JPY 3M source: {_m.get('source')}"
          + (f"  [policy {_m['policy']:.2f}% + {_m['spread_bp']:.1f}bp; "
             f"raw TIBOR {_m['stale_tibor']:.3f}% was {_m['stale_tibor_age_days']}d stale]"
             if 'spread_bp' in _m else ""))
    print(f"    Raw gap (US − JGB): {result['raw_gap_us_minus_jgb']:+.2f}pp  "
          f"← misleading before hedging")
    print()
    print(f"    {'basis':>7} {'hedge cost':>11} {'hedged UST':>11} {'JGB − hedged':>13}")
    for r in rows:
        print(f"    {r['basis']:7.2f} {r['hedge_cost']:11.2f} {r['hedged_ust']:11.2f} "
              f"{r['spread_vs_jgb']:+13.2f}pp")
    print()
    if result["repatriation_favoured"]:
        print(f"    🔴 JGBs OUT-YIELD hedged Treasuries by ~{spread_mid:+.2f}pp")
        print("       → Channel B repatriation economically favoured.")
    else:
        print(f"    🟢 Hedged Treasuries still win by {-spread_mid:.2f}pp")
        print("       → outbound flow still rational.")

    if not args.skip_bis and result.get("bis_jpy_claims"):
        h = result.get("bis_history", {})
        print("\n  ── WHERE THE YEN BORROW SITS (BIS LBS, JPY cross-border claims) ──")
        if h:
            print(f"    History: {h['n_quarters']} quarters, {h['first']} → {h['last']}")
            print(f"    Latest ${h['latest_usd_bn']:,.0f}bn   "
                  f"peak ${h['peak_usd_bn']:,.0f}bn ({h['peak_period']})   "
                  f"{h['pct_off_peak']:+.1f}% off peak")
            print()
        tot = result["bis_jpy_claims"].get("5J", {}).get("usd_bn")
        for cp, v in sorted(result["bis_jpy_claims"].items(),
                            key=lambda kv: -kv[1]["usd_bn"]):
            if cp == "5J":
                continue
            share = f"{100*v['usd_bn']/tot:5.1f}%" if tot else "    —"
            print(f"    {cp:3} {v['label']:36} ${v['usd_bn']:>8,.0f}bn  {share}")
        print()
        print("    ⚠️  Compare the CFTC IMM futures book (~$13bn notional) that")
        print("        l_cross weights at 0.270. It is a SENTIMENT gauge, not an")
        print("        exposure measure — off by roughly two orders of magnitude.")
    ch = result.get("cohort_breakeven")
    if ch:
        sp = result["usdjpy_spot"]
        print(f"\n  ── COHORT BREAKEVEN (spot {sp}) ──")
        print(f"    {'cohort':10} {'avg entry':>10} {'share':>7} {'P&L now':>9}   status")
        for c in ch:
            st = "🔴 UNDERWATER" if c["pnl_at_spot_pct"] < 0 else (
                 "🟠 thin" if c["pnl_at_spot_pct"] < 10 else "🟢 deep onside")
            print(f"    {c['cohort']:10} {c['avg_entry']:10.1f} "
                  f"{c['share_pct']:6.1f}% {c['pnl_at_spot_pct']:+8.1f}%   {st}")
        newest = ch[-1]
        print(f"\n    Marginal cohort ({newest['cohort']}) breaks even at "
              f"USD/JPY {newest['breakeven_usdjpy']}.")
        print("    That — not a round number — is the mechanically derived pain level.")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
