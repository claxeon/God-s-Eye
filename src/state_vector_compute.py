"""
state_vector_compute.py
Assemble the God's Eye state vector from component data files.
State vector fields:
  date, spr_kb, spr_wow_kb, usdjpy, jgb_10y, us_10y, us_jp_spread_bp,
  jgb_wow_bp, usdjpy_wow, boj_rate, closure_week, closure_active
Outputs: data/state_vector.csv
"""
import pandas as pd
import numpy as np
import os
from datetime import datetime

DATA_DIR    = os.path.join(os.path.dirname(__file__), "..", "data")
OUTPUT_PATH = os.path.join(DATA_DIR, "state_vector.csv")


def load_spr() -> pd.DataFrame:
    path = os.path.join(DATA_DIR, "spr_weekly.csv")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Missing {path} — run eia_spr_pull.py first.")
    df = pd.read_csv(path, parse_dates=["date"])
    return df[["date", "spr_kb", "spr_wow_kb"]].copy()


def load_fx_yields() -> pd.DataFrame:
    """
    Load USD/JPY and JGB/UST yield data.
    Expects data/fx_yields.csv with columns:
      date, usdjpy, jgb_10y, us_10y, boj_rate
    Returns a stub with last-known values if file is absent.
    """
    path = os.path.join(DATA_DIR, "fx_yields.csv")
    if os.path.exists(path):
        df = pd.read_csv(path, parse_dates=["date"])
        return df
    print("[state_vector] WARNING: fx_yields.csv not found — using stub anchors.")
    today = pd.Timestamp.today().normalize()
    df = pd.DataFrame({
        "date":     [today],
        "usdjpy":   [160.14],
        "jgb_10y":  [2.69],
        "us_10y":   [4.54],
        "boj_rate": [0.50],
    })
    return df


def compute_state_vector(
    spr: pd.DataFrame,
    fx: pd.DataFrame,
    closure_start: str | None = None,
) -> pd.DataFrame:
    fx = fx.copy()
    fx["date"] = pd.to_datetime(fx["date"])
    fx = fx.set_index("date").resample("W-FRI").last().reset_index()

    spr = spr.copy()
    spr["date"] = pd.to_datetime(spr["date"])

    sv = pd.merge_asof(
        spr.sort_values("date"),
        fx.sort_values("date"),
        on="date",
        direction="backward",
    )

    sv["us_jp_spread_bp"] = (sv["us_10y"] - sv["jgb_10y"]) * 100
    sv["jgb_wow_bp"]      = sv["jgb_10y"].diff() * 100
    sv["usdjpy_wow"]      = sv["usdjpy"].diff()

    if closure_start:
        cs = pd.Timestamp(closure_start)
        sv["closure_active"] = (sv["date"] >= cs).astype(int)
        sv["closure_week"]   = sv["closure_active"] * (
            ((sv["date"] - cs).dt.days // 7 + 1).clip(lower=0)
        )
    else:
        sv["closure_active"] = 0
        sv["closure_week"]   = 0

    cols = [
        "date", "spr_kb", "spr_wow_kb",
        "usdjpy", "usdjpy_wow",
        "jgb_10y", "jgb_wow_bp",
        "us_10y", "us_jp_spread_bp",
        "boj_rate",
        "closure_week", "closure_active",
    ]
    sv = sv[[c for c in cols if c in sv.columns]]
    return sv.sort_values("date").reset_index(drop=True)


def save(df: pd.DataFrame, path: str = OUTPUT_PATH) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    df.to_csv(path, index=False)
    print(f"[state_vector] Saved {len(df)} rows → {path}")


if __name__ == "__main__":
    spr = load_spr()
    fx  = load_fx_yields()
    sv  = compute_state_vector(spr, fx)
    save(sv)
    print(sv.tail(8).to_string(index=False))
