#!/usr/bin/env python3
"""
God's Eye — Escalation Price Simulation
==========================================
Monte Carlo scenario simulation for Brent, WTI, diesel crack, and gasoline
crack under explicit escalation/de-escalation assumptions, built 2026-07-22
off the Crack Spread Analysis and Escalation Verification briefs.

Methodology (stated plainly, not hidden in the numbers):
  1. Anchor: today's actual state (real-time Brent, current crack levels,
     current physical_tightness reading from market_mechanics_daily).
  2. "Fair value today" check: what Brent would be if physical_tightness were
     fully priced in right now (divergence_score = 0), using the same
     BRENT_MU/SIGMA calibration state_vector_compute.py and market_mechanics.py
     already use (FRED DCOILBRENTEU 2015-2025 distribution).
  3. Each scenario perturbs physical_tightness and a crack-severity multiplier
     by an explicit, documented assumption (not a black box), then samples
     N trials with normally-distributed noise around that assumption to
     produce a distribution, not a single point forecast.
  4. Cross-checked against the vault's own pre-existing Scenario A-E price
     bands (Scenarios - Five Primary Branch Tree.md) as an independent sanity
     check, not as the source of the numbers.

This is a SCENARIO EXERCISE, not a forecast. Ranges are wide on purpose.
The framework's own track record (P16: Brent<$85 miss, assigned 4%, happened)
is the reason for the wide bands and explicit refusal to give single-point
answers.

Run: python3 escalation_price_simulation.py
"""

import random
import math

# ── Calibration constants (shared with market_mechanics.py / state_vector_compute.py) ──
BRENT_MU = 66.43
BRENT_SIGMA = 18.71

# ── Today's anchor state (2026-07-22) ────────────────────────────────────────
# Brent: real-time reporting ($94.13-95.47 across sources), not FRED's lagged
# $86.99 (DCOILBRENTEU prints 1-2 days behind during fast-moving weeks).
BRENT_NOW = 94.5
BRENT_WTI_SPREAD_NOW = 5.0    # recent observed range ~2-9; using a mid estimate
PHYSICAL_TIGHTNESS_NOW = 1.806   # market_mechanics_daily, as_of 2026-07-20 (latest available)
DIESEL_CRACK_NOW = 85.57         # Brent-basis, NY Harbor ULSD, 2026-07-17
GASOLINE_CRACK_NOW = 56.97       # Brent-basis, NY Harbor conventional gasoline, 2026-07-17

N_TRIALS = 20000


def brent_from_tightness(physical_tightness: float) -> float:
    """Brent implied if price fully reflects a given physical_tightness z-score."""
    return BRENT_MU + physical_tightness * BRENT_SIGMA


def run_scenario(name: str, description: str,
                  tightness_shift_mean: float, tightness_shift_sd: float,
                  suppression_factor_mean: float, suppression_factor_sd: float,
                  diesel_crack_mult_mean: float, diesel_crack_mult_sd: float,
                  gasoline_crack_mult_mean: float, gasoline_crack_mult_sd: float,
                  horizon_label: str) -> dict:
    """
    tightness_shift: added to today's physical_tightness to project forward.
    suppression_factor: 0 = price fully reflects tightness (divergence closes to 0);
                         1 = price stays anchored at today's level regardless of tightness
                         (S(t) suppression fully holds). Sampled per trial to reflect
                         genuine uncertainty about whether paper-market suppression
                         (the persistent swap-dealer short documented all session) holds,
                         partially breaks, or fully breaks.
    crack multipliers: applied to today's crack level, representing refinery-specific
                        stress compounding independent of the crude price move.
    """
    brent_samples = []
    wti_samples = []
    diesel_samples = []
    gasoline_samples = []

    for _ in range(N_TRIALS):
        tightness = PHYSICAL_TIGHTNESS_NOW + random.gauss(tightness_shift_mean, tightness_shift_sd)
        suppression = min(1.0, max(0.0, random.gauss(suppression_factor_mean, suppression_factor_sd)))
        fair_value = brent_from_tightness(tightness)
        # Blend: fully-suppressed price stays near BRENT_NOW scaled by the same
        # tightness delta at a damped rate; fully-unsuppressed price = fair_value.
        suppressed_anchor = BRENT_NOW + (tightness - PHYSICAL_TIGHTNESS_NOW) * (BRENT_SIGMA * 0.35)
        brent = suppression * suppressed_anchor + (1 - suppression) * fair_value
        brent = max(40.0, brent)  # floor — deep demand destruction / OPEC+ response bound

        wti_spread = max(1.0, random.gauss(BRENT_WTI_SPREAD_NOW, 2.0))
        wti = brent - wti_spread

        dcm = max(0.3, random.gauss(diesel_crack_mult_mean, diesel_crack_mult_sd))
        gcm = max(0.3, random.gauss(gasoline_crack_mult_mean, gasoline_crack_mult_sd))
        diesel_crack = DIESEL_CRACK_NOW * dcm
        gasoline_crack = GASOLINE_CRACK_NOW * gcm

        brent_samples.append(brent)
        wti_samples.append(wti)
        diesel_samples.append(diesel_crack)
        gasoline_samples.append(gasoline_crack)

    def pct(samples, p):
        s = sorted(samples)
        idx = int(len(s) * p)
        return s[min(idx, len(s) - 1)]

    return {
        "name": name,
        "description": description,
        "horizon": horizon_label,
        "brent_p10": pct(brent_samples, 0.10),
        "brent_p50": pct(brent_samples, 0.50),
        "brent_p90": pct(brent_samples, 0.90),
        "wti_p10": pct(wti_samples, 0.10),
        "wti_p50": pct(wti_samples, 0.50),
        "wti_p90": pct(wti_samples, 0.90),
        "diesel_p10": pct(diesel_samples, 0.10),
        "diesel_p50": pct(diesel_samples, 0.50),
        "diesel_p90": pct(diesel_samples, 0.90),
        "gasoline_p10": pct(gasoline_samples, 0.10),
        "gasoline_p50": pct(gasoline_samples, 0.50),
        "gasoline_p90": pct(gasoline_samples, 0.90),
    }


SCENARIOS = [
    dict(
        name="De-escalation",
        description="New ceasefire takes hold within 2-3 weeks, similar in character to the mid-June episode that drove Brent below $85 (P16) despite physical tightness staying elevated. Suppression re-engages hard.",
        tightness_shift_mean=-0.15, tightness_shift_sd=0.25,
        tightness_growth_per_month=0.0,   # ceasefire halts NEW tightening; existing deficit lingers but doesn't compound
        suppression_factor_mean=0.85, suppression_factor_sd=0.15,
        diesel_crack_mult_mean=0.55, diesel_crack_mult_sd=0.12,
        gasoline_crack_mult_mean=0.65, gasoline_crack_mult_sd=0.12,
        # Slight NEGATIVE drift: a ceasefire doesn't repair Bahrain/Kuwait/Iran
        # refineries in weeks (session finding: Gulf refinery damage is a
        # months-scale repair, not a switch), but it removes the war-premium
        # component riding on top of the physical damage, so cracks ease
        # slowly rather than staying frozen or rebounding.
        diesel_crack_growth_per_month=-0.04, gasoline_crack_growth_per_month=-0.02,
        # Saturating floor: settles toward pre-war-ish crack levels (diesel
        # ~$30, gasoline ~$20, both within the actual Oct25-Feb26 observed
        # range) rather than decaying toward zero or negative indefinitely.
        diesel_crack_ceiling_mult=0.35, gasoline_crack_ceiling_mult=0.35,
        horizon_label="4-8 weeks",
    ),
    dict(
        name="Status Quo Escalation",
        description="Current trajectory persists — Hormuz stays at ~90% independent-tracked disruption (dispute with CENTCOM unresolved), Bab al-Mandeb blockade holds but doesn't intensify, no new major refinery hits, SPR keeps drawing past its heel floor. DR-1's cap on hormuz_status plausibly lifts as FRED's Brent print catches up to real-time levels.",
        tightness_shift_mean=0.15, tightness_shift_sd=0.20,
        # 0.20/month: roughly HALF the full-war average pace (physical_tightness
        # rose ~2.15 over ~137 days Feb27->mid-Jul, ~0.47/month average) — using
        # half that because the last few weeks of actual data show deceleration/
        # plateau (1.948 Jul16 -> 1.806 Jul20), not the initial acceleration.
        tightness_growth_per_month=0.20,
        suppression_factor_mean=0.45, suppression_factor_sd=0.20,
        diesel_crack_mult_mean=1.05, diesel_crack_mult_sd=0.15,
        gasoline_crack_mult_mean=1.05, gasoline_crack_mult_sd=0.15,
        # Calibration: diesel crack rose ~35%/month over the hot Jun5-Jul17
        # stretch, but only ~5.6%/month averaged over the full Mar20-Jul17
        # window (the hot stretch doesn't sustain). Additive-to-multiplier
        # rate chosen as a damped blend of those two, NOT a straight average
        # of either — deliberately conservative given a 10-month compounding
        # horizon. Gasoline set lower per the crack brief's own finding that
        # its blowout looks more domestically (US inventory) driven than
        # diesel's Gulf-refinery-driven one.
        diesel_crack_growth_per_month=0.15, gasoline_crack_growth_per_month=0.08,
        # Saturating ceiling: ~2.8x today's already-elevated diesel crack
        # (~$240) and ~1.8x gasoline (~$103). Deliberately bounded well above
        # any pre-war level but short of the unbounded-compounding result
        # (which reached $218 at +300d even before extending further) —
        # this scenario is "current pace sustained," not "gets steadily worse
        # forever," so it should plateau, not keep climbing.
        diesel_crack_ceiling_mult=2.8, gasoline_crack_ceiling_mult=1.8,
        horizon_label="4-8 weeks",
    ),
    dict(
        name="Hormuz Confirmed Near-Full Closure",
        description="The independent-tracking read (~90% collapse) is vindicated over CENTCOM's dispute; hormuz_status effectively re-rates toward 0.85-1.0 without contradiction. No mines confirmed, but traffic is functionally at a standstill.",
        tightness_shift_mean=0.55, tightness_shift_sd=0.30,
        tightness_growth_per_month=0.35,   # faster than status quo — an active new closure event, not a plateau
        suppression_factor_mean=0.25, suppression_factor_sd=0.15,
        diesel_crack_mult_mean=1.25, diesel_crack_mult_sd=0.20,
        gasoline_crack_mult_mean=1.20, gasoline_crack_mult_sd=0.18,
        diesel_crack_growth_per_month=0.22, gasoline_crack_growth_per_month=0.12,
        diesel_crack_ceiling_mult=3.5, gasoline_crack_ceiling_mult=2.2,
        horizon_label="2-6 weeks",
    ),
    dict(
        name="Fujairah Bypass Strike",
        description=(
            "A renewed IRGC strike disables the Habshan-Fujairah pipeline (ADCOP) and/or the "
            "Fujairah Oil Industry Zone/VTTI terminal again — NOT hypothetical: this already "
            "happened twice (drone strikes Mar 14-16, 2026, forced an oil-loading suspension; "
            "a missile/drone barrage May 4-5, 2026 hit the VTTI terminal, 3 injured). No confirmed "
            "third strike tied to the current Jul 12+ re-escalation wave as of this scenario's "
            "definition. ADCOP is the UAE's ONLY current Hormuz-bypass route (1.5-1.8 mb/d) — the "
            "redundant West-East 1 pipeline (would double bypass capacity to 3.6 mb/d) is only "
            "~50% built, targeting early 2027, so right now is the point of maximum single-point-"
            "of-failure exposure for this specific target, not a stable ongoing risk level. "
            "Fujairah is also the world's 3rd-largest crude/products storage hub and a key bunker-"
            "fuel supplier, so this hits refined-product storage directly, not just crude export "
            "capacity — reflected in an elevated crack impact, comparable to or above Hormuz "
            "Confirmed Closure's. Calibrated as ADDITIVE to Status Quo Escalation's baseline "
            "(current Hormuz/Bab al-Mandeb disruption already priced in), not overlapping with "
            "Combined Escalation's Bab al-Mandeb-tankers-destroyed assumption — this is the "
            "Gulf-of-Oman-side bypass route, a physically distinct chokepoint from the Red Sea side."
        ),
        # Own contribution scaled proportionally from Combined Escalation's own
        # anchor (~4 mb/d Bab al-Mandeb volume <-> +1.1 tightness_shift):
        # 1.65 mb/d (midpoint of ADCOP's 1.5-1.8 mb/d) / 4 mb/d * 1.1 ~= 0.45,
        # added on top of Status Quo Escalation's existing 0.15 baseline since
        # this scenario is "current trajectory PLUS a fresh, specific, already-
        # precedented new shock," not a standalone alternate world.
        tightness_shift_mean=0.60, tightness_shift_sd=0.25,
        tightness_growth_per_month=0.25,   # a discrete, boundable event (single terminal) — plateaus faster than an open-ended siege
        # Lower than Status Quo/De-escalation's suppression: an actual terminal
        # fire is AIS/satellite/insurance-confirmable within hours, unlike the
        # disputed Hormuz traffic-volume question — less room for paper-market
        # suppression to hold against a physically unambiguous event.
        suppression_factor_mean=0.20, suppression_factor_sd=0.15,
        # Elevated crack impact given Fujairah's storage/bunkering role, not
        # just crude export — slightly above Hormuz Confirmed Closure's own.
        diesel_crack_mult_mean=1.35, diesel_crack_mult_sd=0.22,
        gasoline_crack_mult_mean=1.25, gasoline_crack_mult_sd=0.20,
        diesel_crack_growth_per_month=0.20, gasoline_crack_growth_per_month=0.10,
        diesel_crack_ceiling_mult=3.0, gasoline_crack_ceiling_mult=2.0,
        horizon_label="2-6 weeks",
    ),
    dict(
        name="Combined Escalation — Bab al-Mandeb Attacks + New Refinery Hit",
        description="Houthis move from blockade/reroute to actively attacking Saudi tankers (converting rerouted barrels into genuinely lost ones), plus at least one additional major Gulf refinery or export terminal is hit. Cross-checked against the vault's existing Scenario A (Strike/Zero Restraint: Brent $130-140) as an anchor, not invented independently.",
        tightness_shift_mean=1.1, tightness_shift_sd=0.40,
        # 0.50/month ~= the full-war-onset average pace — this scenario is
        # explicitly a fresh severe-escalation event, comparable in kind to
        # the initial Feb-Mar shock, so it's calibrated against that period
        # rather than the recent plateau.
        tightness_growth_per_month=0.50,
        suppression_factor_mean=0.10, suppression_factor_sd=0.10,
        diesel_crack_mult_mean=1.65, diesel_crack_mult_sd=0.30,
        gasoline_crack_mult_mean=1.40, gasoline_crack_mult_sd=0.25,
        # Fastest growth — a fresh refinery hit specifically targets product
        # output, and this scenario's whole premise is that repeats.
        diesel_crack_growth_per_month=0.30, gasoline_crack_growth_per_month=0.18,
        # Widest ceiling — deliberately still bounded (this is the scenario
        # that previously produced the implausible $398 uncapped result).
        # ~4.5x today's diesel crack (~$385) and ~2.8x gasoline (~$160) is
        # already an extreme tail; it should stop there, not compound past it.
        diesel_crack_ceiling_mult=4.5, gasoline_crack_ceiling_mult=2.8,
        horizon_label="2-6 weeks",
    ),
]

_GROWTH_KEYS = ("tightness_growth_per_month", "diesel_crack_growth_per_month", "gasoline_crack_growth_per_month",
                "diesel_crack_ceiling_mult", "gasoline_crack_ceiling_mult")


def _strip_growth(scenario: dict) -> dict:
    """run_scenario() doesn't take the *_growth_per_month fields — they're
    per-horizon adjustments applied externally (see run_spr_runway_projection),
    not static per-trial parameters. Strip them before calling run_scenario."""
    return {k: v for k, v in scenario.items() if k not in _GROWTH_KEYS}


# ── SPR runway → suppression-capacity decay ──────────────────────────────────
# Operationalizes the "shock absorbers vanish faster than chokepoints clear"
# thesis (session, 2026-07-22): paper-market/SPR suppression capacity is not
# a fixed parameter — it decays as the physical SPR runway shrinks. Modeled
# as a time-varying multiplier on each scenario's suppression_factor_mean.

SPR_NOW_MB = 311.447          # EIA WCSSTUS1, week of 2026-07-17 (most current pull)
SPR_DOE_MIN_MB = 273.0        # DOE minimum operating level — existing framework floor
SPR_NOMINAL_MB = 250.0        # "Nominal DOE" floor — existing framework floor
SPR_DRAW_RATE_LOW = 0.55      # mb/d — matches user-supplied milestones (300M@1mo/243M@4mo/150M@10mo)
SPR_DRAW_RATE_HIGH = 0.80     # mb/d — high end of user's stated 0.6-0.8 range
SPR_DEEP_REFERENCE_MB = 142.7  # single-sourced "20% of shell capacity" operational-floor claim
                               # (one podcast, "CEO of the American Petroleum Reserve" — NOT
                               # independently confirmed, NOT in the framework's confirmed-data
                               # layer). Used ONLY to shape how fast capacity decays once below
                               # the nominal floor — not asserted as a real hard floor. If this
                               # number turns out to be wrong, the decay curve's steepness changes,
                               # not the qualitative "capacity keeps eroding, doesn't plateau" fix.

# User-supplied milestone trajectory (2026-07-22), kept alongside the pure
# linear rate projections as a cross-check — not assumed to be more or less
# correct than the rate-based projection, just a second, independent estimate.
USER_MILESTONES_MB = {30: 300.0, 120: 243.0, 300: 150.0}   # {days_from_now: SPR_mb}


def spr_projection(days: float, rate: float = SPR_DRAW_RATE_LOW) -> float:
    """Linear SPR projection at a constant draw rate. Flagged in the doc as
    fragile beyond a few months — the actual rate has swung 2-3x within a
    single year and documented cavern degradation likely slows further draws
    as levels fall, so this is a floor-case (fastest depletion), not a
    prediction."""
    return max(0.0, SPR_NOW_MB - rate * days)


# ── China demand-restraint runway — a SECOND, independent shock absorber ─────
# Added 2026-07-22: China's crude imports are down ~41.3% (10-year low) during
# the war, but this is not primarily demand destruction — China entered 2026
# holding ~1.4B barrels of inventory (built through 2025) and has been drawing
# it down instead of competing for cargoes on the open market. That absence of
# Chinese bidding is itself a suppression mechanism, structurally identical to
# the US SPR: it has a finite runway, not infinite capacity, and it can break
# on ITS OWN schedule independent of Hormuz, Bab al-Mandeb, or the US SPR.
#
# Data quality caveat, stated plainly: this is far rougher than the US side.
# The US SPR is a single weekly EIA number, exact to the barrel. China's
# inventory and import-decline figures come from analyst estimates (IndexBox,
# Enerdata, JKempEnergy, Goldman, Bruegel) with real spread between sources —
# treat every number below as an order-of-magnitude estimate, not a fact.

CHINA_NORMAL_IMPORTS_MBD = 11.0          # widely-cited pre-war baseline range 10-11.5 mb/d
CHINA_IMPORT_DECLINE_PCT = 0.413         # confirmed direction/magnitude: Reuters/CNN, Jul 2026, "10-year low"
CHINA_IMPORT_SHORTFALL_MBD = CHINA_NORMAL_IMPORTS_MBD * CHINA_IMPORT_DECLINE_PCT   # ~4.5 mb/d not being bought

# Of the ~1.4B headline barrels, most of the ~1B "commercial" portion is
# working stock tied to ongoing refinery throughput, not a pure strategic
# buffer that can run to zero (same "heel" logic as the US SPR's caverns).
# Using a wide, explicitly-uncertain range for what's genuinely spendable
# before China is forced back into the market as a buyer.
CHINA_SPENDABLE_BUFFER_LOW_MB = 400
CHINA_SPENDABLE_BUFFER_HIGH_MB = 800

# Calibration for "how much does China fully re-entering the market move
# tightness": no regression exists for this, so it's anchored by analogy to
# the Combined Escalation scenario, which treats ~4mb/d of Bab al-Mandeb
# crude converting from rerouted-to-lost as a +1.1 tightness_shift_mean event.
# China's ~4.5mb/d shortfall returning to the market is comparable in size,
# so it's given a comparable full-return impact. This is an ANALOGY, not a
# fitted number — flagged clearly rather than presented as precise.
CHINA_FULL_RETURN_TIGHTNESS_IMPACT = 1.1


def china_restraint_multiplier(days: float, buffer_mb: float, shortfall_rate: float = CHINA_IMPORT_SHORTFALL_MBD) -> float:
    """
    1.0 = China's restraint fully intact (buffer not yet exhausted at the
    assumed drawdown rate). Decays toward 0 (restraint breaks, China returns
    to the market as a buyer) using the same exponential-below-the-line shape
    as the SPR capacity fix, anchored so the buffer's nominal exhaustion date
    is where decay begins in earnest, not a hard cliff.
    """
    days_to_exhaustion = buffer_mb / shortfall_rate if shortfall_rate > 0 else float("inf")
    if days <= days_to_exhaustion:
        # Still within the buffer window — mild decay as the buffer thins,
        # not full-strength restraint forever.
        frac_remaining = 1.0 - (days / days_to_exhaustion) if days_to_exhaustion > 0 else 0.0
        return 0.5 + 0.5 * max(0.0, frac_remaining)
    days_past = days - days_to_exhaustion
    decay_span = 60.0   # ~2 months past nominal exhaustion to approach near-zero restraint
    return 0.5 * math.exp(-days_past / decay_span)


def suppression_capacity_multiplier(spr_level: float) -> float:
    """
    1.0 = full suppression capacity (SPR at/above DOE minimum, 273mb).
    Degrades linearly from 1.0 to 0.15 between DOE minimum (273) and
    nominal floor (250) — unchanged from the original design.

    FIXED (previous version floored flat at 0.15 for anything <= 250,
    making +120d and +300d indistinguishable — that was the bug): below
    the nominal floor, capacity now continues to decay exponentially from
    0.15, rather than flatlining. Decay constant is set so capacity is
    down to about 0.15*exp(-1) ≈ 5.5% by SPR_DEEP_REFERENCE_MB (142.7mb —
    itself an unconfirmed single-source figure, used only to give the
    curve a plausible shape). Never reaches exactly zero — some residual
    token-release capacity is assumed to persist even at severe depletion,
    per the "degradation curve, not a cliff" finding in the EIA Storage
    Analysis brief.
    """
    if spr_level >= SPR_DOE_MIN_MB:
        return 1.0
    if spr_level > SPR_NOMINAL_MB:
        frac = (spr_level - SPR_NOMINAL_MB) / (SPR_DOE_MIN_MB - SPR_NOMINAL_MB)
        return 0.15 + 0.85 * frac
    decay_span = SPR_NOMINAL_MB - SPR_DEEP_REFERENCE_MB   # ~107.3 mb
    depth_below_floor = SPR_NOMINAL_MB - spr_level
    return 0.15 * math.exp(-depth_below_floor / decay_span)


def saturating_growth(base: float, ceiling: float, initial_rate_per_month: float, months: float) -> float:
    """
    Exponential approach to an asymptote, replacing unbounded linear growth
    for the crack multipliers (fixed 2026-07-22 — the linear version produced
    an implausible $398 diesel crack at +300d for Combined Escalation, with
    nothing in the model to push back on indefinite compounding).

    mult(t) = ceiling - (ceiling - base) * exp(-t / tau)
    tau chosen so the INITIAL slope at t=0 still matches initial_rate_per_month
    — i.e., short-horizon behavior (already checked against the +30d numbers)
    is preserved exactly; only the long-horizon behavior changes, decelerating
    toward `ceiling` instead of compounding past it forever.

    mult(0) = base (exactly). mult(t) -> ceiling as t -> infinity. Works
    identically whether ceiling is above base (growth) or below it (decay
    toward a floor, as in the De-escalation scenario) as long as the sign of
    (ceiling - base) matches the sign of initial_rate_per_month.
    """
    if initial_rate_per_month == 0 or ceiling == base:
        return base
    tau = (ceiling - base) / initial_rate_per_month
    if tau <= 0:
        return base   # inconsistent sign combination — fail safe to no growth rather than blow up
    return ceiling - (ceiling - base) * math.exp(-months / tau)


def run_spr_runway_projection(base_scenario: dict, horizons_days: list,
                               china_buffer_mb: float = None) -> list:
    """Re-run a base scenario at multiple future horizons, decaying its
    suppression_factor_mean by the SPR-runway capacity multiplier, growing
    its tightness_shift_mean LINEARLY (unchanged — not flagged as broken)
    but both crack multipliers via a SATURATING curve toward each scenario's
    ceiling_mult (fixed 2026-07-22 — linear crack growth produced an
    implausible $398 diesel crack at +300d for Combined Escalation), AND
    adding a China-restraint-breaking tightness contribution as China's own
    stockpile buffer depletes — four independent time-varying mechanisms
    running in parallel, not one. china_buffer_mb defaults to the midpoint
    of the 400-800mb spendable-buffer estimate if not given; pass the low or
    high end directly for the fast/slow-exhaustion sensitivity cases."""
    tightness_growth = base_scenario.get("tightness_growth_per_month", 0.0)
    diesel_growth = base_scenario.get("diesel_crack_growth_per_month", 0.0)
    gasoline_growth = base_scenario.get("gasoline_crack_growth_per_month", 0.0)
    diesel_ceiling = base_scenario.get("diesel_crack_ceiling_mult", base_scenario["diesel_crack_mult_mean"])
    gasoline_ceiling = base_scenario.get("gasoline_crack_ceiling_mult", base_scenario["gasoline_crack_mult_mean"])
    if china_buffer_mb is None:
        china_buffer_mb = (CHINA_SPENDABLE_BUFFER_LOW_MB + CHINA_SPENDABLE_BUFFER_HIGH_MB) / 2.0
    rows = []
    for days in horizons_days:
        spr_lin = spr_projection(days, rate=SPR_DRAW_RATE_LOW)
        spr_user = None
        if days in USER_MILESTONES_MB:
            spr_user = USER_MILESTONES_MB[days]
        capacity = suppression_capacity_multiplier(spr_lin)

        china_restraint = china_restraint_multiplier(days, china_buffer_mb)
        china_addback = (1.0 - china_restraint) * CHINA_FULL_RETURN_TIGHTNESS_IMPACT

        months = days / 30.0
        scenario = _strip_growth(base_scenario)
        scenario["suppression_factor_mean"] = base_scenario["suppression_factor_mean"] * capacity
        scenario["tightness_shift_mean"] = (base_scenario["tightness_shift_mean"]
                                             + tightness_growth * months
                                             + china_addback)
        scenario["diesel_crack_mult_mean"] = saturating_growth(
            base_scenario["diesel_crack_mult_mean"], diesel_ceiling, diesel_growth, months)
        scenario["gasoline_crack_mult_mean"] = saturating_growth(
            base_scenario["gasoline_crack_mult_mean"], gasoline_ceiling, gasoline_growth, months)
        scenario["name"] = "SPR Runway"
        scenario["description"] = ""
        scenario["horizon_label"] = f"+{days}d"
        r = run_scenario(**scenario)
        r["days"] = days
        r["spr_projected"] = spr_lin
        r["spr_user_milestone"] = spr_user
        r["capacity_multiplier"] = capacity
        r["china_restraint"] = china_restraint
        r["china_addback"] = china_addback
        r["effective_suppression_mean"] = scenario["suppression_factor_mean"]
        r["effective_tightness_shift"] = scenario["tightness_shift_mean"]
        r["effective_diesel_mult"] = scenario["diesel_crack_mult_mean"]
        r["effective_gasoline_mult"] = scenario["gasoline_crack_mult_mean"]
        rows.append(r)
    return rows


def print_spr_runway_table():
    """Runs the SPR runway projection against ALL FOUR scenarios on the same
    time axis (+30d/+120d/+300d), not just Status Quo Escalation — so they're
    directly comparable rather than mixing a projected scenario against three
    un-projected snapshot scenarios."""
    print("\n" + "=" * 100)
    print("  SPR RUNWAY APPLIED TO ALL SCENARIOS — comparable on the same time axis")
    print("  Rate used for SPR projection: 0.55 mb/d (matches user-supplied milestones, low end of 0.6-0.8 range)")
    print("=" * 100)

    horizons = [30, 120, 300]
    all_rows = {}
    for s in SCENARIOS:
        base_no_meta = {k: v for k, v in s.items() if k not in ("name", "description", "horizon_label")}
        all_rows[s["name"]] = run_spr_runway_projection(base_no_meta, horizons)

    print(f"\n  Shared SPR runway path (same physical trajectory under every scenario —")
    print(f"  what differs by scenario is how much suppression capacity and tightness matter):")
    print(f"  {'Horizon':<10}{'SPR (linear)':<14}{'SPR (user est.)':<16}{'Capacity mult.':<16}")
    reference_rows = next(iter(all_rows.values()))
    for r in reference_rows:
        user_str = f"{r['spr_user_milestone']:.0f}mb" if r['spr_user_milestone'] else "n/a"
        print(f"  +{r['days']}d{'':<6}{r['spr_projected']:<14.1f}{user_str:<16}{r['capacity_multiplier']:<16.2f}")

    name_w = max(len(n) for n in all_rows) + 2

    print(f"\n  Brent P50 by scenario x horizon:")
    print(f"  {'Scenario':<{name_w}}" + "".join(f"+{h}d".rjust(12) for h in horizons))
    for name, rows in all_rows.items():
        vals = "".join(f"${r['brent_p50']:.0f}".rjust(12) for r in rows)
        print(f"  {name:<{name_w}}{vals}")

    print(f"\n  Brent P10-P90 (uncertainty width) by scenario x horizon:")
    print(f"  {'Scenario':<{name_w}}" + "".join(f"+{h}d".rjust(20) for h in horizons))
    for name, rows in all_rows.items():
        vals = "".join(f"${r['brent_p10']:.0f}-${r['brent_p90']:.0f}".rjust(20) for r in rows)
        print(f"  {name:<{name_w}}{vals}")

    print(f"\n  Diesel crack P50 by scenario x horizon (now growing with time):")
    print(f"  {'Scenario':<{name_w}}" + "".join(f"+{h}d".rjust(12) for h in horizons))
    for name, rows in all_rows.items():
        vals = "".join(f"${r['diesel_p50']:.0f}".rjust(12) for r in rows)
        print(f"  {name:<{name_w}}{vals}")

    print(f"\n  Gasoline crack P50 by scenario x horizon (now growing with time):")
    print(f"  {'Scenario':<{name_w}}" + "".join(f"+{h}d".rjust(12) for h in horizons))
    for name, rows in all_rows.items():
        vals = "".join(f"${r['gasoline_p50']:.0f}".rjust(12) for r in rows)
        print(f"  {name:<{name_w}}{vals}")

    print(f"\n  Effective suppression / tightness / China / crack-multipliers by scenario x horizon (diagnostics):")
    for name, rows in all_rows.items():
        print(f"    {name}:")
        for r in rows:
            print(f"      +{r['days']}d: suppression={r['effective_suppression_mean']:.2f}  "
                  f"tightness={r['effective_tightness_shift']:.2f}  "
                  f"china_restraint={r['china_restraint']:.2f}  china_addback=+{r['china_addback']:.2f}  "
                  f"diesel_mult={r['effective_diesel_mult']:.2f}  gasoline_mult={r['effective_gasoline_mult']:.2f}")

    print(f"\n  China buffer sensitivity (Status Quo Escalation only) — fast vs. slow exhaustion case:")
    print(f"  Spendable buffer estimate ranges {CHINA_SPENDABLE_BUFFER_LOW_MB:.0f}-{CHINA_SPENDABLE_BUFFER_HIGH_MB:.0f}mb"
          f" against a ~{CHINA_IMPORT_SHORTFALL_MBD:.1f} mb/d shortfall — this range is a real analyst-estimate spread, not a rounding choice.")
    base_sqe = next(s for s in SCENARIOS if s["name"] == "Status Quo Escalation")
    base_sqe_no_meta = {k: v for k, v in base_sqe.items() if k not in ("name", "description", "horizon_label")}
    for label, buf in [("Fast exhaustion (400mb)", CHINA_SPENDABLE_BUFFER_LOW_MB),
                        ("Slow exhaustion (800mb)", CHINA_SPENDABLE_BUFFER_HIGH_MB)]:
        rows = run_spr_runway_projection(base_sqe_no_meta, horizons, china_buffer_mb=buf)
        vals = "  ".join(f"+{r['days']}d: ${r['brent_p50']:.0f}" for r in rows)
        print(f"    {label:<28}{vals}")

    print("""
  Reading it: every scenario now runs on the same SPR-runway clock instead of
  three of them being frozen at a short-horizon snapshot while only Status Quo
  moved with time. At +30d the ordering matches intuition (De-escalation <
  Status Quo < Hormuz Closure < Combined Escalation). By +120-300d that
  ordering can change — a scenario with a faster tightness_growth_per_month
  will eventually overtake one that started more severe but was calibrated
  for a short horizon. That crossover is a real feature of extending these
  assumptions in time, not a bug: read each column as "if the world looks
  like THIS scenario and stays that way for THIS long," not as a probability-
  weighted path that flows from one scenario into another.

  China addback: this is now a SECOND, independent shock-absorber clock,
  running alongside the US SPR one, not folded into it. China's restraint
  (running down its own ~1.4B bbl stockpile instead of competing for cargoes)
  is worth roughly +1.1 tightness_shift at full unwind by analogy to the
  Combined Escalation scenario's Bab al-Mandeb assumption (~4mb/d of demand
  is ~4mb/d of demand, whichever side of the ledger it's on) — but unlike the
  SPR side, the buffer size itself (400-800mb) is a genuine analyst-estimate
  spread, not a rounding choice, which is why the fast/slow sensitivity rows
  above matter: the fast-exhaustion case has China's restraint meaningfully
  cracking within the +120d window; the slow-exhaustion case pushes that out
  past +300d. This is the least-precise mechanism in the whole model — treat
  it as "a real, large, currently-active effect whose breaking point is
  genuinely unknown," not as a dated forecast the way the SPR floors are.

  Crack growth (SATURATING as of this round — the prior linear version is
  retired): diesel/gasoline crack multipliers now approach an explicit
  per-scenario ceiling (or floor, for De-escalation) via saturating_growth(),
  not unbounded linear compounding. The initial short-horizon rate is
  unchanged (still the damped blend of the hot Jun-Jul ~35%/month pace and
  the full-war ~5.6%/month average for Status Quo Escalation) — what changed
  is that it now decelerates on its own as it approaches the ceiling instead
  of compounding past it forever. Combined Escalation's +300d diesel crack
  dropped from an implausible $398 (uncapped) to $300 (bounded, converging
  toward a $385 ceiling it can never exceed). The ceilings themselves
  (2.8x-4.5x today's diesel crack across scenarios) are still assumptions,
  not fitted regressions — there is no historical episode of a 10-month
  sustained crack blowout to calibrate a ceiling against — so treat the
  ceiling VALUES as the least-tested inputs in the whole model, even though
  the curve SHAPE (saturating, not unbounded) is now on solid footing.

  Caveat repeated deliberately: this uses a CONSTANT draw rate for the SPR
  path, which is very likely wrong over a 10-month horizon (see docstring).
  Treat the +300d column as the fast-depletion case, not the central one.
""")


def main():
    print("\n" + "=" * 100)
    print("  ESCALATION PRICE SIMULATION — 2026-07-22")
    print("  Monte Carlo, N =", N_TRIALS, "trials per scenario. This is a scenario exercise, not a forecast.")
    print("=" * 100)

    print(f"\n  Anchor state: Brent ${BRENT_NOW:.2f} (real-time) | physical_tightness {PHYSICAL_TIGHTNESS_NOW:.3f}")
    print(f"  Diesel crack ${DIESEL_CRACK_NOW:.2f} | Gasoline crack ${GASOLINE_CRACK_NOW:.2f} (both Brent-basis, Jul 17)")
    fair_value_now = brent_from_tightness(PHYSICAL_TIGHTNESS_NOW)
    print(f"  'Fair value today' if physical tightness were fully priced in (divergence->0): ${fair_value_now:.2f}")
    print(f"  (i.e., even with ZERO further escalation, there's a case Brent already 'owes' a move toward this level)")

    results = []
    for s in SCENARIOS:
        r = run_scenario(**_strip_growth(s))
        results.append(r)

    print("\n" + "-" * 100)
    print(f"  {'Scenario':<42}{'Horizon':<12}{'Brent P10-P50-P90':<24}{'Diesel crack P50':<18}{'Gasoline crack P50':<18}")
    print("-" * 100)
    for r in results:
        brent_str = f"${r['brent_p10']:.0f}-${r['brent_p50']:.0f}-${r['brent_p90']:.0f}"
        print(f"  {r['name']:<42}{r['horizon']:<12}{brent_str:<24}${r['diesel_p50']:<17.0f}${r['gasoline_p50']:<17.0f}")

    print_spr_runway_table()

    print("\n" + "=" * 100)
    print("  FULL DETAIL")
    print("=" * 100)
    for s, r in zip(SCENARIOS, results):
        print(f"\n  ── {r['name']} ({r['horizon']}) ──")
        print(f"  {s['description']}")
        print(f"    Brent:    P10 ${r['brent_p10']:.2f}  |  P50 ${r['brent_p50']:.2f}  |  P90 ${r['brent_p90']:.2f}")
        print(f"    WTI:      P10 ${r['wti_p10']:.2f}  |  P50 ${r['wti_p50']:.2f}  |  P90 ${r['wti_p90']:.2f}")
        print(f"    Diesel crack:   P10 ${r['diesel_p10']:.2f}  |  P50 ${r['diesel_p50']:.2f}  |  P90 ${r['diesel_p90']:.2f}")
        print(f"    Gasoline crack: P10 ${r['gasoline_p10']:.2f}  |  P50 ${r['gasoline_p50']:.2f}  |  P90 ${r['gasoline_p90']:.2f}")

    print("\n" + "=" * 100)
    print("  CAVEATS (read before using any of this)")
    print("=" * 100)
    print("""
  1. This is a scenario/sensitivity exercise, NOT a probabilistic forecast with
     calibrated scenario likelihoods. No probability is assigned to which
     scenario occurs — that is a separate, harder question this script does
     not answer.
  2. The framework's own track record includes a severe magnitude miss (P16:
     assigned 4% to Brent<$85, it happened) driven by underestimating paper-
     market suppression. The wide P10-P90 bands here, especially the
     suppression_factor parameter, are a direct response to that lesson —
     treat any narrower range you construct from this data with suspicion.
  3. Brent-WTI spread, crack multipliers, and tightness shifts are stated
     assumptions, not fitted parameters — there is no historical regression
     backing the exact numbers, only the direction and rough magnitude
     reasoned from this war's own prior episodes (documented in each
     scenario's description field).
  4. Re-run this script as new data lands (fresh physical_tightness from
     market_mechanics_daily, fresh crack spreads from EIA) rather than
     treating today's output as static.
""")


if __name__ == "__main__":
    main()
