"""
eia_spr_pull.py
Pull U.S. Strategic Petroleum Reserve weekly stock data from the EIA API v2.
Outputs: data/spr_weekly.csv
"""
import os
import requests
import pandas as pd
from datetime import datetime

EIA_API_KEY = os.environ.get("EIA_API_KEY", "")
SPR_SERIES   = "PET.WCSSTUS1.W"
OUTPUT_PATH  = os.path.join(os.path.dirname(__file__), "..", "data", "spr_weekly.csv")


def fetch_spr(start: str = "2020-01-01", end: str | None = None) -> pd.DataFrame:
    """Fetch SPR weekly stock data from EIA API v2."""
    if not EIA_API_KEY:
        raise EnvironmentError("EIA_API_KEY not set. Export it before running.")

    end = end or datetime.today().strftime("%Y-%m-%d")
    url = (
        "https://api.eia.gov/v2/seriesid/"
        f"{SPR_SERIES}"
        f"?api_key={EIA_API_KEY}"
        f"&start={start}&end={end}"
        "&sort[0][column]=period&sort[0][direction]=asc"
        "&length=5000"
    )
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    rows = data.get("response", {}).get("data", [])
    if not rows:
        raise ValueError(f"No data returned for series {SPR_SERIES}.")

    df = pd.DataFrame(rows)
    df = df.rename(columns={"period": "date", "value": "spr_kb"})
    df["date"] = pd.to_datetime(df["date"])
    df["spr_kb"] = pd.to_numeric(df["spr_kb"], errors="coerce")
    df = df[["date", "spr_kb"]].dropna().sort_values("date").reset_index(drop=True)
    return df


def wow_change(df: pd.DataFrame) -> pd.DataFrame:
    """Add week-over-week change columns."""
    df = df.copy()
    df["spr_wow_kb"]  = df["spr_kb"].diff()
    df["spr_wow_pct"] = df["spr_kb"].pct_change() * 100
    return df


def save(df: pd.DataFrame, path: str = OUTPUT_PATH) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    df.to_csv(path, index=False)
    print(f"[eia_spr_pull] Saved {len(df)} rows → {path}")


if __name__ == "__main__":
    df = fetch_spr(start="2015-01-01")
    df = wow_change(df)
    save(df)
    print(df.tail(8).to_string(index=False))
