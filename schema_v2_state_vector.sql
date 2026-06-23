-- God's Eye — Supabase Schema v2: State Vector Extension
-- Run against project: snykuqyceqpplnzmyksp
-- Purpose: Store L(t) observable components, computed leg scores, and calibration params
-- Extends v1 schema (strategic_inventories, macro_oil_balance, oil_market_pricing, spr_policy_events)

-- ── 1. Leg Component Series ──────────────────────────────────────────────────
-- One row per observable data point per series
CREATE TABLE IF NOT EXISTS leg_components (
    id              bigserial PRIMARY KEY,
    series_id       text NOT NULL,          -- e.g. 'L1_brent_backwardation', 'L2_tic_official_flow'
    leg             smallint NOT NULL,      -- 1-9, 0=cross-cutting
    component_idx   smallint NOT NULL,      -- x_{i,j} index within leg
    obs_date        date NOT NULL,
    value           numeric NOT NULL,
    unit            text,                   -- $/bbl, $B, %, kb/d, etc.
    source          text,                   -- EIA, Treasury TIC, USDA, manual, etc.
    confidence      text DEFAULT 'confirmed', -- confirmed / inferred / speculative
    created_at      timestamptz DEFAULT now(),
    UNIQUE (series_id, obs_date)
);

-- ── 2. Calibration Parameters ────────────────────────────────────────────────
-- Rolling mean and std for z-score normalization per component
CREATE TABLE IF NOT EXISTS calibration_params (
    series_id       text NOT NULL,
    estimation_window_start  date NOT NULL,
    estimation_window_end    date NOT NULL,
    mu              numeric NOT NULL,       -- rolling mean
    sigma           numeric NOT NULL,       -- rolling std
    n_obs           integer NOT NULL,       -- number of observations used
    weight          numeric DEFAULT 1.0,    -- component weight w_{i,j}
    updated_at      timestamptz DEFAULT now(),
    PRIMARY KEY (series_id, estimation_window_end)
);

-- ── 3. State Vector History ───────────────────────────────────────────────────
-- Computed L_i(t) values after z-score composite and logistic transform
CREATE TABLE IF NOT EXISTS state_vector_history (
    obs_date        date NOT NULL,
    l1              numeric NOT NULL,   -- War / Energy Chokepoints
    l2              numeric NOT NULL,   -- GCC / Petrodollar Strain
    l3              numeric NOT NULL,   -- Private Credit / NBFI
    l4              numeric NOT NULL,   -- Rails / Settlement / Stablecoin
    l5              numeric NOT NULL,   -- Food / Fertilizer
    l6              numeric NOT NULL,   -- Munitions / MIC
    l7              numeric NOT NULL,   -- Semiconductor / Taiwan
    l8              numeric NOT NULL,   -- Maritime / Insurance
    l9              numeric NOT NULL,   -- AI / Labor
    l_cross         numeric NOT NULL,   -- Cross-cutting JPY Carry
    composite       numeric GENERATED ALWAYS AS (
        l1*0.20 + l2*0.15 + l3*0.12 + l4*0.08 +
        l5*0.12 + l6*0.08 + l7*0.08 + l8*0.10 + l9*0.07
    ) STORED,
    constraint_band text,               -- CB-C / CB-D / CB-E
    notes           text,
    created_at      timestamptz DEFAULT now(),
    PRIMARY KEY (obs_date)
);

-- ── 4. Calibration Episodes ───────────────────────────────────────────────────
-- Historical L(t) snapshots at key analogue episodes for prior calibration
CREATE TABLE IF NOT EXISTS calibration_episodes (
    episode_id      text NOT NULL,          -- e.g. '2024_boj_flash_crash'
    obs_date        date NOT NULL,
    episode_label   text NOT NULL,
    l1              numeric,
    l2              numeric,
    l3              numeric,
    l4              numeric,
    l5              numeric,
    l6              numeric,
    l7              numeric,
    l8              numeric,
    l9              numeric,
    l_cross         numeric,
    brent           numeric,
    vix             numeric,
    usd_jpy         numeric,
    us_10y_yield    numeric,
    -- Realized outcomes
    scenario_realized       text,       -- A/B/C/D/E or null
    flash_crash_occurred    boolean DEFAULT false,
    chokepoint_closure      boolean DEFAULT false,
    days_to_resolution      integer,
    notes           text,
    PRIMARY KEY (episode_id, obs_date)
);

-- ── 5. Simulation Run Registry ────────────────────────────────────────────────
-- Track simulation runs for comparison across parameter sets
CREATE TABLE IF NOT EXISTS simulation_runs (
    run_id          text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    run_date        date NOT NULL DEFAULT current_date,
    engine_version  text NOT NULL DEFAULT '1.0',
    n_simulations   integer NOT NULL,
    seed            integer,
    -- Parameters
    boj_hike_prob   numeric,
    houthi_escalation numeric,
    ceasefire_suppression numeric,
    -- Key outputs
    scenario_A_pct  numeric,
    scenario_B_pct  numeric,
    scenario_C_pct  numeric,
    scenario_D_pct  numeric,
    scenario_E_pct  numeric,
    flash_crash_prob numeric,
    flash_crash_mean_week numeric,
    n_regimes       integer,
    silhouette_score numeric,
    notes           text,
    created_at      timestamptz DEFAULT now()
);

-- ── 6. Event Correlation Matrix ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS event_correlations (
    run_id          text REFERENCES simulation_runs(run_id),
    event_a         text NOT NULL,
    event_b         text NOT NULL,
    correlation     numeric NOT NULL,   -- co-occurrence correlation across runs
    co_occurrence_rate numeric,         -- % of runs where both events fired
    created_at      timestamptz DEFAULT now(),
    PRIMARY KEY (run_id, event_a, event_b)
);

-- ── Seed current L(t) state vector (Jun 8, 2026) ─────────────────────────────
INSERT INTO state_vector_history
    (obs_date, l1, l2, l3, l4, l5, l6, l7, l8, l9, l_cross, constraint_band, notes)
VALUES
    ('2026-06-08', 0.99, 0.89, 0.67, 0.32, 0.83, 0.45, 0.42, 0.89, 0.38, 1.00,
     'CB-D/CB-E-threshold',
     'v4.2: Houthi declared blockade confirmed; Leg 8 upgraded 85→89%. SPR floor ~Jul 1.')
ON CONFLICT (obs_date) DO UPDATE SET
    l8 = 0.89,
    constraint_band = 'CB-D/CB-E-threshold',
    notes = 'v4.2: Houthi declared blockade confirmed; Leg 8 upgraded 85→89%.',
    created_at = now();

-- ── Seed calibration episodes ─────────────────────────────────────────────────
INSERT INTO calibration_episodes
    (episode_id, obs_date, episode_label, l_cross, l1, l8, vix, usd_jpy, flash_crash_occurred, notes)
VALUES
    ('2024_boj_flash_crash', '2024-08-05',
     'Aug 2024 BOJ Flash Crash — Dress Rehearsal',
     0.72, 0.35, 0.45, 65.0, 150.0, true,
     '15bp BOJ hike; VIX 65; Nikkei -12%; Bitcoin -20%. Contained by BOJ reassurance within 5 sessions.
      Closest historical analogue to Jun 2026 trigger. Key diff: 25bp at JPY 159, no reassurance option.'),
    ('2019_hormuz_tanker', '2019-07-19',
     '2019 Hormuz Tanker Seizures',
     0.25, 0.55, 0.60, 22.0, 108.0, false,
     'IRGC seized UK tanker Stena Impero. US deployed carrier. No physical closure. Brent +$3-5.
      Scenario D precursor. Resolved diplomatically. Base rate reference for mine deployment.'),
    ('2022_ukraine_lng', '2022-03-07',
     '2022 Ukraine War / European LNG Shock',
     0.30, 0.70, 0.40, 38.0, 115.0, false,
     'Brent hit $130. TTF spiked 10x. Fertilizer crisis onset (Russian/Belarusian potash).
      No Hormuz component. Leg 5 analogue. No flash crash; Fed began hiking. Prolonged duration.'),
    ('2023_red_sea_onset', '2023-11-19',
     '2023-24 Red Sea Houthi Campaign Onset',
     0.20, 0.60, 0.70, 16.0, 150.0, false,
     'Houthis began attacking shipping Nov 2023. Cape rerouting confirmed by Jan 2024.
      Lloyd''s war risk surcharge activated. No physical closure. Leg 8 analogue for current state.')
ON CONFLICT (episode_id, obs_date) DO NOTHING;

-- ── Indexes ───────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_leg_components_series ON leg_components(series_id, obs_date DESC);
CREATE INDEX IF NOT EXISTS idx_leg_components_leg ON leg_components(leg, obs_date DESC);
CREATE INDEX IF NOT EXISTS idx_state_vector_date ON state_vector_history(obs_date DESC);
CREATE INDEX IF NOT EXISTS idx_sim_runs_date ON simulation_runs(run_date DESC);

-- ── View: Latest state vector with formatted output ───────────────────────────
CREATE OR REPLACE VIEW state_vector_latest AS
SELECT
    obs_date,
    round(l1*100)::text || '%'  AS leg_1_war_energy,
    round(l2*100)::text || '%'  AS leg_2_petrodollar,
    round(l3*100)::text || '%'  AS leg_3_private_credit,
    round(l4*100)::text || '%'  AS leg_4_rails,
    round(l5*100)::text || '%'  AS leg_5_food_fert,
    round(l6*100)::text || '%'  AS leg_6_munitions,
    round(l7*100)::text || '%'  AS leg_7_semicon,
    round(l8*100)::text || '%'  AS leg_8_maritime,
    round(l9*100)::text || '%'  AS leg_9_ai_labor,
    round(l_cross*100)::text || '%' AS cross_jpy_carry,
    round(composite*100,1)::text || '%' AS composite_score,
    constraint_band,
    notes
FROM state_vector_history
ORDER BY obs_date DESC
LIMIT 1;
