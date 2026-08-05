#!/usr/bin/env python3
"""
God's Eye — Semiconductor Self-Sufficiency Gate

Tracks the constraint that actually gates China's Taiwan kinetic option.

THE ARGUMENT
------------
A kinetic Taiwan action destroys or denies TSMC. China is the world's largest
semiconductor importer, so the binding constraint on that option is NOT US
distraction (see carrier_watch.py, which tracks the distraction side and is
currently tripped) -- it is whether China can survive losing TSMC.

That makes Taiwan timing a SEMICONDUCTOR clock, not a distraction clock. The
carrier count says the window is open. This says whether the door is usable.

TWO LAYERS, DELIBERATELY SEPARATE
---------------------------------
1. MARKET-IMPLIED SUBSTITUTION INDEX (automated, yfinance)
   A basket of the Chinese foundry/toolchain complex measured against the
   incumbents it must replace (TSMC + ASML). A rising ratio means the market
   is pricing substitution progress.

   *** This measures EXPECTATIONS, not CAPABILITY. *** Equity prices move on
   sentiment, liquidity and policy rumour. It is a fast, noisy proxy -- useful
   for direction and turning points, worthless as a capability readout.

2. CAPABILITY GATE LADDER (manual, sourced, versioned)
   The physical gates. These move on engineering, not on price, and they are
   what actually decide the question. Hand-maintained with sources and dates
   because there is no feed for "can they make a 5nm chip yet."

The script reports both and names the BINDING gate.

Usage:
    python3 semi_selfsufficiency_watch.py
    python3 semi_selfsufficiency_watch.py --json
"""

import argparse
import json
import sys
import warnings
from datetime import date

warnings.filterwarnings("ignore")

# ── Layer 1: market-implied substitution ─────────────────────────────────────
CHINA_COMPLEX = {
    "688981.SS": "SMIC (logic foundry)",
    "688347.SS": "Hua Hong Grace (mature foundry)",
    "002371.SZ": "NAURA (deposition/etch tools)",
    "688012.SS": "AMEC (etch tools)",
    "688361.SS": "Skyverse (inspection/metrology)",
}
INCUMBENTS = {
    "2330.TW": "TSMC",
    "ASML":    "ASML",
}

# ── Layer 2: capability gate ladder ──────────────────────────────────────────
# status: ACHIEVED | PARTIAL | LIMITED | NOT_ACHIEVED
# binding: True marks a gate that alone blocks TSMC substitution.
GATES = [
    {
        "gate": "Logic <=7nm domestic",
        "status": "ACHIEVED",
        "detail": "SMIC N+2 7nm via DUV multi-patterning. Low yield, high cost, "
                  "not economically comparable to TSMC at node.",
        "binding": False,
        "as_of": "2023",
    },
    {
        "gate": "DRAM at scale",
        "status": "ACHIEVED",
        "detail": "CXMT is now the world's 4th-largest DRAM maker. STAR Market "
                  "IPO 2026-07-27 raised RMB 57.9bn ($8.6bn), +466% debut, "
                  "~$85bn valuation. Proceeds earmarked for DRAM expansion.",
        "binding": False,
        "as_of": "2026-07",
    },
    {
        "gate": "NAND at scale",
        "status": "PARTIAL",
        "detail": "YMTC producing but constrained by equipment access.",
        "binding": False,
        "as_of": "2026",
    },
    {
        "gate": "Immersion DUV lithography, domestic",
        "status": "LIMITED",
        "detail": "Shanghai Aishengna (state-backed; teams from SMEE and "
                  "Yuliangsheng, a SiCarrier affiliate) began LIMITED production "
                  "~Jul 2026. Target ~5 units in 2026, ~20 in 2027. 28nm-class; "
                  "7nm reachable via multi-patterning. SMIC testing since Sep "
                  "2025, mass production expected 2027. US lawmakers proposed "
                  "DUV-immersion-specific restrictions Apr 2026.",
        "binding": False,
        "as_of": "2026-07",
        "units_2026_target": 5,
        "units_2027_target": 20,
    },
    {
        "gate": "EUV lithography, domestic",
        "status": "NOT_ACHIEVED",
        "detail": "No domestic EUV. This is the hard gate: leading-edge logic "
                  "at TSMC-comparable yield and cost is not reachable without "
                  "it. Multi-patterning DUV substitutes only at severe yield "
                  "and cost penalty.",
        "binding": True,
        "as_of": "2026-07",
    },
    {
        "gate": "HBM at scale (AI memory)",
        "status": "NOT_ACHIEVED",
        "detail": "The AI-relevant memory tier. CXMT conventional DRAM does not "
                  "substitute for SK Hynix/Samsung HBM.",
        "binding": True,
        "as_of": "2026-07",
    },
]

STATUS_MARK = {"ACHIEVED": "✅", "PARTIAL": "🟡", "LIMITED": "🟠",
               "NOT_ACHIEVED": "🔴"}


def basket_index(tickers, period="1y"):
    """Equal-weight index of normalized closes. Returns (series, per-ticker)."""
    import yfinance as yf
    import pandas as pd

    cols, detail = {}, {}
    for t in tickers:
        try:
            h = yf.Ticker(t).history(period=period)["Close"].dropna()
        except Exception:
            continue
        if len(h) < 20:
            continue
        h.index = h.index.tz_localize(None)
        cols[t] = h / h.iloc[0]          # normalize to 1.0 at window start
        detail[t] = {
            "last": round(float(h.iloc[-1]), 2),
            "chg_1m_pct": _pct(h, 21),
            "chg_3m_pct": _pct(h, 63),
            "chg_1y_pct": round(100 * (float(h.iloc[-1]) / float(h.iloc[0]) - 1), 1),
        }
    if not cols:
        return None, {}
    df = pd.concat(cols, axis=1).ffill().dropna()
    return df.mean(axis=1), detail


def _pct(series, lookback):
    if len(series) <= lookback:
        return None
    return round(100 * (float(series.iloc[-1]) / float(series.iloc[-lookback - 1]) - 1), 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    cn, cn_detail = basket_index(list(CHINA_COMPLEX))
    inc, inc_detail = basket_index(list(INCUMBENTS))
    if cn is None or inc is None:
        print("ERROR: market data unavailable", file=sys.stderr)
        return 2

    import pandas as pd
    joined = pd.concat({"cn": cn, "inc": inc}, axis=1).ffill().dropna()
    ratio = joined["cn"] / joined["inc"]

    sub = {
        "latest": round(float(ratio.iloc[-1]), 4),
        "chg_1m_pct": _pct(ratio, 21),
        "chg_3m_pct": _pct(ratio, 63),
        "chg_1y_pct": round(100 * (float(ratio.iloc[-1]) / float(ratio.iloc[0]) - 1), 1),
    }

    binding = [g for g in GATES if g["binding"]]
    cleared = [g for g in GATES if g["status"] == "ACHIEVED"]

    result = {
        "as_of": date.today().isoformat(),
        "substitution_index": sub,
        "china_complex": cn_detail,
        "incumbents": inc_detail,
        "gates_total": len(GATES),
        "gates_cleared": len(cleared),
        "binding_gates": [g["gate"] for g in binding],
        "gate_ladder": GATES,
        "verdict": ("GATE CLOSED — leading-edge substitution not viable"
                    if binding else "GATE OPEN"),
    }

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    print("=" * 72)
    print(f"  Semiconductor Self-Sufficiency Gate      {result['as_of']}")
    print("  (gates China's Taiwan kinetic option)")
    print("=" * 72)
    print()
    print("  ── Layer 1: MARKET-IMPLIED substitution index ──")
    print("     China complex / (TSMC + ASML), equal-weight, 1y-normalized")
    print(f"     ratio {sub['latest']:.4f}"
          f"   1m {_f(sub['chg_1m_pct'])}"
          f"   3m {_f(sub['chg_3m_pct'])}"
          f"   1y {_f(sub['chg_1y_pct'])}")
    print()
    print("     China complex:")
    for t, n in CHINA_COMPLEX.items():
        d = cn_detail.get(t)
        if d:
            print(f"       {n:34} 1m {_f(d['chg_1m_pct']):>8}  1y {_f(d['chg_1y_pct']):>8}")
    print("     Incumbents:")
    for t, n in INCUMBENTS.items():
        d = inc_detail.get(t)
        if d:
            print(f"       {n:34} 1m {_f(d['chg_1m_pct']):>8}  1y {_f(d['chg_1y_pct']):>8}")
    print()
    print("     ⚠️  Measures EXPECTATIONS, not capability. Direction only.")
    print()
    print("  ── Layer 2: CAPABILITY GATE LADDER ──")
    for g in GATES:
        mark = STATUS_MARK.get(g["status"], "?")
        bind = "  ← BINDING" if g["binding"] else ""
        print(f"     {mark} {g['gate']:38} {g['status']:13} [{g['as_of']}]{bind}")
    print()
    print(f"     cleared {len(cleared)}/{len(GATES)}")
    print()
    print("  " + "-" * 68)
    print(f"  VERDICT: {result['verdict']}")
    for g in binding:
        print(f"    🔴 {g['gate']}")
        print(f"       {g['detail']}")
    print()
    duv = next((g for g in GATES if "Immersion DUV" in g["gate"]), None)
    if duv:
        print(f"  Scale check: domestic immersion DUV target is "
              f"~{duv['units_2026_target']} units in 2026, "
              f"~{duv['units_2027_target']} in 2027.")
        print("  For context TSMC alone runs dozens of EUV tools. The toolchain")
        print("  gap is measured in hundreds of units and years, not quarters.")
    print("=" * 72)
    return 0


def _f(v):
    return "n/a" if v is None else f"{v:+.1f}%"


if __name__ == "__main__":
    sys.exit(main())
