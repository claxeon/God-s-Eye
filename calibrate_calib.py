#!/usr/bin/env python3
"""
God's Eye — CALIB Historical Calibration
Pulls 2015-2025 FRED series and computes proper mu/sigma for each entry.
Also proposes new S(t) suppression components.

Run: python3 calibrate_calib.py
No API key needed (FRED CSV endpoint is public).
"""

import urllib.request
import csv
import io
import math
from datetime import date
from typing import List, Tuple, Dict, Optional

FRED_BASE = "https://fred.stlouisfed.org/graph/fredgraph.csv"
START = date(2015, 1, 1)
END   = date(2025, 12, 31)


def fetch_fred(series_id: str) -> List[Tuple[date, float]]:
    """Fetch FRED series filtered to 2015-2025 using cosd/coed params."""
    try:
        url = (f"{FRED_BASE}?id={series_id}"
               f"&cosd={START.isoformat()}&coed={END.isoformat()}")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            content = r.read().decode()
        reader = csv.reader(io.StringIO(content))
        next(reader)  # skip header row
        rows = []
        for row in reader:
            if len(row) != 2:
                continue
            try:
                d = date.fromisoformat(row[0].strip())
                if row[1].strip() in ("", "."):
                    continue
                v = float(row[1])
                rows.append((d, v))
            except (ValueError, TypeError):
                continue
        return rows
    except Exception as e:
        print(f"  ✗ ERROR fetching {series_id}: {e}")
        return []


def stats(values: List[float]) -> Tuple[float, float]:
    """Compute mean and sample std, rounded to 4 decimal places."""
    if not values:
        return 0.0, 1.0
    n   = len(values)
    mu  = sum(values) / n
    var = sum((v - mu) ** 2 for v in values) / (n - 1) if n > 1 else 1.0
    return round(mu, 4), round(math.sqrt(var), 4)


def to_monthly(rows: List[Tuple[date, float]]) -> Dict[Tuple[int, int], float]:
    """Collapse to last-value-of-month dict keyed by (year, month)."""
    m: Dict[Tuple[int, int], float] = {}
    for d, v in rows:
        m[(d.year, d.month)] = v  # later dates overwrite earlier in same month
    return m


def main():
    print("=" * 70)
    print("  GOD'S EYE — CALIB Historical Calibration  (2015-2025)")
    print("=" * 70)

    results: Dict[str, dict] = {}
    raw: Dict[str, List[Tuple[date, float]]] = {}

    # ── Fetch primary series ──────────────────────────────────────────────────
    series_to_fetch = [
        ("DCOILBRENTEU",    "Brent Crude Spot ($/bbl)"),
        ("DEXJPUS",         "USD/JPY exchange rate"),
        ("GOLDAMGBD228NLBM","Gold price (USD/troy oz)"),
        ("GS10",            "US 10Y Treasury yield (%)"),
        ("IRLTLT01JPM156N", "Japan 10Y JGB yield (%)"),
        ("DFF",             "Daily Effective Fed Funds (%)"),
        ("SOFR",            "SOFR (%)"),
        ("FEDFUNDS",        "Monthly Fed Funds target (%)"),
        ("MHHNGSP",         "Henry Hub spot ($/MMBtu)"),
        ("DTWEXBGS",        "USD Broad Currency Index (DXY proxy)"),
    ]

    print("\n  Fetching FRED series...")
    for sid, label in series_to_fetch:
        rows = fetch_fred(sid)
        raw[sid] = rows
        status = f"✓ {len(rows)} obs" if rows else "✗ FAILED"
        print(f"    {sid:25s}  {status:15s}  {label}")

    # ── 1. Brent spot price — new L1 S(t) component ──────────────────────────
    print("\n[1] brent_spot  (L1 — new suppression-direction component)")
    brent_vals = [v for _, v in raw["DCOILBRENTEU"]]
    mu_b, sig_b = stats(brent_vals)
    results["brent_spot"] = {"mu": mu_b, "sigma": sig_b, "w": 0.15, "n": len(brent_vals),
                              "note": "NEW — stress-direction: high price = stress; CALIB mu from 2015-2025 mean"}
    print(f"  n={len(brent_vals)},  mu=${mu_b:.2f}/bbl,  sigma=${sig_b:.2f}/bbl")
    print(f"  At $72 (Jun 30): z={(72-mu_b)/sig_b:+.3f}")
    print(f"  At $113 (Jun 8): z={(113-mu_b)/sig_b:+.3f}")

    # ── 2. USD/JPY ────────────────────────────────────────────────────────────
    print("\n[2] usd_jpy  (L_cross)")
    jpy_vals = [v for _, v in raw["DEXJPUS"]]
    mu_jpy, sig_jpy = stats(jpy_vals)
    results["usd_jpy"] = {"mu": mu_jpy, "sigma": sig_jpy,
                           "old_mu": 130.0, "old_sigma": 15.0, "n": len(jpy_vals)}
    print(f"  n={len(jpy_vals)},  mu={mu_jpy:.2f},  sigma={sig_jpy:.2f}  (old: mu=130, sigma=15)")
    if jpy_vals:
        print(f"  Range: {min(jpy_vals):.1f} – {max(jpy_vals):.1f}")

    # ── 3. Gold 12M rolling return ────────────────────────────────────────────
    print("\n[3] gold_usd_12m_ret  (L2)")
    gold_monthly = to_monthly(raw["GOLDAMGBD228NLBM"])
    gm_sorted = sorted(gold_monthly.items())
    returns_12m = []
    for i in range(12, len(gm_sorted)):
        _, v_past  = gm_sorted[i - 12]
        _, v_now   = gm_sorted[i]
        returns_12m.append((v_now / v_past - 1) * 100)
    mu_gold, sig_gold = stats(returns_12m)
    results["gold_usd_12m_ret"] = {"mu": mu_gold, "sigma": sig_gold,
                                    "old_mu": 5.0, "old_sigma": 15.0, "n": len(returns_12m)}
    print(f"  n={len(returns_12m)} monthly returns,  mu={mu_gold:.2f}%,  sigma={sig_gold:.2f}%  (old: mu=5, sigma=15)")

    # ── 4. US-Japan 10Y spread ────────────────────────────────────────────────
    print("\n[4] ustr_10y_spread  (L2)  =  GS10 − IRLTLT01JPM156N")
    us_m  = to_monthly(raw["GS10"])
    jp_m  = to_monthly(raw["IRLTLT01JPM156N"])
    spread_vals = []
    for k in us_m:
        if k in jp_m:
            spread_vals.append(us_m[k] - jp_m[k])
    mu_sp, sig_sp = stats(spread_vals)
    results["ustr_10y_spread"] = {"mu": mu_sp, "sigma": sig_sp,
                                   "old_mu": 1.5, "old_sigma": 0.8, "n": len(spread_vals)}
    print(f"  n={len(spread_vals)},  mu={mu_sp:.4f}%,  sigma={sig_sp:.4f}%  (old: mu=1.5, sigma=0.8)")
    if spread_vals:
        print(f"  Range: {min(spread_vals):.2f}% – {max(spread_vals):.2f}%")

    # ── 5. Henry Hub ──────────────────────────────────────────────────────────
    print("\n[5] henry_hub  (L5)")
    hh_vals = [v for _, v in raw["MHHNGSP"]]
    mu_hh, sig_hh = stats(hh_vals)
    results["henry_hub"] = {"mu": mu_hh, "sigma": sig_hh,
                             "old_mu": 3.0, "old_sigma": 1.5, "n": len(hh_vals)}
    print(f"  n={len(hh_vals)},  mu=${mu_hh:.4f}/MMBtu,  sigma=${sig_hh:.4f}/MMBtu  (old: mu=3.0, sigma=1.5)")
    if hh_vals:
        print(f"  Range: ${min(hh_vals):.2f} – ${max(hh_vals):.2f}")

    # ── 6. BOJ-Fed rate diff in bp ────────────────────────────────────────────
    print("\n[6] boj_fed_diff_bp  (L_cross)  =  (DFF − IRLTLT01JPM156N) × 100")
    dff_m = to_monthly(raw["DFF"])
    diff_bp_vals = []
    for k in dff_m:
        if k in jp_m:
            diff_bp_vals.append((dff_m[k] - jp_m[k]) * 100)
    mu_diff, sig_diff = stats(diff_bp_vals)
    results["boj_fed_diff_bp"] = {"mu": mu_diff, "sigma": sig_diff,
                                   "old_mu": 350.0, "old_sigma": 80.0, "n": len(diff_bp_vals)}
    print(f"  n={len(diff_bp_vals)},  mu={mu_diff:.1f}bp,  sigma={sig_diff:.1f}bp  (old: mu=350, sigma=80)")
    if diff_bp_vals:
        print(f"  Range: {min(diff_bp_vals):.0f} – {max(diff_bp_vals):.0f}bp")

    # ── 7. SOFR-OIS spread (SOFR - DFF) in bp ────────────────────────────────
    print("\n[7] sofr_ois_spread  (L3)  =  (SOFR − DFF) × 100")
    sofr_dict = dict(raw["SOFR"])
    dff_dict  = dict(raw["DFF"])
    sofr_ois_vals = []
    for d, sv in sofr_dict.items():
        if d in dff_dict:
            sofr_ois_vals.append((sv - dff_dict[d]) * 100)
    mu_sofr, sig_sofr = stats(sofr_ois_vals)
    results["sofr_ois_spread"] = {"mu": mu_sofr, "sigma": sig_sofr,
                                   "old_mu": 10.0, "old_sigma": 8.0, "n": len(sofr_ois_vals)}
    print(f"  n={len(sofr_ois_vals)},  mu={mu_sofr:.4f}bp,  sigma={sig_sofr:.4f}bp  (old: mu=10, sigma=8)")
    if sofr_ois_vals:
        print(f"  Range: {min(sofr_ois_vals):.2f} – {max(sofr_ois_vals):.2f}bp")

    # ── 8. USD weakness index — new L2 S(t) component ────────────────────────
    # Low DXY = petrodollar stress. Store as -DXY so "higher" = weaker dollar = more stress.
    print("\n[8] usd_weakness  (L2 — new suppression-direction component)")
    dxy_vals = [v for _, v in raw["DTWEXBGS"]]
    mu_dxy, sig_dxy = stats(dxy_vals)
    # Store negated: mu for usd_weakness = -mu_dxy, same sigma
    neg_mu  = round(-mu_dxy, 4)
    results["usd_weakness"] = {"mu": neg_mu, "sigma": sig_dxy, "w": 0.10, "n": len(dxy_vals),
                                "note": "NEW — stored as -DXY so low DXY → high z → more L2 stress"}
    print(f"  DXY raw:  n={len(dxy_vals)},  mu={mu_dxy:.2f},  sigma={sig_dxy:.2f}")
    print(f"  Stored as -DXY:  mu={neg_mu:.2f},  sigma={sig_dxy:.2f}")
    if dxy_vals:
        print(f"  DXY range: {min(dxy_vals):.1f} – {max(dxy_vals):.1f}")
        # Show current impact (DXY ~105 as of mid-2026)
        dxy_now = 105.0
        z_now = (-dxy_now - neg_mu) / sig_dxy
        print(f"  At DXY=105 (est. Jun 30): z_usd_weakness={z_now:+.3f}  {'(suppressing L2 ✓)' if z_now < 0 else '(adding L2 stress)'}")

    # ── Summary table ─────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("  SUMMARY — ALL CHANGES")
    print("=" * 70)
    print(f"\n  {'KEY':28s}  {'OLD mu':>10}  {'NEW mu':>10}  {'OLD sig':>9}  {'NEW sig':>9}  {'n':>5}")
    print("  " + "-" * 66)

    existing = {
        "brent_spot":       {"mu": "—",     "sigma": "—"},
        "usd_jpy":          {"mu": 130.0,   "sigma": 15.0},
        "gold_usd_12m_ret": {"mu": 5.0,     "sigma": 15.0},
        "ustr_10y_spread":  {"mu": 1.5,     "sigma": 0.8},
        "henry_hub":        {"mu": 3.0,     "sigma": 1.5},
        "boj_fed_diff_bp":  {"mu": 350.0,   "sigma": 80.0},
        "sofr_ois_spread":  {"mu": 10.0,    "sigma": 8.0},
        "usd_weakness":     {"mu": "—",     "sigma": "—"},
    }

    for k, r in results.items():
        old = existing.get(k, {})
        old_mu_s  = str(old.get("mu", "—"))
        old_sig_s = str(old.get("sigma", "—"))
        new_flag  = " ← NEW" if old.get("mu") == "—" else ""
        print(f"  {k:28s}  {old_mu_s:>10}  {r['mu']:>10}  {old_sig_s:>9}  {r['sigma']:>9}  {r['n']:>5}{new_flag}")

    print("\n  PASTE BLOCK — copy into state_vector_compute.py CALIB dict:")
    print("  " + "-" * 66)
    # Only print the changed/new entries — others (brent_backwardation, etc.) stay as-is
    entries = {
        "brent_spot":       (results["brent_spot"]["mu"],       results["brent_spot"]["sigma"],       0.15),
        "usd_jpy":          (results["usd_jpy"]["mu"],          results["usd_jpy"]["sigma"],          0.20),
        "gold_usd_12m_ret": (results["gold_usd_12m_ret"]["mu"], results["gold_usd_12m_ret"]["sigma"], 0.15),
        "ustr_10y_spread":  (results["ustr_10y_spread"]["mu"],  results["ustr_10y_spread"]["sigma"],  0.25),
        "henry_hub":        (results["henry_hub"]["mu"],        results["henry_hub"]["sigma"],        0.15),
        "boj_fed_diff_bp":  (results["boj_fed_diff_bp"]["mu"],  results["boj_fed_diff_bp"]["sigma"],  0.20),
        "sofr_ois_spread":  (results["sofr_ois_spread"]["mu"],  results["sofr_ois_spread"]["sigma"],  0.10),
        "usd_weakness":     (results["usd_weakness"]["mu"],     results["usd_weakness"]["sigma"],     0.10),
    }
    for k, (mu, sig, w) in entries.items():
        tag = "  # NEW — S(t) component" if k in ("brent_spot", "usd_weakness") else ""
        print(f'    "{k}":{" "*(24-len(k))}  {{"mu": {mu:>10},  "sigma": {sig:>9},  "w": {w}}},{tag}')
    print("  " + "-" * 66)


if __name__ == "__main__":
    main()
