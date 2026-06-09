"""
spr_term_structure_model.py
Fit a term-structure model to SPR depletion under a Strait of Hormuz closure scenario.
Produces forward depletion curves at P10 / P50 / P90.
Outputs: data/spr_term_structure.csv
"""
import numpy as np
import pandas as pd
import os

OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "spr_term_structure.csv")

DEFAULT_SPR_KB        = 351_300
DEFAULT_DRAW_RATE_KBD = 1_500
DEFAULT_CLOSURE_WEEKS = (4, 12)
N_SIM                 = 50_000
SEED                  = 42


def simulate_depletion(
    spr_kb: float = DEFAULT_SPR_KB,
    draw_rate_kbd: float = DEFAULT_DRAW_RATE_KBD,
    closure_weeks: tuple = DEFAULT_CLOSURE_WEEKS,
    n_sim: int = N_SIM,
    seed: int = SEED,
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    draw_per_week_kb = draw_rate_kbd * 7

    durations = rng.integers(closure_weeks[0], closure_weeks[1] + 1, size=n_sim)
    max_weeks = int(durations.max())

    remaining = np.full((n_sim, max_weeks + 1), np.nan)
    remaining[:, 0] = spr_kb

    for w in range(1, max_weeks + 1):
        active = durations >= w
        remaining[active, w]  = np.maximum(remaining[active, w - 1] - draw_per_week_kb, 0)
        remaining[~active, w] = remaining[~active, w - 1]

    weeks = np.arange(max_weeks + 1)
    p10  = np.nanpercentile(remaining, 10, axis=0)
    p50  = np.nanpercentile(remaining, 50, axis=0)
    p90  = np.nanpercentile(remaining, 90, axis=0)
    mean = np.nanmean(remaining, axis=0)

    df = pd.DataFrame({"week": weeks, "p10_kb": p10, "p50_kb": p50, "p90_kb": p90, "mean_kb": mean})
    df["p10_pct_drawn"] = (1 - df["p10_kb"] / spr_kb) * 100
    df["p50_pct_drawn"] = (1 - df["p50_kb"] / spr_kb) * 100
    df["p90_pct_drawn"] = (1 - df["p90_kb"] / spr_kb) * 100
    return df


def weeks_to_depletion(df: pd.DataFrame, threshold_pct: float = 50.0) -> dict:
    col = df[df["p50_pct_drawn"] >= threshold_pct]
    week = int(col["week"].iloc[0]) if not col.empty else None
    return {"threshold_pct": threshold_pct, "p50_week": week}


def save(df: pd.DataFrame, path: str = OUTPUT_PATH) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    df.to_csv(path, index=False)
    print(f"[spr_term_structure] Saved {len(df)} rows → {path}")


if __name__ == "__main__":
    df = simulate_depletion()
    save(df)
    print(df.head(16).to_string(index=False))
    print("\n50% depletion at P50:", weeks_to_depletion(df, 50))
