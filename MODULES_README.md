# God's Eye — Modules 1-9 (added 2026-08-18)

Extension to the existing framework, built at user request, to improve
causal resolution: distinguish normal market repricing from impaired market
functioning, separate independent transmission mechanisms, expose
timing/procurement constraints, and make claims falsifiable.

## Relationship to the existing framework

**This extension does not modify `state_vector_compute.py`, `gods_eye_engine.py`,
any leg weight, or any threshold in the existing pipeline.** It is purely
additive: new tables (`schema_v3_extension_modules.sql`), new scripts
(`module1..9_*.py`), new config (`config/module*.json`). The existing
9-leg L(t) composite, `leg_components`, `state_vector_history`, and
`calibration_params` are untouched.

Where a new module's finding is relevant to an existing leg (e.g. Module 2's
Japan-channel decomposition and Leg 2, or Module 4's crack spread and Leg 1),
that connection is **documented, not automatic**. A human decides whether/how
to fold a new observable into an existing leg's weight — the same discipline
the vault's own Intelligence Briefs already use (see e.g.
`Intelligence Briefs/Daily Macro Risk Report - 2026-08-17.md` and
`Intelligence Briefs/Kinetic & Financial Update - 2026-08-18.md`, both of
which document new evidence and explicitly decline to auto-adjust a leg
score). This is a deliberate design choice, not an oversight: requirement #2
of the original spec ("do not modify existing weights/thresholds without an
explicit versioned override") and requirement #9 ("no individual signal [is]
confirmation of the entire macro thesis") both point the same direction.

## Shared infrastructure

`godseye_modules_common.py` holds everything module1-9 share:
- `SignalState` (NORMAL/WATCH/ELEVATED/CRITICAL/DATA_QUALITY_FAILURE) and
  `ConfidenceTier` (CONFIRMED/INFERRED/SPECULATIVE) enums.
- `Observable` / `ModuleResult` dataclasses — the common return shape every
  module produces, consumed generically by `module9_dashboard_outputs.py`.
- `ProviderInterface` (ABC) + `UnavailableProvider` — every data source is
  either a real fetcher or an explicit, documented stub that returns
  `value=None` with a `note` explaining what feed is missing and why. No
  module fabricates a number for a source it doesn't have.
- Calculation helpers, all unit-tested in `tests/test_godseye_modules.py`:
  `pct_to_bps` / `decimal_to_bps` / `bps_to_decimal`, `rolling_zscore`,
  `is_stale`, `matched_crack_spread` (the crack-spread data-quality gate),
  `threshold_signal_state`, `worse_state`.
- `load_config(module_name)` — reads `config/{module_name}.json`. Every
  module's thresholds live there, not in source, per the spec's requirement
  #3.
- `supabase_upsert` — best-effort write (try/except, never raises), same
  discipline as the `|| true` suffix already used on every step in
  `state_vector_daily.sh`.

## Module-by-module

### Module 1 — Treasury Market Plumbing (`module1_treasury_plumbing.py`)
**Purpose:** distinguish ordinary long-end repricing from impaired market
function. **Real data:** DGS2/5/10/20/30, SOFR, IORB, WTREGEN (TGA),
WRESBAL (reserves), RRPONTSYD (ON RRP) via FRED; bid-to-cover ratio for the
latest 30Y/10Y auction via Treasury's free `fiscaldata.treasury.gov` API.
**Documented gaps:** MOVE index, repo fails, 10Y/30Y swap spreads, auction
tail/stop-through (needs a when-issued yield source), indirect/direct/
primary-dealer allocation breakdown — all need a paid or more specialized
feed than this deployment has. **Structural honesty:** the spec's CRITICAL
condition requires auction weakness + repo stress + elevated fails + rising
long yields together; `repo_fails` is structurally unavailable here, so this
module can never reach CRITICAL on its own — that cap is reported, not
hidden. **Falsifier:** rising yields + strong auctions + normal dealer
absorption + orderly repo + no swap-spread stress = normal repricing.

### Module 2 — Japan Three-Channel Decomposition (`module2_japan_channels.py`)
**Purpose:** "Japan sells Treasuries" is not one mechanism — separate FX
intervention (A), institutional reallocation (B), leveraged carry unwind (C).
**Implementation choice:** wraps the framework's existing, validated
`yen_mechanics.py` (Channels A/C) and `carry_mechanics.py` (Channel B)
rather than recomputing their series — avoiding the two-source drift the
vault's own TIC notes had to reconcile by hand once already. **Gaps:** FIMA
repo usage and TIC holdings-by-maturity are not free-feed observables; the
"heavy FIMA use" falsifier can only be checked qualitatively via dated
Intelligence Briefs. Channel C is structurally capped at WATCH (no VIX/
credit-spread feed to confirm risk-asset stress, which the spec's own
falsifier requires before calling something a "leveraged carry unwind").
**Known pipeline cost:** see the comment in `state_vector_daily.sh` — this
module re-invokes `yen_mechanics.py`/`carry_mechanics.py`'s fetchers, which
already ran earlier in the daily script. Documented, not fixed, in this pass.

### Module 3 — Dollar Funding and Collateral Stress (`module3_dollar_funding.py`)
**Purpose:** is dollar liquidity tightening, and are official facilities
absorbing it? **Real data:** SOFR, IORB (reused from Module 1), DTWEXBGS
(broad dollar index). **Gaps:** cross-currency basis (JPY/EUR/KRW), FRA-OIS,
FIMA/swap-line usage, repo specialness/fails, FX implied vol/risk reversals —
all need a paid feed. **Structural honesty:** the module's core
classification ("stress absorbed by facilities" vs. "transmitted to asset
sales") is explicitly reported as `UNKNOWN` because the facility-usage data
needed to determine it isn't wired — this module reports the SOFR-IORB
spread only, and says so plainly rather than implying a fuller read.

### Module 4 — Refined Products and Physical Energy (`module4_refined_products.py`)
**Purpose:** separate crude shocks from refined-product/refinery/logistics
scarcity. **Real data, verified live 2026-08-18:** WTI spot (FRED
`DCOILWTICO`) and NY Harbor ULSD spot (EIA `PET.EER_EPD2DXL0_PF4_Y35NY_DPG.D`,
$/gal, converted ×42 to $/bbl, conversion logged). This is the module that
formalizes, as reusable code, the exact matched-unit/matched-date check
`Intelligence Briefs/Daily Macro Risk Report - 2026-08-17.md` had to do by
hand for the ~$102/bbl crack. **Data-quality gate:** `matched_crack_spread()`
in the common library refuses to return a number unless units match,
delivery months/assessment dates match, and quote types (futures vs.
physical) aren't mixed — returns `DATA_QUALITY_FAILURE` otherwise, by
construction, not by convention. **Gaps:** matched-month gasoil/Brent and
RBOB gasoline cracks (need a futures vendor), tanker rates, regional (East
Africa/South Asia) retail diesel prices, distillate inventory/refinery
utilization (deliberately deferred to `market_mechanics.py`'s existing,
already-validated EIA weekly puller rather than a second unverified copy).

### Module 5 — Fertilizer Procurement Clock (`module5_fertilizer_clock.py`)
**Purpose:** treat fertilizer risk as a seasonal procurement/application
problem, not a price problem. **Real data:** Henry Hub natural gas (EIA
`NG.RNGWHHD.D`) — the dominant ammonia/urea production cost driver.
**Reference (not live) data:** a static planting-window calendar (FAO crop
calendars) for six major exposed regions, used to compute a genuine
`planting_window_urgency` score. **Gaps:** urea/ammonia/DAP/MAP/potash/sulfur
prices, Gulf plant status, India/Brazil/Bangladesh/Pakistan tender data,
China/Russia export-restriction status, application-rate/acreage surveys —
all need a paid ag-data subscription or manual event logging; none are
fabricated. **Structural honesty:** this module is capped at WATCH — it
never reaches ELEVATED/CRITICAL from price/gas data alone, because the
spec's own falsifier design requires non-price evidence (tender failure,
plant outage, application drop) that isn't wired here.

### Module 6 — Country Vulnerability Matrix (`module6_country_vulnerability.py`)
**Purpose:** score which of 19 countries face first-order stress. **Data
policy (strict):** every field of every `CountryProfile` ships as `None`
until a maintainer loads sourced values — see "Module 6 data acquisition"
below. The scoring engine (`compute_scores()`) is fully built and tested,
but currently returns `DATA_QUALITY_FAILURE` for all 19 countries, honestly,
rather than hardcoding the vault's own prior qualitative risk list
(Bangladesh, Pakistan, Somalia, Sudan, Afghanistan, Yemen, etc. — see
`Intelligence Briefs/Daily Macro Risk Report - 2026-08-17.md`) as if it were
sourced numeric data. That distinction — a narrative claim vs. a sourced
number — is the entire point of this module; collapsing it would defeat the
purpose.

**Module 6 data acquisition (for a future maintainer):**
| Field group | Suggested source |
|---|---|
| Import dependence (N/P/K) | FAO FAOSTAT trade matrices, USDA FAS |
| Gulf/Russia/China sourcing share | UN Comtrade, national customs data |
| FX reserves, fiscal capacity | IMF Article IV consultations, World Bank WDI |
| Conflict/displacement | UNHCR, ACLED |
| Food security classification | IPC/Cadre Harmonisé (label the field as-is, don't convert to a score yourself) |
| Application/acreage surveys | FAO/USDA, national ag ministries |

### Module 7 — Policy Response Function (`module7_policy_response.py`)
**Purpose:** are governments/central banks absorbing stress before it
transmits further? **Note the inverted framing:** a HIGH score here is
reassuring; CRITICAL means policy response is failing to keep pace, not that
policy itself is extreme — see the `note` field in
`config/module7_policy_response.json`. **Real data:** Fed reserve balances,
ON RRP, TGA (reused from Module 1's FRED client). **Manual event log:**
`log_policy_event()` / `data/policy_events_log.json` — the only practical way
to track SPR releases beyond the EIA series, export restrictions, subsidies,
food-aid programs, etc. without a paid news-event feed. **Structural
honesty:** `policy_buffer_score_full` is always `None` — the module can only
measure the liquidity half of "policy response," not the physical-flow half,
and says so rather than blending a partial read into a confident-looking
single number.

### Module 8 — Claims, Epistemic, and Falsification Engine (`module8_claims_engine.py`)
**Purpose:** every framework thesis claim gets a structured record —
mechanism, required evidence, falsifier, confidence tier, evidence log,
contradictory-evidence log, status. **Storage:** `data/claims.json`
(flat file, same pattern as Module 7's event log; `schema_v3_extension_
modules.sql` has a `claims` table ready for a future migration). **Seeded**
with five real, currently-live claims (not synthetic examples) — see the
module docstring: C1 fertilizer/2027 crop-input risk (the spec's own worked
example, now instantiated with real evidence), C2 Japan UST reallocation,
C3 refined-product scarcity, C4 IRGC offensive-posture shift, C5 Pickaxe
Mountain strike scenario (backfilled 2026-08-18 from
`Scenarios/Scenarios - Five Primary Branch Tree.md` Scenario F — this one
did not exist as a formal claim until the backfill pass). Each carries real
evidence entries dated and sourced to the vault's own Aug 17-18 material; as
of the 2026-08-18 backfill, C2 alone carries 6 supporting + 1 contradictory
entry (the contradictory one — Feb/May officials being net BUYERS of
Treasury bonds specifically — is kept deliberately, not pruned, since a
claim that only accumulates supporting evidence isn't being tested).
**CLI:** `--list`, `--evidence <claim_id> --text "..." --source "..."
[--contradicts]` to append evidence without hand-editing JSON.
**Status changes are never automatic** — `weakening_candidates` is a
mechanical suggestion (contradictory evidence outnumbers supporting
evidence) surfaced in `data_quality_notes`, requiring a human to actually
flip a claim's `status`, mirroring how the vault's TIC Analysis note only
retracted a claim after direct verification, not on suspicion.

### Module 9 — Dashboard Outputs (`module9_dashboard_outputs.py`)
**Purpose:** aggregate modules 1-8 into the 10 spec'd dashboard panels as one
JSON document (`data/dashboard_modules_feed.json`). **Deliberately does not
edit `gods_eye_dashboard.html`** (134KB of working, hand-built Chart.js
wiring against the existing 9-leg state vector) — a blind structural edit
there risks breaking a working dashboard for a change that's additive by
nature. Each panel reports current state, trend (currently "insufficient
history" until `module_results` accumulates dated rows — the table and the
write path both exist, just not enough history yet), data freshness,
confidence, top drivers, countervailing evidence, falsification conditions,
and linked source records. One module failing (exception in `compute()`)
does not take down the other panels — caught and reported as
`DATA_QUALITY_FAILURE` with the error message, not a crash.

**Dashboard integration (when a maintainer is ready):** add to
`gods_eye_dashboard.html`:
```js
fetch('data/dashboard_modules_feed.json').then(r => r.json()).then(feed => {
  // feed.panels is the array described above; render alongside the
  // existing 9-leg panels however the existing Chart.js layout prefers.
});
```

## Wiring into `state_vector_daily.sh`

One new line, added after the existing Kalman step and before the final
`exec python3 state_vector_compute.py ...` (which must stay last — `exec`
replaces the shell process, so nothing after it would ever run):
```bash
python3 module9_dashboard_outputs.py --write-file --write-supabase 1>&2 || true
```
Same `|| true` discipline as every other step — a module failure never
blocks the existing state-vector computation. See the comment in the script
for the known Module 2 double-fetch cost (documented, not yet optimized).

## Tests

`tests/test_godseye_modules.py` — 27 tests, network-free, covering bps
conversion, the crack-spread data-quality gate (clean match, mismatched
units, mismatched delivery months, futures/physical mixing, missing price,
and a regression test pinned to the real Aug 17 diesel-crack numbers),
rolling z-scores (including the zero-variance/insufficient-history edge
cases), stale-data detection, and signal-state transitions.
```bash
cd Scripts && python3 -m unittest discover -s tests -v
```

## Known data gaps (honest summary)

Paid/authenticated feeds this deployment does not have, appearing as
`UnavailableProvider` stubs across modules 1, 3, 4, 5, 6, 7: MOVE index,
Treasury repo fails, 10Y/30Y swap spreads, auction tail/dealer-allocation
detail, cross-currency basis swaps, FRA-OIS, FIMA/swap-line usage
by-counterparty, FX risk reversals, matched-month gasoil/RBOB futures,
tanker rates/war-risk insurance, regional retail diesel prices,
fertilizer commodity prices (urea/ammonia/DAP/MAP/potash/sulfur), Gulf plant
operating status as structured data, national fertilizer-tender data,
application-rate/acreage surveys, and essentially all of Module 6's country
fields. This is the honest state of what free/already-available data can
support — treat every module's `missing_data` and `data_quality_notes`
fields as load-bearing, not boilerplate.

## Assumptions worth revisiting (per the original spec's requirement)

- Module 2's re-invocation of `yen_mechanics.py`/`carry_mechanics.py` inside
  the daily pipeline is wasteful (double fetch) — worth refactoring those
  two scripts to expose a "compute from already-fetched inputs" path.
- Module 6 ships with zero populated countries. Its value is entirely
  contingent on a maintainer investing in real data acquisition — until
  then it is a tested, correct, but empty scoring engine.
- Several modules (1, 3, 4) are structurally capped below CRITICAL because
  a required input (repo fails, swap spreads, inventory/utilization
  confirmation) isn't wired. That is intentional honesty, not a bug — but it
  does mean "no module has reached CRITICAL" should not be read as "nothing
  is critical," only as "this deployment can't currently confirm CRITICAL
  through this module's specific evidentiary bar."
- Consider whether Module 8's four seeded claims should be the START of a
  process where every future Intelligence Brief mints a claim here instead
  of only living as prose — that would be the highest-leverage single change
  for making the framework's falsifiability real over time rather than
  demonstrated once.
