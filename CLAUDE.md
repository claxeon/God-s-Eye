# God's Eye Scripts — Claude Code Context

## Directory Structure
```
Scripts/
├── spr_term_structure_model.py   # OLS regression, Newey-West HAC, SPR drawdown → Brent spread
├── historical_backfill.py        # L(t) state vector reconstruction for 6 historical episodes
├── eia_spr_pull.py               # EIA data pull (US SPR only — WCSSPUS1, WCSSTUS1, WCESTUS1)
└── youtube/                      # YouTube automation pipeline (free stack)
    ├── pipeline.py               # ENTRY POINT: python3 pipeline.py --topic "..."
    ├── config.py                 # All constants (voice, channel, paths)
    ├── tts.py                    # edge-tts (free Microsoft neural TTS, voice: Aria)
    ├── images.py                 # Pollinations.ai (free Flux images, needs browser UA)
    ├── assemble.py               # ffmpeg assembly (uses tmpdir to avoid apostrophe-in-path bug)
    ├── upload.py                 # YouTube Data API v3 upload
    ├── auth.py                   # OAuth flow (token.json already exists — do not re-run)
    ├── script_gen.py             # Claude Haiku script gen (needs ANTHROPIC_API_KEY)
    ├── token.json                # OAuth refresh token — DO NOT DELETE
    └── output/                   # One subdirectory per video slug
```

## YouTube Pipeline — How to Produce a Video

**Step 1** — Write `script.json` for the topic (do this in the Claude session, no API key needed):
```
output/<slug>/script.json
```
Required fields: `title`, `description`, `tags`, `thumbnail_prompt`, `fun_fact`, `scenes[]`
Each scene: `id`, `name`, `narration`, `visual_prompt`, `duration_seconds`

**Step 2** — Run the pipeline:
```bash
cd "/Users/leehutton/Downloads/God's Eye/Scripts/youtube"
python3 pipeline.py --topic "Why do volcanoes erupt?"
```
Pipeline auto-detects existing `script.json` and skips script generation step.

**Stack (all free, zero ongoing cost):**
- TTS: `edge-tts` — Microsoft neural voice `en-US-AriaNeural`
- Images: `Pollinations.ai` — Flux model, `image.pollinations.ai/prompt/...`, needs browser User-Agent
- Assembly: `ffmpeg` — slideshow + audio merge via tmpdir (avoids apostrophe-in-path bug)
- Upload: YouTube Data API v3 — `token.json` has refresh token, fully automated

**Channel:** "Why? Science for Kids" — MADE_FOR_KIDS=True, Category 27 (Education)

**Live videos:**
- https://youtu.be/Jl9evV2mTf8 — Why is the Sky Blue?
- https://youtu.be/Wk8a13gwb_I — Why Do We Have Seasons?
- https://youtu.be/ydOqAJ-GqfI — Why Do We Dream?
- https://youtu.be/Kr4CMzs2TVg — How Do Airplanes Fly?
- https://youtu.be/RGKyR1EMAjE — Why Is the Ocean Salty?

---

## SPR Term Structure Model

**Script:** `spr_term_structure_model.py`
**Run:** `python3 spr_term_structure_model.py`
**Data source:** Supabase view `spr_factors_monthly` (God's Eye project)
**EIA API key:** `6JlB2qAQoHxNGL6kEiiZ6fIRt8cU5FlqR8ReVWYE`

### Known Blockers (do not re-investigate these)

**B-001 — FIXED 2026-06-22:**
Root causes were: (1) `strategic_inventories` uses country='USA' for 66 historical rows but VIEW filtered on country='US'; (2) both 'USA' first-of-month and 'US'/'JP'/'CN' end-of-month dates interlaced in the output, so `brent_1_6m_spread.diff()` always hit a NULL row in between and returned NaN.
**Fix applied:** Rewrote VIEW with `si_dedup`, `mob_dedup`, `omp_dedup` CTEs normalizing all tables to one row per calendar month (date_trunc month key), coercing 'USA'→'US', joining omp on month preferring rows with non-null brent_6m. Migration: `fix_spr_factors_monthly_view_grain`. View now returns 66 rows (one per month, 2021-01 to 2026-06), 4 complete regression observations (Feb-May 2026).

**B-002 — brent_6m is NULL everywhere:**
No free public source exists for historical Brent 6m futures prices. EIA API v2 has no Brent futures (total=0). Yahoo Finance 404s on expired contracts. EIA LeafHandler has no Brent series.
**Current workaround:** Model falls back to EMBEDDED_DATA (Jan–May 2026, 5 rows).
**Paid options only:** ICE API, Bloomberg, Quandl/Nasdaq Data Link.

### Model Architecture
- Target: `Δ(brent_1_6m_spread)` — change in Brent 1m–6m spread ($/bbl)
- Predictors: 8 lagged features (L1_ prefix) — SPR levels, macro oil balance, Japan IEA inventories
- Method: OLS + Newey-West HAC standard errors
- Min observations: 36 for reliable inference, warns below 4, returns empty below 2
- Forward projection: US SPR runway = 365.1 mmbbl, draw 16.8 mmbbl/month → floor Nov 2026

---

## Key Don'ts
- Do NOT re-run `python3 auth.py` — `token.json` already exists with valid refresh token
- Do NOT search EIA for Brent 6m futures — confirmed dead end, no data exists there
- Do NOT use Higgsfield free tier for batch image generation — runs out immediately; use Pollinations.ai
- Do NOT run ffmpeg with paths containing apostrophes in concat files — use tmpdir workaround in `assemble.py`
