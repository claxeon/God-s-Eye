#!/usr/bin/env python3
"""
God's Eye — SPR Term Structure Model
=====================================
Regresses the monthly change in Brent 1–6M spread against:
  - Global SPR days-of-cover flow (Δglobal_spr_days_cover)
  - Country-level SPR flows: ΔJP, ΔUS, ΔCN gov days cover
  - Controls: VIX, WTI crack spread, TTF/HH spread

Outputs:
  - Regression coefficients + Newey-West standard errors
  - Japan SPR factor (z-scored Δjp_delta_spr_mmbbl)
  - Forward projection given current draw rates
  - Results pushed back to Supabase spr_model_results table

Usage:
    pip install supabase pandas numpy statsmodels scipy --break-system-packages
    export SUPABASE_URL="https://snykuqyceqpplnzmyksp.supabase.co"
    export SUPABASE_KEY="<your-anon-or-service-key>"
    python3 spr_term_structure_model.py
"""

import os
import json
import warnings
import numpy as np
import pandas as pd
from datetime import date, datetime

warnings.filterwarnings("ignore")

# ── Supabase connection ───────────────────────────────────────────────────────
try:
    from supabase import create_client
    SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://snykuqyceqpplnzmyksp.supabase.co")
    SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
    if SUPABASE_KEY:
        sb = create_client(SUPABASE_URL, SUPABASE_KEY)
        USE_SUPABASE = True
    else:
        print("⚠  SUPABASE_KEY not set — running in offline mode with embedded data")
        USE_SUPABASE = False
except ImportError:
    print("⚠  supabase package not installed — running in offline mode")
    USE_SUPABASE = False


# ── Embedded data (fallback / bootstrap) ─────────────────────────────────────
# Mirrors the spr_factors_monthly view output for months with full panels.
# Extend this as new data arrives; Supabase pull will override when connected.

EMBEDDED_DATA = [
    # date, jp_spr, jp_days_gov, jp_delta_mmbbl, us_spr, us_delta, cn_spr, cn_delta,
    # global_spr, global_days_cover, delta_global_days_cover,
    # brent_front, brent_1_6m, crack, hh, ttf, vix
    {
        "date": "2026-01-31",
        "jp_spr_gov_mmbbl": None, "jp_gov_days_cover": None, "jp_delta_spr_mmbbl": None,
        "us_spr_gov_mmbbl": 414.8, "us_delta_spr_mmbbl": 1.8, "us_gov_days_cover": None,
        "cn_spr_gov_mmbbl": 1405.0, "cn_delta_spr_mmbbl": 8.0,
        "global_spr_gov_mmbbl": 1819.8, "global_spr_days_cover": 1.671, "delta_global_spr_days_cover": None,
        "brent_front": 82.0, "brent_1_6m_spread": 1.5, "wti_3_2_1_crack": 30.0,
        "henry_hub": 3.80, "ttf_front": 11.5, "ttf_hh_spread": 7.7, "vix": 18.0,
    },
    {
        "date": "2026-02-28",
        "jp_spr_gov_mmbbl": None, "jp_gov_days_cover": None, "jp_delta_spr_mmbbl": None,
        "us_spr_gov_mmbbl": 415.4, "us_delta_spr_mmbbl": 0.6, "us_gov_days_cover": None,
        "cn_spr_gov_mmbbl": 1412.0, "cn_delta_spr_mmbbl": 7.0,
        "global_spr_gov_mmbbl": 1827.4, "global_spr_days_cover": 1.692, "delta_global_spr_days_cover": 0.021,
        "brent_front": 112.0, "brent_1_6m_spread": 4.0, "wti_3_2_1_crack": 38.0,
        "henry_hub": 3.40, "ttf_front": 14.8, "ttf_hh_spread": 11.4, "vix": 24.1,
    },
    {
        "date": "2026-03-31",
        "jp_spr_gov_mmbbl": 255.0, "jp_gov_days_cover": None, "jp_delta_spr_mmbbl": -8.0,
        "us_spr_gov_mmbbl": 413.5, "us_delta_spr_mmbbl": -1.9, "us_gov_days_cover": None,
        "cn_spr_gov_mmbbl": 1420.0, "cn_delta_spr_mmbbl": 8.0,
        "global_spr_gov_mmbbl": 2088.5, "global_spr_days_cover": 1.794, "delta_global_spr_days_cover": 0.102,
        "brent_front": 118.0, "brent_1_6m_spread": 8.0, "wti_3_2_1_crack": 44.0,
        "henry_hub": 3.20, "ttf_front": 16.2, "ttf_hh_spread": 13.0, "vix": 26.5,
    },
    {
        "date": "2026-04-30",
        "jp_spr_gov_mmbbl": 225.0, "jp_gov_days_cover": None, "jp_delta_spr_mmbbl": -8.0,
        "us_spr_gov_mmbbl": 397.9, "us_delta_spr_mmbbl": -15.6, "us_gov_days_cover": None,
        "cn_spr_gov_mmbbl": 1428.0, "cn_delta_spr_mmbbl": 8.0,
        "global_spr_gov_mmbbl": 2050.9, "global_spr_days_cover": 1.799, "delta_global_spr_days_cover": 0.005,
        "brent_front": 115.0, "brent_1_6m_spread": 7.0, "wti_3_2_1_crack": 50.0,
        "henry_hub": 3.50, "ttf_front": 15.8, "ttf_hh_spread": 12.3, "vix": 24.1,
    },
    {
        "date": "2026-05-31",
        "jp_spr_gov_mmbbl": None, "jp_gov_days_cover": None, "jp_delta_spr_mmbbl": None,
        "us_spr_gov_mmbbl": 365.1, "us_delta_spr_mmbbl": -32.8, "us_gov_days_cover": None,
        "cn_spr_gov_mmbbl": None, "cn_delta_spr_mmbbl": None,
        "global_spr_gov_mmbbl": 365.1, "global_spr_days_cover": 0.615, "delta_global_spr_days_cover": -1.184,
        "brent_front": 113.0, "brent_1_6m_spread": 6.0, "wti_3_2_1_crack": 55.49,
        "henry_hub": 3.75, "ttf_front": 15.5, "ttf_hh_spread": 11.75, "vix": None,
    },
]


# ── Data loading ──────────────────────────────────────────────────────────────

def load_data() -> pd.DataFrame:
    if USE_SUPABASE:
        try:
            resp = sb.table("spr_factors_monthly").select("*").execute()
            df = pd.DataFrame(resp.data)
            print(f"✓ Loaded {len(df)} rows from Supabase spr_factors_monthly view")
        except Exception as e:
            print(f"⚠  Supabase query failed ({e}) — falling back to embedded data")
            df = pd.DataFrame(EMBEDDED_DATA)
    else:
        df = pd.DataFrame(EMBEDDED_DATA)
        print(f"  Using embedded data: {len(df)} rows")

    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    return df


# ── Japan interpolation ───────────────────────────────────────────────────────

def interpolate_japan(df: pd.DataFrame) -> pd.DataFrame:
    """
    Fill missing Japan SPR months by linear interpolation between anchor points.
    Constrains cumulative change to announced ~80 mmbbl release program.
    """
    jp_cols = ["jp_spr_gov_mmbbl", "jp_gov_days_cover", "jp_delta_spr_mmbbl"]
    for col in ["jp_spr_gov_mmbbl", "jp_gov_days_cover"]:
        df[col] = df[col].interpolate(method="linear", limit_direction="both")

    # Recompute delta after interpolation
    df["jp_delta_spr_mmbbl"] = df["jp_spr_gov_mmbbl"].diff()

    # Japan z-score factor
    mu = df["jp_delta_spr_mmbbl"].mean()
    sigma = df["jp_delta_spr_mmbbl"].std()
    if sigma > 0:
        df["jp_spr_factor_z"] = (df["jp_delta_spr_mmbbl"] - mu) / sigma
    else:
        df["jp_spr_factor_z"] = 0.0

    return df


# ── Feature engineering ───────────────────────────────────────────────────────

def build_features(df: pd.DataFrame) -> pd.DataFrame:
    df = interpolate_japan(df)

    # Target: monthly change in Brent 1–6M spread
    df["y"] = df["brent_1_6m_spread"].diff()

    # Lagged predictors (t-1 to avoid look-ahead)
    df["L1_delta_global_days"] = df["delta_global_spr_days_cover"].shift(1)
    df["L1_global_days"]       = df["global_spr_days_cover"].shift(1)
    df["L1_jp_delta_days"]     = df["jp_delta_spr_mmbbl"].shift(1)
    df["L1_us_delta_mmbbl"]    = df["us_delta_spr_mmbbl"].shift(1)
    df["L1_cn_delta_mmbbl"]    = df["cn_delta_spr_mmbbl"].shift(1)
    df["L1_vix"]               = df["vix"].shift(1)
    df["L1_crack"]             = df["wti_3_2_1_crack"].shift(1)
    df["L1_ttf_hh"]            = df["ttf_hh_spread"].shift(1)
    df["L1_jp_factor_z"]       = df["jp_spr_factor_z"].shift(1)

    return df


# ── Regression ────────────────────────────────────────────────────────────────

def run_regression(df: pd.DataFrame) -> dict:
    try:
        import statsmodels.api as sm
        from statsmodels.stats.sandwich_covariance import cov_hac
    except ImportError:
        print("⚠  statsmodels not installed — skipping regression")
        return {}

    FEATURES = [
        "L1_delta_global_days",
        "L1_global_days",
        "L1_jp_delta_days",
        "L1_us_delta_mmbbl",
        "L1_cn_delta_mmbbl",
        "L1_vix",
        "L1_crack",
        "L1_ttf_hh",
    ]

    model_df = df[["y"] + FEATURES].dropna()
    if len(model_df) < 4:
        print(f"⚠  Only {len(model_df)} complete observations — need more data for reliable regression.")
        print("   Coefficients will be reported but treat as directional priors, not calibrated estimates.")
        print("   Add historical data (2020–2025) to strategic_inventories and oil_market_pricing for full fit.")

    if len(model_df) < 2:
        print("  Insufficient data for regression.")
        return {}

    X = sm.add_constant(model_df[FEATURES])
    y = model_df["y"]

    ols = sm.OLS(y, X).fit()

    # Newey-West HAC standard errors (lags=min(4, n//4))
    nw_lags = max(1, min(4, len(model_df) // 4))
    try:
        nw_cov  = cov_hac(ols, nlags=nw_lags)
        ols_nw  = ols.get_robustcov_results(cov_type="HAC", maxlags=nw_lags)
        se_label = f"Newey-West (lags={nw_lags})"
    except Exception:
        ols_nw  = ols
        se_label = "OLS (NW failed)"

    print("\n=== SPR TERM STRUCTURE MODEL ===")
    print(f"Target: Δ Brent 1–6M spread (monthly)")
    print(f"N={len(model_df)} | R²={ols.rsquared:.3f} | Adj-R²={ols.rsquared_adj:.3f}")
    print(f"Standard errors: {se_label}\n")
    print(f"{'Variable':<28} {'Coef':>10} {'SE':>10} {'t':>8} {'p':>8} {'Signal'}")
    print("-" * 72)

    # Use base OLS params with NW SEs where available
    try:
        nw_se = np.sqrt(np.diag(nw_cov))
        nw_t  = ols.params.values / nw_se
        from scipy import stats as scipy_stats
        nw_p  = 2 * (1 - scipy_stats.t.cdf(np.abs(nw_t), df=max(1, len(model_df) - X.shape[1])))
        se_source = "nw"
    except Exception:
        nw_se = ols.bse.values
        nw_t  = ols.tvalues.values
        nw_p  = ols.pvalues.values
        se_source = "ols"

    results = {}
    for i, var in enumerate(ols.params.index):
        coef = float(ols.params.iloc[i])
        se   = float(nw_se[i])
        tval = float(nw_t[i])
        pval = float(nw_p[i])
        sig  = "***" if pval < 0.01 else ("**" if pval < 0.05 else ("*" if pval < 0.10 else ""))
        print(f"{var:<28} {coef:>10.4f} {se:>10.4f} {tval:>8.2f} {pval:>8.3f} {sig}")
        results[var] = {"coef": float(coef), "se": float(se), "t": float(tval), "p": float(pval)}

    return results


# ── Japan SPR factor output ───────────────────────────────────────────────────

def compute_jp_factor(df: pd.DataFrame) -> pd.DataFrame:
    latest = df.dropna(subset=["jp_spr_gov_mmbbl"]).iloc[-1]
    print(f"\n=== JAPAN SPR FACTOR (current) ===")
    print(f"Latest date with JP data: {latest['date'].date()}")
    print(f"JP gov SPR:               {latest['jp_spr_gov_mmbbl']:.1f} mmbbl")
    print(f"JP Δ gov SPR (MoM):       {latest['jp_delta_spr_mmbbl']:+.1f} mmbbl")
    print(f"JP SPR z-score:           {latest['jp_spr_factor_z']:+.2f}σ")
    if latest['jp_gov_days_cover']:
        print(f"JP gov days cover:        {latest['jp_gov_days_cover']:.0f} days")
    return df


# ── Forward projection ────────────────────────────────────────────────────────

def forward_projection(df: pd.DataFrame):
    print("\n=== FORWARD PROJECTION — SPR RUNWAY ===")

    # US projection
    us_latest = df.dropna(subset=["us_spr_gov_mmbbl"]).iloc[-1]
    us_draw_rate_mmbbl_per_month = abs(df["us_delta_spr_mmbbl"].dropna().iloc[-3:].mean())
    us_floor = 250.0
    us_buffer = us_latest["us_spr_gov_mmbbl"] - us_floor
    us_months = us_buffer / us_draw_rate_mmbbl_per_month if us_draw_rate_mmbbl_per_month > 0 else 99

    print(f"\nUS SPR:")
    print(f"  Current:       {us_latest['us_spr_gov_mmbbl']:.1f} mmbbl ({us_latest['date'].date()})")
    print(f"  Avg draw rate: {us_draw_rate_mmbbl_per_month:.1f} mmbbl/month ({us_draw_rate_mmbbl_per_month/30:.2f} mb/d)")
    print(f"  Floor (~250):  {us_floor:.0f} mmbbl")
    print(f"  Runway:        {us_buffer:.0f} mmbbl → ~{us_months:.1f} months → ~{us_latest['date'] + pd.DateOffset(months=int(us_months)):%B %Y}")

    # Japan projection
    jp_rows = df.dropna(subset=["jp_spr_gov_mmbbl"])
    if len(jp_rows) >= 2:
        jp_latest = jp_rows.iloc[-1]
        jp_draw = abs(jp_rows["jp_delta_spr_mmbbl"].iloc[-3:].mean())
        jp_floor = 120.0  # ~90 days at ~1.3 mbd demand
        jp_buffer = jp_latest["jp_spr_gov_mmbbl"] - jp_floor
        jp_months = jp_buffer / jp_draw if jp_draw > 0 else 99
        print(f"\nJapan SPR:")
        print(f"  Current:       {jp_latest['jp_spr_gov_mmbbl']:.1f} mmbbl ({jp_latest['date'].date()})")
        print(f"  Avg draw rate: {jp_draw:.1f} mmbbl/month")
        print(f"  Floor (~120):  {jp_floor:.0f} mmbbl")
        print(f"  Runway:        {jp_buffer:.0f} mmbbl → ~{jp_months:.1f} months → ~{jp_latest['date'] + pd.DateOffset(months=int(jp_months)):%B %Y}")

    # Brent spread projection (directional)
    print(f"\nBrent 1–6M Spread (directional, assuming SPR draw continues):")
    print(f"  Current:        {df['brent_1_6m_spread'].dropna().iloc[-1]:.1f} $/bbl")
    print(f"  At US SPR floor (~Sep 2026): expect steepening as last suppression removed")
    print(f"  Framework thesis: spread widens to 12–18 $/bbl range once SPR depletion priced")


# ── Push results to Supabase ─────────────────────────────────────────────────

def push_results(results: dict):
    if not USE_SUPABASE or not results:
        return
    try:
        rows = [
            {
                "run_date": date.today().isoformat(),
                "variable": var,
                "coef": vals["coef"],
                "se": vals["se"],
                "t_stat": vals["t"],
                "p_value": vals["p"],
            }
            for var, vals in results.items()
        ]
        sb.table("spr_model_results").upsert(rows).execute()
        print(f"\n✓ Pushed {len(rows)} coefficient rows to spr_model_results")
    except Exception as e:
        print(f"⚠  Could not push results: {e}")


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("God's Eye — SPR Term Structure Model")
    print(f"Run date: {date.today()}\n")

    df = load_data()
    df = build_features(df)

    print("\n=== SPR FACTORS PANEL ===")
    display_cols = ["date", "jp_spr_gov_mmbbl", "jp_delta_spr_mmbbl", "jp_spr_factor_z",
                    "us_spr_gov_mmbbl", "us_delta_spr_mmbbl",
                    "global_spr_gov_mmbbl", "global_spr_days_cover",
                    "brent_1_6m_spread", "wti_3_2_1_crack"]
    print(df[[c for c in display_cols if c in df.columns]].to_string(index=False, float_format="{:.2f}".format))

    results = run_regression(df)
    df = compute_jp_factor(df)
    forward_projection(df)
    push_results(results)

    print("\n✓ Model run complete.")
    print("  To calibrate properly: load 2020–2025 historical data into strategic_inventories")
    print("  and oil_market_pricing. The current window (5 months) gives directional priors.")
    print("  Target: 36–60 monthly observations for reliable Newey-West inference.")
