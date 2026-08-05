#!/usr/bin/env python3
"""
God's Eye — Reverse Carry Trade trigger monitor

Watches the REGIME BOUNDARY, not the level. See
Framework/Reverse Carry Trade - Mechanics.md for the derivation.

THE CORE LOGIC
--------------
The 2023-26 cohort of yen borrowing has a flow-weighted entry of USD/JPY 149.6
(BIS LBS, 194 quarters, converted to JPY terms to strip FX revaluation). Above
that the marginal cohort is onside and holds; below it, selling begets yen
strength begets more selling. That is a regime boundary, not a gradient.

But proximity alone is not the risk. The risk is proximity WITHOUT HEDGING:

    close to breakeven + VIX asleep  -> no dampener, initial move unabsorbed
    close to breakeven + VIX awake   -> being priced, materially safer

That interaction is what made August 2024 violent -- not the size of the carry,
but that nobody was positioned for it. This monitor scores that interaction.

WHAT IT DELIBERATELY DOES NOT CLAIM
-----------------------------------
It cannot size the margin-financed slice. Total JPY cross-border claims are
~$2.26tn; the exposed cohort is ~15%; the Cayman (leveraged-vehicle) share is
~24%; their US book is ~86% equity and corporate credit. But how much of that
is actually margin-financed against volatile collateral is known only to those
funds' prime brokers. This monitor tracks TRIGGER PROXIMITY, not loss size.

Usage:
    python3 rct_trigger_monitor.py
    python3 rct_trigger_monitor.py --json
"""

import argparse
import json
import sys
import warnings
from datetime import date

warnings.filterwarnings("ignore")

# Derived in carry_mechanics.py from BIS LBS + FRED DEXJPUS
COHORT_BREAKEVEN = 149.6      # 2023-26 vintage flow-weighted entry
COHORT_SHARE_PCT = 15.4       # share of outstanding JPY borrow
TOTAL_JPY_CLAIMS_BN = 2264.0  # BIS LBS, 2026-Q1
CAYMAN_SHARE_PCT = 24.2       # leveraged-vehicle proxy
CAYMAN_RISK_ASSET_PCT = 86.4  # equity + corporate credit, TIC SHL Jun-2024

VIX_ASLEEP = 20.0             # below this, the move is unhedged
VIX_AWAKE = 25.0              # above this, it is being priced
SPEC_SHORT_EXTREME = 30.0     # |NC net| as % OI


def market():
    import yfinance as yf
    out = {}
    for key, tkr in [("usdjpy", "USDJPY=X"), ("vix", "^VIX"),
                     ("spx", "^GSPC"), ("nky", "^N225")]:
        try:
            h = yf.Ticker(tkr).history(period="1mo")["Close"].dropna()
            out[key] = float(h.iloc[-1])
            out[key + "_5d_pct"] = round(100 * (h.iloc[-1] / h.iloc[-6] - 1), 2) \
                if len(h) > 6 else None
        except Exception:
            out[key] = None
    return out


def spec_short():
    """CFTC IMM JPY non-commercial net as % OI, via yen_mechanics_daily."""
    import subprocess
    url = ("https://snykuqyceqpplnzmyksp.supabase.co/rest/v1/yen_mechanics_daily"
           "?select=as_of_date,jpy_nc_pct_oi&order=as_of_date.desc&limit=1")
    k = "sb_publishable_TJg65x5w56CulOEdWFJNyQ_89loJtit"
    try:
        r = subprocess.run(["curl", "-s", "--max-time", "20", url,
                            "-H", f"apikey: {k}", "-H", f"Authorization: Bearer {k}"],
                           capture_output=True, text=True)
        rows = json.loads(r.stdout)
        if rows and rows[0].get("jpy_nc_pct_oi") is not None:
            return abs(float(rows[0]["jpy_nc_pct_oi"])), rows[0]["as_of_date"]
    except Exception:
        pass
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    m = market()
    fx, vix = m.get("usdjpy"), m.get("vix")
    ss, ss_date = spec_short()
    if fx is None or vix is None:
        print("ERROR: market data unavailable", file=sys.stderr)
        return 2

    dist = round(fx - COHORT_BREAKEVEN, 2)          # +ve = above breakeven
    dist_pct = round(100 * (fx / COHORT_BREAKEVEN - 1), 2)
    below = dist < 0

    # ── Regime classification ────────────────────────────────────────────────
    # Proximity band
    if below:
        prox = "BREACHED"
    elif dist <= 3:
        prox = "AT BOUNDARY"
    elif dist <= 8:
        prox = "APPROACHING"
    else:
        prox = "CLEAR"

    hedged = "ASLEEP" if vix < VIX_ASLEEP else ("AWAKE" if vix >= VIX_AWAKE else "STIRRING")

    # The interaction is the signal.
    if prox in ("BREACHED", "AT BOUNDARY") and hedged == "ASLEEP":
        state, note = "🔴 DANGEROUS", ("At/through the reflexive boundary with the "
                                       "market unhedged — no dampener on the initial move")
    elif prox in ("BREACHED", "AT BOUNDARY"):
        state, note = "🟠 ELEVATED", ("At/through the boundary but volatility is "
                                      "responding — being priced, not ambushed")
    elif prox == "APPROACHING" and hedged == "ASLEEP":
        state, note = "🟡 WATCH", ("Nearing the boundary with volatility asleep — "
                                   "the configuration that preceded Aug 2024")
    elif prox == "APPROACHING":
        state, note = "🟡 WATCH", "Nearing the boundary, volatility responding"
    else:
        state, note = "🟢 CLEAR", "Marginal cohort comfortably onside"

    exposed_bn = round(TOTAL_JPY_CLAIMS_BN * COHORT_SHARE_PCT / 100, 0)
    cayman_bn = round(TOTAL_JPY_CLAIMS_BN * CAYMAN_SHARE_PCT / 100, 0)

    result = {
        "as_of": date.today().isoformat(),
        "usdjpy": round(fx, 2), "usdjpy_5d_pct": m.get("usdjpy_5d_pct"),
        "cohort_breakeven": COHORT_BREAKEVEN,
        "distance_yen": dist, "distance_pct": dist_pct,
        "proximity": prox,
        "vix": round(vix, 2), "vix_5d_pct": m.get("vix_5d_pct"),
        "hedging_state": hedged,
        "spec_short_pct_oi": ss, "spec_short_date": ss_date,
        "spec_extreme": (ss is not None and ss >= SPEC_SHORT_EXTREME),
        "state": state, "note": note,
        "exposed_cohort_usd_bn": exposed_bn,
        "cayman_usd_bn": cayman_bn,
        "cayman_risk_asset_pct": CAYMAN_RISK_ASSET_PCT,
    }

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    print("=" * 70)
    print(f"  RCT Trigger Monitor                        {result['as_of']}")
    print("=" * 70)
    print()
    print(f"  USD/JPY            {fx:8.2f}   ({m.get('usdjpy_5d_pct'):+.2f}% 5d)")
    print(f"  Cohort breakeven   {COHORT_BREAKEVEN:8.2f}   (2023-26 vintage, {COHORT_SHARE_PCT}% of book)")
    print(f"  Distance           {dist:+8.2f} yen  ({dist_pct:+.2f}%)   → {prox}")
    print()
    print(f"  VIX                {vix:8.2f}   ({m.get('vix_5d_pct'):+.2f}% 5d)   → {hedged}")
    if ss is not None:
        flag = "  🚨 EXTREME" if result["spec_extreme"] else ""
        print(f"  Spec short         {ss:8.1f}% OI  ({ss_date}){flag}")
    print()
    print("  " + "-" * 66)
    print(f"  STATE: {state}")
    print(f"    {note}")
    print()
    print("  ── Exposure context (NOT a loss estimate) ──")
    print(f"    Total JPY cross-border claims   ${TOTAL_JPY_CLAIMS_BN:,.0f}bn")
    print(f"    Exposed cohort (2023-26)        ${exposed_bn:,.0f}bn  ({COHORT_SHARE_PCT}%)")
    print(f"    Cayman vehicles (leveraged)     ${cayman_bn:,.0f}bn  ({CAYMAN_SHARE_PCT}%)")
    print(f"    Their US book in risk assets    {CAYMAN_RISK_ASSET_PCT}%  (equity + corp credit)")
    print()
    print("    ⚠️  The margin-financed share is UNOBSERVABLE. This monitor tracks")
    print("        trigger proximity, not loss size. Composition data is the")
    print("        Jun-2024 TIC survey — structurally reliable, ~2yrs stale.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
