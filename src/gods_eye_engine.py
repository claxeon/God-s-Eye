"""
gods_eye_engine.py
God's Eye Monte Carlo engine.
Simulates joint paths for:
  - USD/JPY
  - Japan 10-year JGB yield
  - U.S. 10-year Treasury yield
  - BoJ policy rate
  - SPR depletion
under a parameterised Strait of Hormuz closure scenario.
Outputs: data/japan_hormuz_mc_{scenario}.csv
"""
import numpy as np
import pandas as pd
import os
from dataclasses import dataclass, field

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
N_SIM      = 10_000
N_MONTHS   = 18
SEED       = 42


@dataclass
class ScenarioParams:
    name:               str   = "capped1"
    # BoJ
    boj_start:          float = 0.50
    boj_max:            float = 1.00
    boj_hike_prob:      float = 0.65
    boj_trigger_usdjpy: float = 160.0
    boj_trigger_jgb:    float = 2.80
    # USD/JPY
    usdjpy_start:       float = 160.14
    usdjpy_drift:       float = -0.003
    usdjpy_vol:         float = 2.8
    fx_defense_floor:   float = 152.5
    fx_red_line:        float = 162.0
    # JGB
    jgb_start:          float = 2.69
    jgb_vol:            float = 0.06
    jgb_boj_beta:       float = 0.30
    # UST
    ust_start:          float = 4.54
    ust_drift:          float = 0.002
    ust_vol:            float = 0.08
    ust_jgb_corr:       float = 0.45
    # SPR
    spr_start_kb:       float = 351_300
    spr_draw_kbd:       float = 1_500
    closure_end_month:  int   = 6
    # Repatriation
    repatriation_mult:  float = 1.0


@dataclass
class ScenarioRefined(ScenarioParams):
    name:               str   = "refined"
    boj_max:            float = 1.25
    boj_hike_prob:      float = 0.75
    fx_defense_floor:   float = 155.0
    repatriation_mult:  float = 1.40


def run_montecarlo(params: ScenarioParams, n_sim: int = N_SIM, seed: int = SEED) -> pd.DataFrame:
    rng = np.random.default_rng(seed)

    cov = np.array([
        [1.0,              params.ust_jgb_corr],
        [params.ust_jgb_corr, 1.0],
    ])
    L = np.linalg.cholesky(cov)

    usdjpy = np.zeros((n_sim, N_MONTHS + 1))
    jgb    = np.zeros((n_sim, N_MONTHS + 1))
    ust    = np.zeros((n_sim, N_MONTHS + 1))
    boj    = np.zeros((n_sim, N_MONTHS + 1))
    spr    = np.zeros((n_sim, N_MONTHS + 1))

    usdjpy[:, 0] = params.usdjpy_start
    jgb[:,    0] = params.jgb_start
    ust[:,    0] = params.ust_start
    boj[:,    0] = params.boj_start
    spr[:,    0] = params.spr_start_kb

    draw_per_month_kb = params.spr_draw_kbd * 30

    for m in range(1, N_MONTHS + 1):
        z_raw  = rng.standard_normal((2, n_sim))
        z_corr = L @ z_raw
        z_jgb  = z_corr[0]
        z_ust  = z_corr[1]
        z_fx   = rng.standard_normal(n_sim)

        prev_boj    = boj[:, m - 1]
        prev_jgb    = jgb[:, m - 1]
        prev_usdjpy = usdjpy[:, m - 1]
        prev_ust    = ust[:, m - 1]
        prev_spr    = spr[:, m - 1]

        triggered = (prev_usdjpy >= params.boj_trigger_usdjpy) | (prev_jgb >= params.boj_trigger_jgb)
        hike_roll = rng.random(n_sim) < params.boj_hike_prob
        hike = triggered & hike_roll & (prev_boj < params.boj_max)
        new_boj = prev_boj + np.where(hike, 0.25, 0.0)
        new_boj = np.minimum(new_boj, params.boj_max)
        boj[:, m] = new_boj

        hike_delta = new_boj - prev_boj
        new_jgb = prev_jgb + hike_delta * params.jgb_boj_beta + params.jgb_vol * z_jgb
        new_jgb = np.maximum(new_jgb, 0.0)
        jgb[:, m] = new_jgb

        repatriate_pressure = params.repatriation_mult if m > 3 else 1.0
        new_ust = prev_ust + params.ust_drift * repatriate_pressure + params.ust_vol * z_ust
        new_ust = np.maximum(new_ust, 0.0)
        ust[:, m] = new_ust

        yen_strength_from_hike = -2.5 * hike_delta
        new_fx = (
            prev_usdjpy
            + params.usdjpy_drift * prev_usdjpy
            + yen_strength_from_hike
            + params.usdjpy_vol * z_fx
        )
        below_floor = new_fx < params.fx_defense_floor
        new_fx = np.where(below_floor, new_fx + 0.4 * (params.fx_defense_floor - new_fx), new_fx)
        above_red = new_fx > params.fx_red_line
        new_fx = np.where(above_red, new_fx - 0.6 * (new_fx - params.fx_red_line), new_fx)
        usdjpy[:, m] = np.maximum(new_fx, 100.0)

        closure_active = m <= params.closure_end_month
        draw = draw_per_month_kb if closure_active else 0.0
        spr[:, m] = np.maximum(prev_spr - draw, 0.0)

    months = np.arange(N_MONTHS + 1)
    records = []
    for m in months:
        records.append({
            "month":             m,
            "usdjpy_p10":        np.percentile(usdjpy[:, m], 10),
            "usdjpy_p50":        np.percentile(usdjpy[:, m], 50),
            "usdjpy_p90":        np.percentile(usdjpy[:, m], 90),
            "usdjpy_mean":       np.mean(usdjpy[:, m]),
            "jgb_p10":           np.percentile(jgb[:, m], 10),
            "jgb_p50":           np.percentile(jgb[:, m], 50),
            "jgb_p90":           np.percentile(jgb[:, m], 90),
            "ust_p10":           np.percentile(ust[:, m], 10),
            "ust_p50":           np.percentile(ust[:, m], 50),
            "ust_p90":           np.percentile(ust[:, m], 90),
            "boj_p50":           np.percentile(boj[:, m], 50),
            "boj_hike_prob":     np.mean(boj[:, m] > boj[:, max(m - 1, 0)]),
            "spr_p50_kb":        np.percentile(spr[:, m], 50),
            "spr_p10_kb":        np.percentile(spr[:, m], 10),
            "us_jp_spread_p50":  (np.percentile(ust[:, m], 50) - np.percentile(jgb[:, m], 50)) * 100,
        })
    return pd.DataFrame(records)


def save(df: pd.DataFrame, scenario_name: str, out_dir: str = OUTPUT_DIR) -> None:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"japan_hormuz_mc_{scenario_name}.csv")
    df.to_csv(path, index=False)
    print(f"[gods_eye_engine] Saved {len(df)} rows → {path}")


if __name__ == "__main__":
    for ScenClass in [ScenarioParams, ScenarioRefined]:
        p = ScenClass()
        print(f"\n=== Running scenario: {p.name} ===")
        df = run_montecarlo(p)
        save(df, p.name)
        print(df[["month","usdjpy_p50","jgb_p50","ust_p50","boj_p50","spr_p50_kb"]].to_string(index=False))
