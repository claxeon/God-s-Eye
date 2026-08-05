#!/usr/bin/env python3
"""
God's Eye — USD/JPY Intervention Mechanics Tracker
====================================================
Mirrors market_mechanics.py for oil but targets the yen carry-trade / BOJ dilemma.

The Central Question (BOJ Trap):
  Raise rates → defend yen → BUT: JGB prices fall, banks bleed mark-to-market losses,
                                    debt service on 250% GDP debt balloons → fiscal crisis
  Hold rates → yen weakens → import inflation, political pressure, intervention spending
  Resolution: MOF/BOJ prefer FX spot intervention (temporary) over rate hikes (structural)
              until the Fed pivots and closes the rate differential.

This script identifies WHERE in that cycle Japan currently is.

Data cascade (analogous to oil physical flows):
  [1] Rate differential (US 10yr - JGB 10yr) → structural yen weakness driver
  [2] Speculative positioning (CFTC IMM JPY) → crowded carry trade pressure
  [3] Key level approach (160/161/162) → MOF verbal + actual intervention threshold
  [4] Intervention detection (daily drop ≥ 1.5%) → MOF/BOJ actual USD selling
  [5] Price sustainability after intervention → determines next episode

Data sources (all free):
  FRED: DEXJPUS (USD/JPY daily), DGS10 (US 10yr), IRLTLT01JPM156N (JGB 10yr monthly)
  CFTC: Legacy COT dataset 6dca-aqww — JAPANESE YEN (CME IMM futures)
  Supabase: yen_mechanics_daily persistence

Run: python3 yen_mechanics.py
Schedule: daily via state_vector_daily.sh (after inventory_tracker.py)
"""

import json
import math
import os
import re
import subprocess
import sys
from datetime import date, timedelta
from typing import Optional

# ── Config ────────────────────────────────────────────────────────────────────
SUPA_URL = "https://snykuqyceqpplnzmyksp.supabase.co"
SUPA_KEY = "sb_publishable_TJg65x5w56CulOEdWFJNyQ_89loJtit"
FRED_BASE = "https://fred.stlouisfed.org/graph/fredgraph.csv?id="

# ── JGB daily proxy ──────────────────────────────────────────────────────────
#
# FRED's IRLTLT01JPM156N is Japan's 10Y benchmark, but it is a MONTHLY series
# (dated first-of-month, and it is a monthly AVERAGE of dailies). Through the
# 2026 fiscal-driven JGB selloff it ran two months behind: on 2026-08-03 it
# still read 2.670% (June) against an actual ~2.83% -- a 16bp understatement,
# on the single input that matters most for the BOJ bond-vs-currency trap.
#
# There is no free daily JGB yield feed (FRED has none; yfinance has no JGB
# yield ticker). So: anchor on the last true FRED monthly value and extrapolate
# with a JGB bond ETF, inverted through modified duration.
#
#   dP/P = -D_mod * dy   ->   dy(pp) = -(dP/P) * 100 / D_mod
#
# TICKER CHOICE MATTERS -- verified 2026-08-03, and two obvious candidates are
# traps:
#   2561.T  iShares Core JAPAN Government Bond ETF   <- correct, JGBs
#   1482.T  iShares Core 7-10yr US TREASURY, JPY-hedged   <- NOT JGBs
#   1487.T  Listed Index Fund US Bond (currency hedge)    <- NOT JGBs
# Only 2561.T holds Japanese government bonds. The other two are US Treasury
# funds that merely trade in Tokyo in yen.
#
# CALIBRATION: regressing monthly FRED yield changes on 2561.T monthly-MEAN
# returns (n=76, 2019-2026) gives corr = -0.667, t = -7.69, implied modified
# duration ~6.0y. Matching monthly means matters -- comparing FRED's monthly
# average against a month's FIRST close destroys the signal (corr collapses to
# ~0.01 and implied duration to ~0).
#
# ACCURACY: on 2026-08-03 the proxy returned 2.872% against a reported actual
# of 2.83% -- a 4bp error, versus 16bp for the stale FRED value. A single-point
# fit today would prefer D=7.0 (1bp error), but 6.0 is used because it is
# estimated from 76 observations rather than tuned to one.
JGB_PROXY_TICKER = "2561.T"
JGB_PROXY_DURATION = 6.0     # years, modified; regression-derived


def jgb_daily_proxy(anchor_yield):
    """Estimate today's JGB 10Y from the FRED monthly anchor + 2561.T.

    Returns (estimated_yield_pct, meta_dict) or (None, {}) if unavailable.
    Never raises -- a failed proxy must fall back to the stale FRED value
    rather than break the daily pipeline."""
    if anchor_yield is None:
        return None, {}
    try:
        import datetime as _dt
        import pandas as pd
        import yfinance as yf

        rows = fred_series("IRLTLT01JPM156N", n_rows=36)
        if not rows:
            return None, {}
        anchor_date = pd.Timestamp(rows[-1][0])

        px = yf.Ticker(JGB_PROXY_TICKER).history(period="2y")["Close"].dropna()
        if len(px) < 60:
            return None, {}
        px.index = px.index.tz_localize(None)

        # anchor price = mean of the anchor month, matching FRED's convention
        month = px[(px.index >= anchor_date) &
                   (px.index < anchor_date + pd.DateOffset(months=1))]
        if len(month) == 0:
            return None, {}
        p_anchor, p_now = float(month.mean()), float(px.iloc[-1])
        chg = (p_now / p_anchor) - 1.0
        est = anchor_yield - chg * 100.0 / JGB_PROXY_DURATION

        # Sanity band: reject absurd extrapolations rather than publish them.
        if not (0.0 <= est <= 8.0) or abs(est - anchor_yield) > 1.5:
            return None, {}

        return round(est, 3), {
            "anchor_date": str(anchor_date.date()),
            "anchor_yield": anchor_yield,
            "anchor_age_days": (_dt.date.today() - anchor_date.date()).days,
            "px_change_pct": round(100 * chg, 3),
            "ticker": JGB_PROXY_TICKER,
            "duration": JGB_PROXY_DURATION,
        }
    except Exception:
        return None, {}
CFTC_LEGACY_URL = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"
TODAY = date.today().isoformat()

# Key intervention thresholds (MOF track record)
KEY_LEVELS = [160.0, 161.0, 162.0]
INTERVENTION_THRESHOLD_PCT = -1.5   # single-day % drop signaling MOF action

# Historical known MOF/BOJ FX interventions (for backtest calibration)
KNOWN_INTERVENTIONS = {
    "2022-09-22": 145.9,  # First in 24 years — shocked markets
    "2022-10-21": 151.9,
    "2022-10-24": 149.1,
    "2024-04-29": 160.2,  # April 2024 intervention at 160
    "2024-05-01": 157.6,
    "2024-07-11": 161.9,  # July 2024 — held 161 briefly, intervention pulled it back
    "2024-07-12": 158.3,
}


# ── FRED fetch ────────────────────────────────────────────────────────────────

def fred_series(series_id: str, n_rows: int = 800) -> list:
    """
    Pull daily (or monthly) FRED series. Returns sorted list of (date_str, float).
    n_rows controls how many recent rows to keep for backtest analysis.
    """
    r = subprocess.run(
        ["curl", "-s", "--max-time", "30", FRED_BASE + series_id],
        capture_output=True, text=True
    )
    if r.returncode != 0 or not r.stdout.strip():
        return []
    rows = []
    for line in r.stdout.strip().split("\n")[1:]:  # skip header
        parts = line.split(",")
        if len(parts) == 2 and parts[1].strip() not in ("", "."):
            try:
                rows.append((parts[0].strip(), float(parts[1].strip())))
            except ValueError:
                pass
    return rows[-n_rows:] if len(rows) > n_rows else rows


def fred_latest(series_id: str) -> Optional[float]:
    rows = fred_series(series_id, n_rows=10)
    return rows[-1][1] if rows else None


# ── CFTC JPY positioning ──────────────────────────────────────────────────────

def fetch_cftc_jpy() -> Optional[dict]:
    """
    Pull latest CFTC legacy COT non-commercial (speculative) positioning for JPY futures.
    Dataset: 6dca-aqww (Legacy COT — covers financial futures including CME IMM JPY)
    Contract: 'JAPANESE YEN' FutOnly
    Non-commercial net = speculative hedge fund directional bet on yen.
    Positive net = speculators net LONG yen (USD/JPY falling pressure)
    Negative net = speculators net SHORT yen / long USD (carry trade crowding)
    """
    import urllib.parse
    where = "contract_market_name='JAPANESE YEN' AND futonly_or_combined='FutOnly'"
    encoded_url = (CFTC_LEGACY_URL
                   + "?%24limit=1"
                   + "&%24order=report_date_as_yyyy_mm_dd%20DESC"
                   + "&%24where=" + urllib.parse.quote(where))
    cmd = ["curl", "-s", "--max-time", "25", encoded_url]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        return None
    try:
        rows = json.loads(r.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(rows, list) or not rows:
        return None
    row = rows[0]

    def _i(k): return int(row.get(k) or 0)

    oi       = _i("open_interest_all")
    nc_long  = _i("noncomm_positions_long_all")
    nc_short = _i("noncomm_positions_short_all")
    nc_net   = nc_long - nc_short

    # Date is ISO format: 2026-06-23T00:00:00.000
    raw_date = row.get("report_date_as_yyyy_mm_dd", "")
    report_date = raw_date[:10] if raw_date else ""

    return {
        "report_date":   report_date,
        "open_interest": oi,
        "nc_long":       nc_long,
        "nc_short":      nc_short,
        "nc_net":        nc_net,
        "nc_pct_oi":     round(nc_net / oi * 100, 2) if oi else None,
        "contract":      row.get("contract_market_name", "JAPANESE YEN"),
    }


# ── Key-level backtest ────────────────────────────────────────────────────────

def analyze_key_levels(usdjpy_rows: list) -> dict:
    """
    For each key level (160, 161, 162):
      - Count distinct 'episodes' where USD/JPY breached and sustained above level
      - Episode = consecutive days above level (gap of 1 day resets)
      - Track duration, max sustained, first touch date
      - Detect intervention days (drop ≥ 1.5%) within/after each episode

    Returns dict with per-level stats and current episode state.
    """
    results = {}
    intervention_days = []

    # Detect intervention days across full history
    prev_val = None
    for dt_str, val in usdjpy_rows:
        if prev_val is not None and prev_val > 0:
            pct = (val - prev_val) / prev_val * 100
            if pct <= INTERVENTION_THRESHOLD_PCT:
                intervention_days.append((dt_str, val, pct))
        prev_val = val

    for level in KEY_LEVELS:
        episodes = []
        current_ep = None

        for dt_str, val in usdjpy_rows:
            above = val >= level
            if above:
                if current_ep is None:
                    current_ep = {"start": dt_str, "days": 1, "max": val, "end": None}
                else:
                    current_ep["days"] += 1
                    current_ep["max"] = max(current_ep["max"], val)
            else:
                if current_ep is not None:
                    current_ep["end"] = dt_str
                    episodes.append(current_ep)
                    current_ep = None

        # If currently in episode, don't close it
        in_episode_now = current_ep is not None
        if in_episode_now:
            episodes.append(current_ep)  # include open episode

        # Post-episode intervention: did MOF act within 5 days of first touch?
        episode_interventions = []
        for ep in episodes:
            start_dt = ep["start"]
            ep_ints = [d for d, v, p in intervention_days
                       if d >= start_dt and (ep["end"] is None or d <= ep["end"])]
            episode_interventions.append(len(ep_ints))

        results[level] = {
            "episode_count":           len(episodes),
            "current_episode_days":    current_ep["days"] if in_episode_now else 0,
            "max_sustained_days":      max((ep["days"] for ep in episodes), default=0),
            "first_touch_date":        episodes[0]["start"] if episodes else None,
            "last_episode_start":      episodes[-1]["start"] if episodes else None,
            "in_episode_now":          in_episode_now,
            "episode_peak":            max((ep["max"] for ep in episodes), default=None),
            "interventions_in_episodes": sum(episode_interventions),
            "episodes": episodes,
        }

    return {
        "levels": results,
        "intervention_days": intervention_days,
        "total_intervention_days": len(intervention_days),
    }


# ── Signal classification ─────────────────────────────────────────────────────

def classify_signal(spot: Optional[float], daily_pct: Optional[float],
                    rate_diff: Optional[float], nc_pct_oi: Optional[float],
                    level_analysis: dict) -> dict:
    """
    Classify current yen dynamics.

    dominant_pressure:
      'carry_trade'    — rate differential wide, specs short yen, no intervention
      'intervention'   — spot intervention detected today
      'boj_pivot'      — rate differential narrowing, specs covering shorts
      'stress_zone'    — above key level, intervention imminent
      'mixed'          — no clear dominant

    yen_signal: 'intervention_detected' | 'stress_zone' | 'watch' | 'stable'
    """
    today_intervention = (daily_pct is not None and daily_pct <= INTERVENTION_THRESHOLD_PCT)
    above_160 = (spot or 0) >= 160.0
    above_161 = (spot or 0) >= 161.0

    # Rate differential: >3.5% = carry strongly favors USD, yen structurally weak
    carry_dominant = (rate_diff is not None and rate_diff > 3.0)
    carry_extreme  = (rate_diff is not None and rate_diff > 3.5)

    # Speculative positioning (IMM JPY FutOnly runs 20-40% OI range, not commodity scale)
    # nc_pct_oi < -25% = historically extreme short yen / crowded carry (squeeze risk)
    # nc_pct_oi < -15% = heavily short yen (carry dominant)
    # nc_pct_oi > -5%  = covering / pivoting to long yen
    spec_short    = (nc_pct_oi is not None and nc_pct_oi < -15)
    spec_extreme  = (nc_pct_oi is not None and nc_pct_oi < -25)
    spec_covering = (nc_pct_oi is not None and nc_pct_oi > -5)

    if today_intervention:
        yen_signal = "intervention_detected"
        dominant   = "intervention"
    elif above_161:
        yen_signal = "stress_zone"
        dominant   = "stress_zone"
    elif above_160 and (carry_extreme or spec_extreme):
        yen_signal = "stress_zone"
        dominant   = "carry_trade"
    elif above_160:
        yen_signal = "watch"
        dominant   = "carry_trade" if carry_dominant else "mixed"
    elif spec_covering and not carry_extreme:
        yen_signal = "watch"
        dominant   = "boj_pivot"
    else:
        yen_signal = "stable"
        dominant   = "carry_trade" if carry_dominant else "mixed"

    # Build notes
    notes = []
    if rate_diff:
        notes.append(f"Rate diff {rate_diff:.2f}% — {'carry extreme, structural yen weakness' if carry_extreme else 'moderate carry pressure'}")
    if nc_pct_oi is not None:
        carry_desc = ('EXTREME crowded short — squeeze fuel loaded' if spec_extreme
                      else 'crowded short yen carry' if spec_short
                      else 'covering / pivoting' if spec_covering else 'neutral')
        notes.append(f"Specs {nc_pct_oi:+.1f}% OI — {carry_desc}")
    if today_intervention:
        notes.append(f"Intervention signal: {daily_pct:.1f}% daily drop exceeds -1.5% threshold")
    if above_161:
        notes.append("USD/JPY >161 — historically MOF acts within days at this level (Jul 2024 precedent)")
    elif above_160:
        notes.append("USD/JPY >160 — MOF watch zone, verbal intervention typical before spot action")

    # BOJ trap note
    if carry_extreme:
        notes.append("BOJ trap: closing 350bps+ differential requires ~14 hikes at 25bps each; fiscal risk caps pace")

    return {
        "yen_signal":         yen_signal,
        "dominant_pressure":  dominant,
        "hypothesis_notes":   "; ".join(notes),
        "today_intervention": today_intervention,
    }


# ── Dashboard ─────────────────────────────────────────────────────────────────

def print_dashboard(spot: Optional[float], daily_pct: Optional[float],
                    us10: Optional[float], jgb10: Optional[float],
                    cot: Optional[dict], level_stats: dict,
                    sig: dict):

    print("\n" + "═" * 72)
    print("  USD/JPY INTERVENTION MECHANICS — God's Eye")
    print(f"  {TODAY}")
    print("═" * 72)

    # Spot + daily
    rate_diff = (us10 - jgb10) if (us10 and jgb10) else None
    spot_str = f"{spot:.3f}" if spot else "N/A"
    dpct_str = f"{daily_pct:+.2f}%" if daily_pct is not None else "N/A"
    flag = " 🚨 INTERVENTION SIGNAL" if sig["today_intervention"] else ""
    print(f"\n  USD/JPY Spot:       {spot_str}   (daily: {dpct_str}){flag}")

    # Key level indicators
    def _level_bar(lvl):
        s = level_stats["levels"].get(lvl, {})
        in_ep = s.get("in_episode_now", False)
        ep_ct = s.get("episode_count", 0)
        cur_d = s.get("current_episode_days", 0)
        max_d = s.get("max_sustained_days", 0)
        peak  = s.get("episode_peak")
        first = s.get("first_touch_date", "—")
        ep_icon = "🔴 ABOVE" if in_ep else "⚪ below"
        print(f"  {lvl:>6.0f} level:   {ep_icon}  episodes={ep_ct}  "
              + (f"current={cur_d}d  " if in_ep else "")
              + f"max_sustained={max_d}d  "
              + (f"peak={peak:.3f}  " if peak else "")
              + f"first={first}")

    print()
    for lv in KEY_LEVELS:
        _level_bar(lv)

    # Intervention history in this data
    int_days = level_stats.get("intervention_days", [])
    print(f"\n  Intervention events (≥1.5% daily drop): {len(int_days)} detected in data window")
    for dt, val, pct in int_days[-5:]:  # show last 5
        known = " ← KNOWN MOF" if dt in KNOWN_INTERVENTIONS else ""
        print(f"    {dt}  USD/JPY {val:.3f}  {pct:+.2f}%{known}")

    # Rate differential — the BOJ trap
    print(f"\n  ── Rate Differential (BOJ Bond-vs-Currency Dilemma) ──")
    print(f"  US 10yr:          {us10:.2f}%"  if us10  else "  US 10yr:          N/A")
    print(f"  JGB 10yr:         {jgb10:.2f}%"  if jgb10 else "  JGB 10yr:         N/A")
    if rate_diff:
        bps = rate_diff * 100
        hikes_needed = rate_diff / 0.25
        print(f"  Differential:     {rate_diff:.2f}%  ({bps:.0f} bps)")
        print(f"  BOJ hikes to close gap: ~{hikes_needed:.0f} × 25bps — fiscal risk caps pace")
    print()
    print("  BOJ Trap:")
    print("    Raise rates → defend yen → BUT JGB prices fall → bank losses → fiscal blowout")
    print("    Hold rates  → yen weakens → import inflation → political pressure → intervention")
    print("    Resolution: MOF FX intervention (temporary) until Fed cuts close the differential")

    # CFTC IMM positioning
    print(f"\n  ── CFTC IMM JPY Futures (Speculative / Non-Commercial) ──")
    if cot:
        print(f"  Report date:      {cot['report_date']}")
        print(f"  Open interest:    {cot['open_interest']:,}")
        net = cot["nc_net"]
        pct = cot["nc_pct_oi"] or 0
        bar = ("▼" * min(abs(int(pct)), 20)) if pct < 0 else ("▲" * min(int(pct), 20))
        direction = "SHORT JPY / LONG USD (carry trade)" if net < 0 else "LONG JPY (yen bulls)"
        print(f"  NC Net:           {net:>+10,}  ({pct:>+6.1f}% OI)  {bar}")
        print(f"  Interpretation:   {direction}")
        if pct < -25:
            print(f"  🚨 EXTREME crowded short ({pct:.1f}% OI) — violent squeeze risk on any intervention")
        elif pct < -15:
            print(f"  🔴 Heavily short yen ({pct:.1f}% OI) — carry trade dominant")
        elif pct < -5:
            print(f"  🟠 Moderately short yen ({pct:.1f}% OI) — carry trade active")
        elif pct > 5:
            print(f"  🟢 Specs net long yen ({pct:.1f}% OI) — BOJ pivot expectations dominating")
    else:
        print("  [CFTC data unavailable — legacy COT may need TFF dataset check]")

    # Signal
    print(f"\n  ── Composite Signal ──")
    icons = {
        "intervention_detected": "🚨",
        "stress_zone": "🔴",
        "watch": "🟠",
        "stable": "⚪",
    }
    print(f"  Yen signal:       {icons.get(sig['yen_signal'], '?')} {sig['yen_signal'].upper()}")
    print(f"  Dominant driver:  {sig['dominant_pressure'].upper()}")
    if sig.get("hypothesis_notes"):
        print("\n  Evidence:")
        for note in sig["hypothesis_notes"].split("; "):
            if note:
                print(f"  → {note}")

    print("═" * 72)


# ── Supabase upsert ───────────────────────────────────────────────────────────

def upsert_row(row: dict) -> bool:
    r = subprocess.run(
        ["curl", "-s", "--max-time", "30", "-X", "POST",
         "-H", "Content-Type: application/json",
         "-H", f"apikey: {SUPA_KEY}",
         "-H", f"Authorization: Bearer {SUPA_KEY}",
         "-H", "Prefer: resolution=merge-duplicates,return=minimal",
         "-d", json.dumps(row),
         SUPA_URL + "/rest/v1/yen_mechanics_daily?on_conflict=as_of_date"],
        capture_output=True, text=True
    )
    return r.returncode == 0


# ── Main ──────────────────────────────────────────────────────────────────────

def run_yen_mechanics() -> dict:
    print(f"\n  God's Eye — USD/JPY Mechanics  ({TODAY})")
    print("  " + "─" * 50)

    # 1. USD/JPY daily history (FRED, ~3 years)
    print("  Fetching USD/JPY history from FRED DEXJPUS...", end=" ", flush=True)
    usdjpy_rows = fred_series("DEXJPUS", n_rows=800)
    print(f"{len(usdjpy_rows)} daily rows")

    spot = usdjpy_rows[-1][1] if usdjpy_rows else None
    daily_pct = None
    daily_change = None
    if len(usdjpy_rows) >= 2:
        prev = usdjpy_rows[-2][1]
        daily_change = round(spot - prev, 3) if spot else None
        daily_pct = round((spot - prev) / prev * 100, 3) if (spot and prev) else None

    # 2. Rate data (FRED)
    print("  Fetching US 10yr (DGS10) and JGB 10yr (IRLTLT01JPM156N)...", end=" ", flush=True)
    us10       = fred_latest("DGS10")
    jgb10_fred = fred_latest("IRLTLT01JPM156N")
    print(f"US={us10:.2f}%  JGB(FRED monthly)={jgb10_fred:.2f}%"
          if (us10 and jgb10_fred) else "partial/unavailable")

    # 2b. JGB daily proxy — see jgb_daily_proxy() for why this exists.
    jgb10_proxy, proxy_meta = jgb_daily_proxy(jgb10_fred)
    jgb10 = jgb10_proxy if jgb10_proxy is not None else jgb10_fred
    if jgb10_proxy is not None:
        print(f"  JGB daily proxy: {jgb10_proxy:.3f}%  "
              f"(anchor {proxy_meta['anchor_date']} {proxy_meta['anchor_yield']:.3f}%, "
              f"{proxy_meta['anchor_age_days']}d old; 2561.T {proxy_meta['px_change_pct']:+.2f}%, "
              f"D={JGB_PROXY_DURATION}y)  → +{100*(jgb10_proxy-jgb10_fred):.0f}bp vs stale FRED")
    else:
        print("  JGB daily proxy: unavailable — falling back to stale FRED monthly")

    rate_diff = round(us10 - jgb10, 3) if (us10 and jgb10) else None
    if rate_diff is not None:
        print(f"  Differential (US10Y − JGB10Y, proxy-adjusted): {rate_diff:.2f}%")

    # 3. CFTC JPY positioning
    print("  Fetching CFTC IMM JPY positioning...", end=" ", flush=True)
    cot = fetch_cftc_jpy()
    if cot:
        print(f"NC net={cot['nc_net']:+,}  ({cot['nc_pct_oi']:+.1f}% OI)  date={cot['report_date']}")
    else:
        print("FAILED or no data — check TFF dataset")

    # 4. Key level backtest
    print("  Running key-level backtest (160/161/162)...")
    level_stats = analyze_key_levels(usdjpy_rows)
    for lv in KEY_LEVELS:
        s = level_stats["levels"].get(lv, {})
        print(f"    {lv:.0f}: {s.get('episode_count', 0)} episodes, "
              f"max_sustained={s.get('max_sustained_days', 0)}d, "
              f"in_episode_now={'YES' if s.get('in_episode_now') else 'no'}")

    # 5. Signal classification
    sig = classify_signal(
        spot, daily_pct, rate_diff,
        cot["nc_pct_oi"] if cot else None,
        level_stats
    )

    # 6. Dashboard
    print_dashboard(spot, daily_pct, us10, jgb10, cot, level_stats, sig)

    # 7. Build current episode metrics
    lv160 = level_stats["levels"].get(160.0, {})
    lv161 = level_stats["levels"].get(161.0, {})
    lv162 = level_stats["levels"].get(162.0, {})

    row = {
        "as_of_date":              TODAY,
        "usdjpy_spot":             spot,
        "usdjpy_daily_change":     daily_change,
        "usdjpy_daily_change_pct": daily_pct,
        "above_160":               (spot or 0) >= 160.0,
        "above_161":               (spot or 0) >= 161.0,
        "above_162":               (spot or 0) >= 162.0,
        "episodes_above_160":      lv160.get("episode_count"),
        "episodes_above_161":      lv161.get("episode_count"),
        "episodes_above_162":      lv162.get("episode_count"),
        "current_episode_days":    lv160.get("current_episode_days", 0),
        "max_sustained_above_160": lv160.get("max_sustained_days"),
        "intervention_flag":       sig["today_intervention"],
        "intervention_magnitude":  daily_pct if sig["today_intervention"] else None,
        "us_10yr_yield":           us10,
        "jgb_10yr_yield":          jgb10,          # proxy when available, else stale FRED
        "rate_differential":       rate_diff,
        "cot_report_date":         cot["report_date"] if cot else None,
        "jpy_nc_long":             cot["nc_long"] if cot else None,
        "jpy_nc_short":            cot["nc_short"] if cot else None,
        "jpy_nc_net":              cot["nc_net"] if cot else None,
        "jpy_open_interest":       cot["open_interest"] if cot else None,
        "jpy_nc_pct_oi":           cot["nc_pct_oi"] if cot else None,
        "yen_signal":              sig["yen_signal"],
        "dominant_pressure":       sig["dominant_pressure"],
        "hypothesis_notes":        sig.get("hypothesis_notes"),
    }

    ok = upsert_row(row)
    print(f"\n  {'✓' if ok else '⚠️  FAILED'} Supabase upsert: yen_mechanics_daily")
    return row


if __name__ == "__main__":
    run_yen_mechanics()
