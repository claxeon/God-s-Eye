-- God's Eye — Supabase Schema v3: Modules 1-9 Extension
-- Run against project: snykuqyceqpplnzmyksp
-- Purpose: storage for the new Treasury-plumbing / Japan-channels / dollar-
-- funding / refined-products / fertilizer-clock / country-vulnerability /
-- policy-response / claims-engine / dashboard-outputs modules added
-- 2026-08-18 at user request.
--
-- ADDITIVE ONLY. Does not alter, drop, or rename any table in
-- schema_v2_state_vector.sql (leg_components, calibration_params,
-- state_vector_history, calibration_episodes). Nothing here changes an
-- existing leg score, weight, or threshold — this is storage for the NEW
-- modules' own outputs, which a human decides whether/how to fold into the
-- existing L(t) pipeline (see MODULES_README.md "Relationship to the
-- existing framework").

-- ── 1. Module Results ────────────────────────────────────────────────────
-- One row per module per day. `payload` carries the full ModuleResult JSON
-- (metrics, observables, falsifiers, data_quality_notes, missing_data,
-- source_freshness) so nothing is lost to a narrower typed schema.
CREATE TABLE IF NOT EXISTS module_results (
    module_id       text NOT NULL,          -- e.g. 'M1_treasury_plumbing'
    obs_date        date NOT NULL,
    signal_state    text NOT NULL,          -- NORMAL / WATCH / ELEVATED / CRITICAL / DATA_QUALITY_FAILURE
    confidence      text NOT NULL,          -- CONFIRMED / INFERRED / SPECULATIVE
    payload         jsonb NOT NULL,
    created_at      timestamptz DEFAULT now(),
    PRIMARY KEY (module_id, obs_date)
);
CREATE INDEX IF NOT EXISTS idx_module_results_signal_state ON module_results (signal_state);
CREATE INDEX IF NOT EXISTS idx_module_results_obs_date ON module_results (obs_date);

-- ── 2. Claims (Module 8) ─────────────────────────────────────────────────
-- Mirrors module8_claims_engine.py's Claim dataclass / data/claims.json.
-- The Python module is the source of truth today (flat JSON file, same
-- pattern as module7's policy_events_log.json); this table exists so a
-- maintainer can migrate storage later without changing module8's public
-- interface (compute() / add_evidence()).
CREATE TABLE IF NOT EXISTS claims (
    claim_id                    text PRIMARY KEY,
    claim_text                  text NOT NULL,
    mechanism                   text NOT NULL,
    required_evidence           text NOT NULL,
    observable_indicators       jsonb NOT NULL DEFAULT '[]',
    source_hierarchy            jsonb NOT NULL DEFAULT '[]',
    threshold                   text,
    time_window                 text,
    expected_market_transmission     text,
    expected_physical_transmission   text,
    falsifier                   text NOT NULL,
    confidence_tier             text NOT NULL DEFAULT 'INFERRED',
    last_reviewed                timestamptz,
    evidence_log                 jsonb NOT NULL DEFAULT '[]',
    contradictory_evidence_log   jsonb NOT NULL DEFAULT '[]',
    score_history                jsonb NOT NULL DEFAULT '[]',
    status                       text NOT NULL DEFAULT 'ACTIVE',  -- ACTIVE/WEAKENING/FALSIFIED/RESOLVED/DATA_INSUFFICIENT
    created_at                   timestamptz DEFAULT now(),
    updated_at                   timestamptz DEFAULT now()
);

-- ── 3. Country Vulnerability (Module 6) ──────────────────────────────────
-- One row per country per observation date. All fields nullable — see
-- module6_country_vulnerability.py's explicit "unknown, not inferred" policy.
CREATE TABLE IF NOT EXISTS country_vulnerability (
    country                              text NOT NULL,
    obs_date                             date NOT NULL,
    nitrogen_import_dependence_pct       numeric,
    phosphate_import_dependence_pct      numeric,
    potash_import_dependence_pct         numeric,
    gulf_sourcing_share_pct              numeric,
    russia_china_belarus_sourcing_share_pct  numeric,
    fertilizer_inventory_months          numeric,
    fertilizer_subsidy_capacity_0to1     numeric,
    fx_reserve_adequacy_months_import_cover  numeric,
    fiscal_capacity_0to1                 numeric,
    diesel_import_dependence_pct         numeric,
    food_import_dependence_pct           numeric,
    ag_share_of_employment_pct           numeric,
    conflict_displacement_0to1           numeric,
    port_logistics_vulnerability_0to1    numeric,
    irrigation_dependency_pct            numeric,
    planting_window_proximity_days       integer,
    food_security_classification         text,
    humanitarian_access_0to1             numeric,
    overall_score                        numeric,
    overall_state                        text,
    field_coverage                       numeric,
    source                               text,             -- e.g. 'FAO', 'USDA FAS', 'World Bank WDI'
    confidence                           text DEFAULT 'speculative',
    created_at                           timestamptz DEFAULT now(),
    PRIMARY KEY (country, obs_date)
);

-- ── 4. Policy Events Log (Module 7) ──────────────────────────────────────
-- Mirrors data/policy_events_log.json. Announcement-driven events this
-- deployment has no live feed for (SPR releases beyond the EIA series,
-- export restrictions, subsidies, food-aid programs, etc.).
CREATE TABLE IF NOT EXISTS policy_events (
    id              bigserial PRIMARY KEY,
    event_date      date NOT NULL,
    category        text NOT NULL,     -- e.g. 'spr_release', 'export_restriction', 'subsidy'
    country_or_bloc text,
    description     text NOT NULL,
    source          text,
    logged_at       timestamptz DEFAULT now()
);

-- ── 5. Dashboard Feed Snapshot (Module 9) ────────────────────────────────
CREATE TABLE IF NOT EXISTS module_dashboard_feed (
    obs_date        date PRIMARY KEY,
    payload         jsonb NOT NULL,
    created_at      timestamptz DEFAULT now()
);
