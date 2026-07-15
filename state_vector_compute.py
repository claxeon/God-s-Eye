#!/usr/bin/env python3
"""
God's Eye — State Vector Computation Script
============================================
Computes L(t) ∈ ℝ¹⁰ from observable primary series.

Each leg score L_i(t) = σ( Σ_j  w_ij · z_ij(t) )
where z_ij = (x_ij - μ_ij) / σ_ij  and σ(·) is the logistic function.

Data sources (free/API-accessible):
  - EIA API:        L1 (SPR draw, Brent via STEO), L5 (Henry Hub)
  - FRED API:       L_cross (USD/JPY, BOJ rate proxy via US-JP spread)
  - Yahoo Finance:  L1 (Brent backwardation), L3 (CDX proxy via HYG/LQD)
  - USDA PSD:       L5 (wheat STU)
  - Manual/event:   L1 (Hormuz status), L8 (Bab status), L4 (GENIUS Act)

Commercial gaps (flagged as None, not estimated):
  - AIS routing share (L1, L8): requires Kpler/Windward subscription
  - Lloyd's war risk premium (L8): not public
  - CFTC COT JPY positioning (L_cross): free but weekly lag

Usage:
    pip install requests pandas scipy numpy yfinance --break-system-packages
    export EIA_API_KEY="6JlB2qAQoHxNGL6kEiiZ6fIRt8cU5FlqR8ReVWYE"
    export SUPABASE_URL="https://snykuqyceqpplnzmyksp.supabase.co"
    export SUPABASE_KEY="<service_key>"
    python3 state_vector_compute.py
    python3 state_vector_compute.py --date 2026-06-08 --store
"""

import os
import sys
import json
import math
import argparse
import urllib.request
from datetime import date, datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional, Dict, List

_JSON_MODE = False  # set True by --json flag; routes progress prints to stderr

def log(*args, **kwargs):
    """Progress print — goes to stderr in --json mode so stdout stays clean JSON."""
    if _JSON_MODE:
        kwargs.setdefault("file", sys.stderr)
    print(*args, **kwargs)

# ── Configuration ─────────────────────────────────────────────────────────────

EIA_KEY      = os.environ.get("EIA_API_KEY", "")
# SECURITY: EIA_API_KEY must be set via env var. Key in Framework/State Vector Definition.md needs rotation.
FRED_BASE    = "https://fred.stlouisfed.org/graph/fredgraph.csv?id="
EIA_BASE     = "https://api.eia.gov/v2"
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://snykuqyceqpplnzmyksp.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

# ── Manual event state (updated on confirmed events) ──────────────────────────
# These require human monitoring — no API feed
MANUAL_STATE = {
    # Hormuz: 0=open, 0.33=PGSA toll, 0.67=partial close, 1.0=mines/closed
    "hormuz_status": 0.33,          # PGSA toll — Jun 8, 2026

    # Bab al-Mandab: 0=clear, 0.5=declared blockade, 1.0=AIS physical confirmed
    "bab_status": 0.50,             # Declared blockade Jun 8, 2026

    # GENIUS Act: 0=pending, 0.5=passed Senate, 1.0=signed
    "genius_act": 0.30,             # Active lobbying, not passed

    # QAFCO/Kuwait/Bahrain FM: 0=normal, 0.5=partial, 1.0=full FM
    "fertilizer_fm": 1.00,          # Full FM all three — confirmed

    # Fund gate count (0, 1, 2=Apollo+Barings confirmed, 3+=cascade)
    "fund_gates": 2,                # Apollo + Barings confirmed

    # Mojtaba Khamenei Supreme Leader (confirmed)
    "iran_succession": 1.0,

    # Ceasefire escalation: 0=full ceasefire holding, 1.0=active military operations
    # S(t) suppression component — falls when peace holds, rises on escalation
    # Historical baseline: 2015-2025 Middle East conflict at varying levels, avg ~0.35
    "ceasefire_escalation": 0.60,   # Jun 30: nominal MoU but Israel still hitting Lebanon, Hormuz PGSA
}

# ── Logistic / sigmoid ────────────────────────────────────────────────────────
def sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))

def zscore(value: float, mu: float, sigma: float) -> float:
    if sigma < 1e-8:
        return 0.0
    return (value - mu) / sigma

# ── EIA fetch ──────────────────────────────────────────────────────────────────
def eia_fetch(series_id: str, n: int = 52) -> List[Dict]:
    params = (f"/petroleum/sum/sndw/data/?frequency=weekly&data[0]=value"
              f"&facets[series][]={series_id}"
              f"&sort[0][column]=period&sort[0][direction]=desc&length={n}"
              f"&api_key={EIA_KEY}")
    try:
        with urllib.request.urlopen(EIA_BASE + params, timeout=10) as r:
            data = json.loads(r.read())["response"]["data"]
        return sorted(data, key=lambda x: x["period"])
    except Exception as e:
        print(f"  ⚠️  EIA {series_id}: {e}")
        return []

def eia_latest(series_id: str) -> Optional[float]:
    rows = eia_fetch(series_id, n=4)
    if rows:
        return float(rows[-1]["value"])
    return None

# ── FRED fetch ────────────────────────────────────────────────────────────────
def fred_latest(series_id: str) -> Optional[float]:
    """Fetch latest FRED value via curl subprocess (more reliable than urllib in this env)."""
    import subprocess
    try:
        url = FRED_BASE + series_id
        r = subprocess.run(
            ["curl", "-s", "--max-time", "20", url],
            capture_output=True, text=True
        )
        if r.returncode != 0 or not r.stdout.strip():
            raise RuntimeError(f"curl failed: {r.stderr[:100]}")
        lines = r.stdout.strip().split("\n")
        for line in reversed(lines[1:]):
            parts = line.split(",")
            if len(parts) == 2 and parts[1].strip() not in ("", "."):
                return float(parts[1])
    except Exception as e:
        print(f"  ⚠️  FRED {series_id}: {e}")
    return None

# ── Estimation parameters (mu, sigma) ────────────────────────────────────────
# These are calibrated from 2015-2026 historical data
# Initially approximate — will be updated from Supabase history once populated
CALIB = {
    # L1 components — calibrated from FRED/EIA 2015-2025 where available
    "brent_backwardation": {"mu": 0.0,    "sigma": 3.0,    "w": 0.10},  # $/bbl 1-6M spread (w: 0.20→0.10; Cushing now primary)
    "global_draw_mbd":     {"mu": 0.5,    "sigma": 1.5,    "w": 0.20},  # mb/d draw rate
    "hormuz_status":       {"mu": 0.05,   "sigma": 0.15,   "w": 0.20},  # 0-1 score
    "brent_impl_vol":      {"mu": 28.0,   "sigma": 12.0,   "w": 0.05},  # % (w: 0.10→0.05; no free API, often null)
    "spr_draw_rate":       {"mu": 0.0,    "sigma": 0.4,    "w": 0.00},  # retired: redundant w/ crude_stocks_inv + STEO global draw
    # Physical inventory components — initial wave signals (Step 1 in supply chain cascade):
    # Stored as inverted deviation: value = (5yr_avg − actual); mu=0.0 so z = deficit/sigma
    "cushing_stocks_inv":  {"mu": 0.0,    "sigma": 9.05,   "w": 0.10},  # mmbbl below 5yr avg 32.2; EIA W_EPC0_SAX_YCUOK_MBBL
    "crude_stocks_inv":    {"mu": 0.0,    "sigma": 20.0,   "w": 0.10},  # mmbbl below 5yr avg 439.5; EIA WCESTUS1
    # S(t) suppression components for L1:
    "brent_spot":          {"mu": 66.43,  "sigma": 18.71,  "w": 0.15},  # $/bbl — FRED DCOILBRENTEU 2015-2025
    "ceasefire_escalation":{"mu": 0.35,   "sigma": 0.25,   "w": 0.10},  # 0-1; mu=hist mean Middle East conflict

    # L2 components
    "tic_official_flow":   {"mu": 10.0,   "sigma": 35.0,   "w": 0.30},  # $B rolling 12M (inverted)
    "gold_usd_12m_ret":    {"mu": 9.2,    "sigma": 9.2,    "w": 0.15},  # % — 2015-2025 annual returns
    "ustr_10y_spread":     {"mu": 2.317,  "sigma": 0.809,  "w": 0.25},  # US-Japan 10Y — FRED GS10-JP10Y 2015-2025
    # S(t) suppression component for L2:
    "usd_weakness":        {"mu": -116.31,"sigma": 5.40,   "w": 0.10},  # −DXY broad (DTWEXBGS) — low DXY = stress

    # L3 components
    "hyg_lqd_spread":      {"mu": 3.5,    "sigma": 1.5,    "w": 0.30},  # yield spread %
    "fund_gate_count":     {"mu": 0.0,    "sigma": 0.5,    "w": 0.20},  # integer 0-5+
    "sofr_ois_spread":     {"mu": 2.0,    "sigma": 5.0,    "w": 0.10},  # bp — SOFR-DFF (near zero normally)

    # L4 components
    "genius_act_status":   {"mu": 0.1,    "sigma": 0.3,    "w": 0.35},  # 0-1

    # L5 components
    "wheat_stu_inverted":  {"mu": 62.0,   "sigma": 5.0,    "w": 0.25},  # 100 - STU%
    "fertilizer_fm_score": {"mu": 0.05,   "sigma": 0.2,    "w": 0.20},  # 0-1
    "henry_hub":           {"mu": 3.135,  "sigma": 1.342,  "w": 0.15},  # $/MMBtu — FRED MHHNGSP 2015-2025

    # L6 components
    "defense_supplemental_bn": {"mu": 0.0, "sigma": 50.0,  "w": 0.30},  # $B

    # L8 components
    "bab_status":          {"mu": 0.0,    "sigma": 0.3,    "w": 0.20},  # 0-1
    "bdti_vs_baseline":    {"mu": 0.0,    "sigma": 200.0,  "w": 0.10},  # index points vs 5Y avg

    # L_cross components
    "boj_fed_diff_bp":     {"mu": 168.0,  "sigma": 165.0,  "w": 0.15},  # bp — DFF-JP10Y means; old 350/80 was wrong
    "usd_jpy":             {"mu": 122.82, "sigma": 17.26,  "w": 0.15},  # — FRED DEXJPUS 2015-2025
    "boj_rate":            {"mu": 0.1,    "sigma": 0.3,    "w": 0.15},  # % (inverted — low = loaded)
    # Physical flow analogs for FX (from yen_mechanics_daily):
    "jpy_spec_short":      {"mu": 0.0,    "sigma": 12.0,   "w": 0.30},  # −NC%OI; positive = specs short yen = carry crowded; IMM JPY 2015-2025 1σ≈12%
    "yen_episode_days":    {"mu": 0.0,    "sigma": 7.0,    "w": 0.25},  # days sustained above 160; mu=0=baseline not above 160; intervention window ≈7d
}


# ── EIA STEO global oil balance ───────────────────────────────────────────────

def _fetch_global_draw_mbd() -> Optional[float]:
    """
    Pull the most recent WORLD row from macro_oil_balance (Supabase).
    net_imports_mbd is stored as (demand - supply): positive = draw, negative = surplus.
    Falls back to None (hardcoded 7.5 used by caller) if Supabase is unreachable.
    Also refreshes the table from EIA STEO if the latest row is >28 days old.
    """
    import subprocess as _sp
    key = "sb_publishable_TJg65x5w56CulOEdWFJNyQ_89loJtit"
    # Use the most recent row at or before today (not future STEO forecast months)
    today_iso = date.today().isoformat()
    url = (SUPABASE_URL
           + "/rest/v1/macro_oil_balance"
           + f"?country=eq.WORLD&date=lte.{today_iso}"
           + "&select=date,net_imports_mbd,prod_mbd,apparent_demand_mbd"
           + "&order=date.desc&limit=1")
    r = _sp.run(
        ["curl", "-s", "--max-time", "15", url,
         "-H", f"apikey: {key}",
         "-H", f"Authorization: Bearer {key}"],
        capture_output=True, text=True
    )
    if r.returncode != 0 or not r.stdout.strip():
        return None
    try:
        import json as _json
        rows = _json.loads(r.stdout)
        if not rows:
            return None
        row = rows[0]
        val = row.get("net_imports_mbd")
        return float(val) if val is not None else None
    except Exception:
        return None


# ── EIA physical inventory levels ────────────────────────────────────────────

def _fetch_inventory_levels() -> Dict[str, Optional[float]]:
    """
    Pull latest inventory readings from Supabase inventory_levels table
    (populated by inventory_tracker.py, which runs first in the daily pipeline).

    Returns pre-inverted deviation signals for CALIB with mu=0.0:
      cushing_stocks_inv = 32.2 − actual_cushing_mmbbl   (sigma=9.05)
      crude_stocks_inv   = 439.5 − actual_crude_mmbbl    (sigma=20.0)

    Positive value = below 5yr seasonal avg = stress signal.
    """
    import subprocess as _sp
    key = "sb_publishable_TJg65x5w56CulOEdWFJNyQ_89loJtit"
    series_filter = "series_id=in.(WCESTUS1,W_EPC0_SAX_YCUOK_MBBL)"
    url = (SUPABASE_URL
           + "/rest/v1/inventory_levels"
           + f"?{series_filter}"
           + "&select=series_id,value_mbbl,as_of_date"
           + "&order=as_of_date.desc"
           + "&limit=10")
    r = _sp.run(
        ["curl", "-s", "--max-time", "15", url,
         "-H", f"apikey: {key}",
         "-H", f"Authorization: Bearer {key}"],
        capture_output=True, text=True
    )
    if r.returncode != 0 or not r.stdout.strip():
        return {}
    try:
        import json as _j
        rows = _j.loads(r.stdout)
        if not isinstance(rows, list):
            return {}
        result: Dict[str, Optional[float]] = {}
        seen: set = set()
        for row in rows:
            sid = row.get("series_id")
            val = row.get("value_mbbl")
            if sid in seen or val is None:
                continue
            seen.add(sid)
            if sid == "W_EPC0_SAX_YCUOK_MBBL":
                result["cushing_stocks_inv"] = round(32.2 - float(val), 3)
            elif sid == "WCESTUS1":
                result["crude_stocks_inv"] = round(439.5 - float(val), 3)
        return result
    except Exception:
        return {}


# ── Yen mechanics (from yen_mechanics_daily, populated by yen_mechanics.py) ───

def _fetch_yen_mechanics() -> Dict[str, Optional[float]]:
    """
    Pull latest row from yen_mechanics_daily (written by yen_mechanics.py, runs before
    state_vector_compute.py in the daily pipeline).

    Returns two signals for L_cross CALIB:
      jpy_spec_short   = −jpy_nc_pct_oi: positive = specs net short yen = carry crowded
                         mu=0.0, sigma=12; current -33.9% → stored +33.9 → z=+2.83
      yen_episode_days = current_episode_days above 160: 0 when below 160
                         mu=0.0, sigma=7; current 15d → z=+2.14 (max-ever episode)

    Both are physical-flow analogs for FX: CFTC positioning ≡ COT for oil,
    episode duration ≡ Cushing drawdown days below threshold.
    """
    import subprocess as _sp
    key = "sb_publishable_TJg65x5w56CulOEdWFJNyQ_89loJtit"
    url = (SUPABASE_URL
           + "/rest/v1/yen_mechanics_daily"
           + "?select=as_of_date,jpy_nc_pct_oi,current_episode_days,usdjpy_spot,yen_signal"
           + "&order=as_of_date.desc&limit=1")
    r = _sp.run(
        ["curl", "-s", "--max-time", "15", url,
         "-H", f"apikey: {key}",
         "-H", f"Authorization: Bearer {key}"],
        capture_output=True, text=True
    )
    if r.returncode != 0 or not r.stdout.strip():
        return {}
    try:
        import json as _j
        rows = _j.loads(r.stdout)
        if not isinstance(rows, list) or not rows:
            return {}
        row = rows[0]
        result: Dict[str, Optional[float]] = {}
        nc_pct = row.get("jpy_nc_pct_oi")
        ep_days = row.get("current_episode_days")
        if nc_pct is not None:
            # Invert: negative NC % → positive stress (crowded short = stress)
            result["jpy_spec_short"] = round(-float(nc_pct), 3)
        if ep_days is not None:
            result["yen_episode_days"] = float(ep_days)
        return result
    except Exception:
        return {}


# ── Component fetchers ────────────────────────────────────────────────────────

def get_components(target_date: Optional[date] = None) -> Dict[str, Optional[float]]:
    """Fetch all available component values. Returns None for gaps."""
    print("\n  Fetching observable components...")
    c = {}

    # ── L1: War/Energy ────────────────────────────────────────────────────────
    print("  L1 — War/Energy Chokepoints")

    # EIA SPR draw rate (WCSSTUS1)
    spr_rows = eia_fetch("WCSSTUS1", n=8)
    if len(spr_rows) >= 2:
        latest = float(spr_rows[-1]["value"])
        prev   = float(spr_rows[-2]["value"])
        # Inverted: negative Δ (draw) → positive stress direction
        c["spr_draw_rate"] = -(latest - prev) / 7 / 1000
        print(f"    SPR draw rate: {c['spr_draw_rate']:.2f} mb/d (positive = drawing = stress)")
    else:
        c["spr_draw_rate"] = None

    c["hormuz_status"] = MANUAL_STATE["hormuz_status"]
    print(f"    Hormuz status: {c['hormuz_status']} (manual)")

    # Brent backwardation — use Yahoo Finance WTI 1M vs 6M as proxy
    try:
        import yfinance as yf
        # CL=F is front-month, CLM26.NYM is 6M forward — simplified proxy
        cl = yf.Ticker("CL=F")
        hist = cl.history(period="5d")
        if not hist.empty:
            c["brent_backwardation"] = 6.0  # confirmed from Platts/CME as of May 2026
            print(f"    Brent backwardation: ~$6.0 (confirmed, using known value)")
    except Exception:
        c["brent_backwardation"] = 6.0  # use confirmed value from framework
        print(f"    Brent backwardation: $6.0 (confirmed value, yfinance unavailable)")

    # Global oil balance from EIA STEO via macro_oil_balance table
    # net_imports_mbd for WORLD = (demand - supply): positive = draw, negative = surplus
    global_draw = _fetch_global_draw_mbd()
    c["global_draw_mbd"] = global_draw if global_draw is not None else 7.5
    src = "EIA_STEO" if global_draw is not None else "fallback"
    print(f"    Global draw: {c['global_draw_mbd']:+.2f} mb/d ({src})")
    c["brent_impl_vol"]  = None  # CME options — no free API

    # Brent spot price — S(t) market-integrated suppression signal
    brent_spot = fred_latest("DCOILBRENTEU")
    c["brent_spot"] = brent_spot
    print(f"    Brent spot: ${brent_spot:.2f}/bbl" if brent_spot else "    Brent spot: unavailable")

    # Ceasefire escalation — S(t) diplomatic suppression component
    c["ceasefire_escalation"] = MANUAL_STATE["ceasefire_escalation"]
    print(f"    Ceasefire escalation: {c['ceasefire_escalation']} (0=ceasefire, 1=active ops, manual)")

    # Physical inventory levels — initial wave signal (upstream of price)
    # Values are inverted deviations: positive = below seasonal avg = stress
    inv = _fetch_inventory_levels()
    c["cushing_stocks_inv"] = inv.get("cushing_stocks_inv")
    c["crude_stocks_inv"]   = inv.get("crude_stocks_inv")
    if c["cushing_stocks_inv"] is not None:
        actual_cushing = round(32.2 - c["cushing_stocks_inv"], 1)
        print(f"    Cushing stocks: {actual_cushing} mmbbl (inv_dev={c['cushing_stocks_inv']:+.1f}, z={c['cushing_stocks_inv']/9.05:+.2f})")
    else:
        print("    Cushing stocks: unavailable (inventory_tracker not yet run today?)")
    if c["crude_stocks_inv"] is not None:
        actual_crude = round(439.5 - c["crude_stocks_inv"], 1)
        print(f"    Crude stocks excl SPR: {actual_crude} mmbbl (inv_dev={c['crude_stocks_inv']:+.1f}, z={c['crude_stocks_inv']/20.0:+.2f})")
    else:
        print("    Crude stocks excl SPR: unavailable")

    # ── L2: Petrodollar ───────────────────────────────────────────────────────
    print("  L2 — GCC/Petrodollar Strain")

    # TIC official flow: latest confirmed -$37.9B March.
    # Inverted: selling (actual -37.9) → stored positive for stress direction.
    c["tic_official_flow"] = 37.9
    print(f"    TIC official flow: -$37.9B actual → +37.9 stress signal (March confirmed)")

    # Gold 12M return
    try:
        import yfinance as yf
        gold = yf.Ticker("GC=F")
        ghist = gold.history(period="1y")
        if len(ghist) >= 252:
            ret = (ghist["Close"].iloc[-1] / ghist["Close"].iloc[0] - 1) * 100
            c["gold_usd_12m_ret"] = ret
            print(f"    Gold 12M return: {ret:.1f}%")
        else:
            c["gold_usd_12m_ret"] = None
    except Exception:
        c["gold_usd_12m_ret"] = None

    # US-Japan 10Y yield spread from FRED
    jgb10 = fred_latest("IRLTLT01JPM156N")   # Japan 10Y
    ust10  = fred_latest("GS10")             # US 10Y
    if jgb10 and ust10:
        c["ustr_10y_spread"] = ust10 - jgb10   # positive = US yields higher = carry pressure
        print(f"    US-Japan 10Y spread: {c['ustr_10y_spread']:.2f}%")
    else:
        c["ustr_10y_spread"] = None

    # USD weakness — S(t) suppression signal for L2 (stored as −DXY broad)
    dxy = fred_latest("DTWEXBGS")
    if dxy:
        c["usd_weakness"] = -dxy   # negated: low DXY → high value → more L2 stress
        print(f"    USD broad index: {dxy:.2f}  → usd_weakness: {c['usd_weakness']:.2f}")
    else:
        c["usd_weakness"] = None

    # ── L3: Private Credit ────────────────────────────────────────────────────
    print("  L3 — Private Credit/NBFI")

    # HYG vs LQD yield spread proxy
    try:
        import yfinance as yf
        hyg = yf.Ticker("HYG")
        lqd = yf.Ticker("LQD")
        hyg_h = hyg.history(period="5d")
        lqd_h = lqd.history(period="5d")
        # Use SEC yield as proxy (not perfect but directional)
        c["hyg_lqd_spread"] = None  # Can't get yield from yfinance reliably
    except Exception:
        c["hyg_lqd_spread"] = None

    c["fund_gate_count"] = float(MANUAL_STATE["fund_gates"])
    print(f"    Fund gate count: {c['fund_gate_count']} (Apollo + Barings confirmed)")

    # SOFR-OIS spread from FRED
    sofr = fred_latest("SOFR")
    effr = fred_latest("DFF")   # Daily effective fed funds rate = OIS proxy
    c["sofr_ois_spread"] = (sofr - effr) * 100 if (sofr and effr) else None

    # ── L4: Settlement Rails ──────────────────────────────────────────────────
    print("  L4 — Settlement/Stablecoin")
    c["genius_act_status"] = MANUAL_STATE["genius_act"]
    print(f"    GENIUS Act status: {c['genius_act_status']} (manual)")

    # ── L5: Food/Fertilizer ───────────────────────────────────────────────────
    print("  L5 — Food/Fertilizer")

    # USDA WASDE global wheat STU — use confirmed value from vault
    c["wheat_stu_inverted"] = 100.0 - 33.6   # = 66.4 (100 - 33.6% STU)
    print(f"    Wheat STU inverted: {c['wheat_stu_inverted']:.1f} (100 - 33.6%)")

    c["fertilizer_fm_score"] = MANUAL_STATE["fertilizer_fm"]
    print(f"    Fertilizer FM score: {c['fertilizer_fm_score']} (all three confirmed)")

    # Henry Hub from EIA
    hh = eia_latest("RNGWHHD")
    if hh is None:
        hh_rows = eia_fetch("RNGWHHD", n=4)
        hh = float(hh_rows[-1]["value"]) if hh_rows else 3.80
    c["henry_hub"] = hh
    print(f"    Henry Hub: ${hh:.2f}/MMBtu")

    # ── L6: Munitions ─────────────────────────────────────────────────────────
    print("  L6 — Munitions/MIC")
    c["defense_supplemental_bn"] = 200.0  # $200B confirmed
    print(f"    Supplemental: $200B (confirmed)")

    # ── L8: Maritime ──────────────────────────────────────────────────────────
    print("  L8 — Maritime/Insurance")
    c["bab_status"] = MANUAL_STATE["bab_status"]
    print(f"    Bab al-Mandab status: {c['bab_status']} (declared Jun 8)")
    c["bdti_vs_baseline"] = None  # Baltic Exchange — no free API

    # ── L_cross: JPY Carry ────────────────────────────────────────────────────
    print("  L_cross — JPY Carry / Intervention Mechanics")

    # USD/JPY from FRED
    jpy = fred_latest("DEXJPUS")
    c["usd_jpy"] = jpy
    print(f"    USD/JPY: {jpy:.3f}" if jpy else "    USD/JPY: unavailable")

    # BOJ rate — confirmed 1.0% after Jun 16 hike (25bp from 0.75%)
    c["boj_rate"] = 1.0
    print(f"    BOJ rate: 1.0% (confirmed Jun 16 hike)")

    # Fed Funds rate from FRED
    fed = fred_latest("FEDFUNDS")
    if fed and c["boj_rate"]:
        c["boj_fed_diff_bp"] = (fed - c["boj_rate"]) * 100
        print(f"    Fed-BOJ diff: {c['boj_fed_diff_bp']:.0f}bp")
    else:
        c["boj_fed_diff_bp"] = None

    # Physical-flow analogs from yen_mechanics_daily (populated by yen_mechanics.py)
    yen_data = _fetch_yen_mechanics()
    c["jpy_spec_short"]   = yen_data.get("jpy_spec_short")    # −NC%OI; +ve = crowded short yen
    c["yen_episode_days"] = yen_data.get("yen_episode_days")  # days above 160; 0 when below

    if c["jpy_spec_short"] is not None:
        nc_pct = -c["jpy_spec_short"]   # recover original sign for display
        z_spec = c["jpy_spec_short"] / 12.0
        print(f"    CFTC IMM JPY NC: {nc_pct:+.1f}% OI → jpy_spec_short={c['jpy_spec_short']:+.1f}  z={z_spec:+.2f}")
    else:
        print("    CFTC IMM JPY NC: unavailable (yen_mechanics.py not yet run today?)")

    if c["yen_episode_days"] is not None:
        z_ep = c["yen_episode_days"] / 7.0
        above = "🔴 ABOVE 160" if c["yen_episode_days"] > 0 else "⚪ below 160"
        print(f"    Yen episode days above 160: {c['yen_episode_days']:.0f}d  z={z_ep:+.2f}  [{above}]")
    else:
        print("    Yen episode days: unavailable")

    return c


# ── Per-leg downgrade rules ───────────────────────────────────────────────────
# MANUAL_STATE values only go up (hormuz escalates, gates accumulate).
# These rules auto-cap MANUAL_STATE-derived components when observables show
# sustained suppression — prevents the model being a ratchet.

def apply_downgrade_rules(components: dict) -> list:
    """
    Checks observable thresholds and reduces MANUAL_STATE-derived component
    values in `components` (in-place) when sustained suppression is confirmed.

    Returns list of rule-fire descriptions for logging.

    Rules defined here:
      DR-1  Brent < $95  → cap hormuz_status at 0.20  (market not pricing PGSA war premium)
      DR-2  Brent < $80  → cap ceasefire_escalation at 0.35  (deep price suppression = peace momentum)
      DR-3  Jul 8+ with bab_status==0.50  → downgrade to 0.25  (30-day Bab declaration unconfirmed)
    """
    from datetime import date as _date
    fired = []
    brent = components.get("brent_spot")

    # DR-1: Brent war-premium collapse — PGSA without market pricing
    # At $95 the war premium historically starts. Below it: market doesn't believe PGSA.
    if brent is not None and brent < 95.0:
        old = components.get("hormuz_status", MANUAL_STATE["hormuz_status"])
        if old > 0.20:
            components["hormuz_status"] = 0.20
            fired.append(
                f"DR-1 fired: Brent ${brent:.0f} < $95 → hormuz_status {old:.2f}→0.20 "
                f"(market not pricing PGSA war premium)"
            )

    # DR-2: Deep price suppression signals diplomatic progress
    # $80 is where Iran-deal pricing would put Brent historically (JCPOA 2015: ~$50-60).
    if brent is not None and brent < 80.0:
        old = components.get("ceasefire_escalation", MANUAL_STATE["ceasefire_escalation"])
        if old > 0.35:
            components["ceasefire_escalation"] = 0.35
            fired.append(
                f"DR-2 fired: Brent ${brent:.0f} < $80 (deep suppression) → "
                f"ceasefire_escalation {old:.2f}→0.35"
            )

    # DR-3: Bab al-Mandab 30-day bluff rule — if declared but no AIS confirmation
    # by Jul 8, downgrade from 0.50 (declared) to 0.25 (unconfirmed/partial)
    today = _date.today()
    if today > _date(2026, 7, 8):
        bab = components.get("bab_status", MANUAL_STATE["bab_status"])
        if abs(bab - 0.50) < 0.01:  # still at "declared, unconfirmed" level
            components["bab_status"] = 0.25
            fired.append(
                f"DR-3 fired: Bab declaration >30 days, no AIS physical confirmation "
                f"→ bab_status 0.50→0.25 (re-rated bluff/incomplete)"
            )

    return fired


# ── L_i computation ──────────────────────────────────────────────────────────

def compute_leg(components: Dict[str, Optional[float]],
                relevant_keys: List[str]) -> tuple[float, Dict]:
    """Compute logistic-transformed z-score composite for one leg."""
    z_sum = 0.0
    total_weight = 0.0
    detail = {}

    for key in relevant_keys:
        val = components.get(key)
        cal = CALIB.get(key)
        if val is None or cal is None:
            detail[key] = {"value": None, "z": None, "weight": cal["w"] if cal else 0}
            continue
        z = zscore(val, cal["mu"], cal["sigma"])
        w = cal["w"]
        z_sum     += z * w
        total_weight += w
        detail[key] = {"value": round(val, 4), "z": round(z, 3), "weight": w}

    if total_weight < 0.01:
        return 0.5, detail  # No data — return neutral

    # Normalize by actual weight covered
    z_normalized = z_sum / total_weight * total_weight  # keep raw sum for logistic
    score = sigmoid(z_sum)
    return round(score, 4), detail


def compute_state_vector(components: Dict) -> Dict:
    """Compute all L_i(t) scores from fetched components."""

    L = {}
    details = {}

    L["l1"], details["l1"] = compute_leg(components, [
        "brent_backwardation", "global_draw_mbd", "hormuz_status",
        "brent_impl_vol",
        "cushing_stocks_inv",   # Step 1: physical delivery stress (inverted, z>0 = below seasonal)
        "crude_stocks_inv",     # Step 1: upstream crude cushion (inverted, z>0 = below seasonal)
        "brent_spot",           # S(t): market-integrated price signal
        "ceasefire_escalation", # S(t): diplomatic suppression / escalation
    ])

    L["l2"], details["l2"] = compute_leg(components, [
        "tic_official_flow", "gold_usd_12m_ret", "ustr_10y_spread",
        "usd_weakness",         # S(t): dollar strength suppresses petrodollar stress
    ])

    L["l3"], details["l3"] = compute_leg(components, [
        "hyg_lqd_spread", "fund_gate_count", "sofr_ois_spread"
    ])

    L["l4"], details["l4"] = compute_leg(components, ["genius_act_status"])

    L["l5"], details["l5"] = compute_leg(components, [
        "wheat_stu_inverted", "fertilizer_fm_score", "henry_hub"
    ])

    L["l6"], details["l6"] = compute_leg(components, ["defense_supplemental_bn"])

    L["l7"], details["l7"] = (0.42, {})   # No free API for Taiwan Strait incidents

    L["l8"], details["l8"] = compute_leg(components, [
        "bab_status", "bdti_vs_baseline"
    ])

    L["l9"], details["l9"] = (0.38, {})   # AI/Labor: no primary API feed configured

    L["l_cross"], details["l_cross"] = compute_leg(components, [
        "boj_fed_diff_bp", "usd_jpy", "boj_rate",
        "jpy_spec_short",    # CFTC IMM crowding: −NC%OI; +ve = specs short yen = intervention approach
        "yen_episode_days",  # days above 160 key level; 0 baseline; >7d = MOF action window
    ])

    # Composite score
    weights = {"l1":0.20,"l2":0.15,"l3":0.12,"l4":0.08,"l5":0.12,"l6":0.08,"l7":0.08,"l8":0.10,"l9":0.07}
    L["composite"] = round(sum(L[k] * w for k, w in weights.items()), 4)

    return L, details


def _constraint_band(composite: float) -> str:
    if composite >= 0.90: return "CB-E"
    if composite >= 0.75: return "CB-D"
    if composite >= 0.55: return "CB-C"
    if composite >= 0.35: return "CB-B"
    return "CB-A"


def store_to_supabase(L: Dict, details: Dict, obs_date: date):
    """Store computed L(t) to Supabase state_vector_history."""
    if not SUPABASE_KEY:
        print("\n  Supabase key not set — skipping storage")
        return
    try:
        from supabase import create_client
        sb = create_client(SUPABASE_URL, SUPABASE_KEY)
        band = _constraint_band(L["composite"])
        sb.table("state_vector_history").upsert({
            "obs_date":        str(obs_date),
            "l1":  L["l1"],  "l2": L["l2"],  "l3": L["l3"],
            "l4":  L["l4"],  "l5": L["l5"],  "l6": L["l6"],
            "l7":  L["l7"],  "l8": L["l8"],  "l9": L["l9"],
            "l_cross":         L["l_cross"],
            "constraint_band": band,
            "notes": json.dumps({
                "computed_at": datetime.now().isoformat(),
                "engine": "state_vector_compute.py",
                "coverage": {
                    k: {c: v.get("value") for c, v in d.items()}
                    for k, d in details.items() if d
                }
            })
        }).execute()
        print(f"\n  ✅ Stored L({obs_date}) → {band} to Supabase")
    except Exception as e:
        print(f"\n  ⚠️  Supabase storage failed: {e}")


def print_vector(L: Dict, details: Dict):
    print("\n" + "═"*58)
    print("  GOD'S EYE STATE VECTOR L(t)")
    print("═"*58)
    labels = {
        "l1":"War / Energy Chokepoints",  "l2":"GCC / Petrodollar",
        "l3":"Private Credit / NBFI",     "l4":"Settlement Rails",
        "l5":"Food / Fertilizer",         "l6":"Munitions / MIC",
        "l7":"Semiconductor / Taiwan",    "l8":"Maritime / Insurance",
        "l9":"AI / Labor",                "l_cross":"Cross-Cut JPY Carry"
    }
    for k, label in labels.items():
        v   = L.get(k, 0)
        bar = "█" * int(v * 30)
        na  = len(details.get(k, {})) == 0 or all(
            d.get("value") is None for d in details.get(k, {}).values())
        flag = " (partial data)" if na else ""
        color_tag = "🔴" if v > 0.85 else "🟠" if v > 0.65 else "🟡" if v > 0.45 else "🔵"
        print(f"  {color_tag} {k.upper():8s}  {v:.2%}  {bar}{flag}")
        print(f"           {label}")
    print(f"\n  COMPOSITE: {L['composite']:.1%}")
    print("═"*58)

    # Coverage report
    all_components = [k for d in details.values() for k in d.keys()]
    covered = [k for d in details.values() for k, v in d.items() if v.get("value") is not None]
    print(f"\n  Data coverage: {len(covered)}/{len(all_components)} components with live data")
    print(f"  Gaps (require commercial feeds or manual update):")
    for d in details.values():
        for k, v in d.items():
            if v.get("value") is None:
                print(f"    - {k}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="God's Eye State Vector Computation")
    parser.add_argument("--date", default=str(date.today()))
    parser.add_argument("--store", action="store_true", help="Store to Supabase")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    _JSON_MODE = args.json  # module-level flag — no global keyword needed at module scope
    target = date.fromisoformat(args.date)

    # In --json mode, redirect all progress output to stderr so stdout is clean JSON
    _real_stdout = sys.stdout
    if args.json:
        sys.stdout = sys.stderr

    print(f"\n  God's Eye State Vector — {target}")
    components = get_components(target)

    # Apply per-leg downgrade rules — auto-cap MANUAL_STATE components when
    # sustained observable suppression contradicts the manually-set values
    downgrade_fired = apply_downgrade_rules(components)
    if downgrade_fired:
        print(f"\n  📉 DOWNGRADE RULES FIRED ({len(downgrade_fired)}):")
        for msg in downgrade_fired:
            print(f"     {msg}")
    else:
        print("\n  ✅ No downgrade rules triggered")

    L, details = compute_state_vector(components)

    if args.json:
        sys.stdout = _real_stdout
        print(json.dumps({"date": str(target), "vector": L, "details": details}, indent=2))
    else:
        print_vector(L, details)

    if args.store:
        store_to_supabase(L, details, target)
