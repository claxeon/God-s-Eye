#!/usr/bin/env python3
"""
God's Eye — Agent-Based Simulation Engine v1.0
================================================
Multi-actor geopolitical simulation for the 2026 Persian Gulf conflict scenario.

Framework: Full Convergence Strategic Intelligence — 9-Leg Convergence Model
Horizon:   June 8 – December 31, 2026 (weekly time steps)
Actors:    13 primary nodes + 7 subgroups
Output:    Scenario probability distribution (A/B/C/D/E) with Monte Carlo CI bands

Architecture:
  Layer 1 — Agent Core       Each actor has a computable utility function,
                              constraint set, and IF/THEN behavior model.
  Layer 2 — Coupling Graph   Events propagate through 7 coupling rules.
                              Decisions by one actor update world state for all others.
  Layer 3 — Monte Carlo      N stochastic simulation runs over the horizon.
                              Samples: BOJ timing, Houthi action, ceasefire probability,
                              private credit cascade, Tether de-peg, Israel unilateral.
  Layer 4 — Output           Scenario probability distribution + leg score trajectories
                              + actor state timelines. JSON-serializable for React demo.

Usage:
    python3 gods_eye_engine.py
    python3 gods_eye_engine.py --simulations 1000 --output results.json
    python3 gods_eye_engine.py --fire BOJ_HIKE --fire BAB_AL_MANDAB

Scenarios:
    A  Strike / Zero Restraint      — US/Israel hit Iranian power plants; IRGC zero restraint
    B  Back Down / Duration         — Ceasefire cycle; flash crash Jun 16–25; duration repricing
    C  Back Channel Deal            — Qatar mediates; Hormuz partial open; thesis delayed
    D  IRGC Mines                   — Physical Hormuz closure; $160+ Brent
    E  Dual Chokepoint              — Hormuz + Yanbu; $200+ Brent; Scenario E analog
"""

import random
import math
import json
import argparse
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any
from datetime import date, timedelta
from enum import Enum
from copy import deepcopy

# ── Calibrated priors (P-035) ─────────────────────────────────────────────────
# Market-noise parameters are fetched from Supabase calibration_params (written
# by sde_priors.py from full FRED history) at startup, with these hand-set
# values as offline fallback. SCOPE: only the MARKET-NOISE layer is calibrated —
# actor event probabilities (mine_prob, bab_prob, ...) are conditional
# mechanisms that ARE the framework's thesis and have no historical analogue;
# they stay hand-set and are validated through the prediction Brier record
# instead. Endogenous price JUMPS are deliberately NOT added: the agent/event
# system already produces them; adding SDE jumps would double-count.

_CALIB_DEFAULTS = {
    "SDE_DCOILBRENTEU":         {"mu": 0.034, "sigma": 0.403},   # ann log-ret
    "SDE_DCOILBRENTEU_REGIME":  {"mu": 0.25,  "sigma": 0.624, "weight": 16.0},
    "SDE_DEXJPUS":              {"mu": -0.014, "sigma": 0.101},
    "SDE_DEXJPUS_REGIME":       {"mu": 0.25,  "sigma": 0.132, "weight": 17.1},
    "SDE_VIXCLS":               {"mu": -0.001, "sigma": 1.081},
    "SDE_VIXCLS_REGIME":        {"mu": 0.25,  "sigma": 1.400, "weight": 18.3},
}
CALIB: Dict[str, Dict[str, float]] = {k: dict(v) for k, v in _CALIB_DEFAULTS.items()}
CALIB_SOURCE = "fallback (hand-set defaults)"


def load_calibrated_priors(quiet: bool = False) -> None:
    """Fetch calibration_params via Supabase REST; fall back silently offline."""
    global CALIB_SOURCE
    url = ("https://snykuqyceqpplnzmyksp.supabase.co/rest/v1/calibration_params"
           "?select=series_id,mu,sigma,weight&series_id=like.SDE_*")
    key = "sb_publishable_TJg65x5w56CulOEdWFJNyQ_89loJtit"
    try:
        r = subprocess.run(["curl", "-s", "--max-time", "8", url,
                            "-H", f"apikey: {key}",
                            "-H", f"Authorization: Bearer {key}"],
                           capture_output=True, text=True)
        rows = json.loads(r.stdout)
        loaded = 0
        for row in rows:
            sid = row["series_id"]
            if sid in CALIB:
                CALIB[sid] = {"mu": float(row["mu"]), "sigma": float(row["sigma"]),
                              "weight": float(row.get("weight") or 1.0)}
                loaded += 1
        if loaded:
            CALIB_SOURCE = f"calibration_params ({loaded} series, Supabase)"
    except Exception:
        pass  # offline: defaults stand
    if not quiet:
        print(f"  Market-noise priors: {CALIB_SOURCE}")
        b, br = CALIB["SDE_DCOILBRENTEU"], CALIB["SDE_DCOILBRENTEU_REGIME"]
        print(f"    Brent ann vol {b['sigma']:.1%} (hi-regime {br['sigma']:.1%}, "
              f"{br['mu']:.0%} of time, ~{br.get('weight',16)/7:.1f}wk episodes) — "
              f"prev hand-set noise implied ~22% ann")
        j = CALIB["SDE_DEXJPUS"]
        print(f"    USD/JPY ann vol {j['sigma']:.1%} — prev hand-set implied ~2% ann")


def _wk(sigma_ann: float) -> float:
    return sigma_ann / math.sqrt(52.0)

# ── Simulation parameters ─────────────────────────────────────────────────────

SIM_START      = date(2026, 6, 8)
SIM_END        = date(2026, 12, 31)
DEFAULT_RUNS   = 1000
WEEKLY_STEPS   = (SIM_END - SIM_START).days // 7  # ~29 weeks

# ── Enumerations ──────────────────────────────────────────────────────────────

class Scenario(str, Enum):
    A = "A"   # Strike / Zero Restraint
    B = "B"   # Back Down / Duration (base case)
    C = "C"   # Back Channel Deal
    D = "D"   # IRGC Mines
    E = "E"   # Dual Chokepoint

class HormuzStatus(str, Enum):
    CLOSED     = "closed"
    TOLL       = "toll"       # PGSA selective access
    PARTIAL    = "partial"    # Scenario C partial open
    OPEN       = "open"

class ConstraintBand(str, Enum):
    CB_C = "CB-C"
    CB_D = "CB-D"
    CB_E = "CB-E"

# ── Events ────────────────────────────────────────────────────────────────────

class EventType(str, Enum):
    # Financial / Monetary
    BOJ_HIKE              = "BOJ_HIKE"
    CARRY_UNWIND_BEGINS   = "CARRY_UNWIND_BEGINS"
    FLASH_CRASH           = "FLASH_CRASH"
    TIC_OFFICIAL_SELLING  = "TIC_OFFICIAL_SELLING"
    SPR_FLOOR_HIT         = "SPR_FLOOR_HIT"
    GENIUS_ACT_SIGNED     = "GENIUS_ACT_SIGNED"
    TETHER_DEPEG          = "TETHER_DEPEG"
    PRIVATE_CREDIT_GATE   = "PRIVATE_CREDIT_GATE"
    CREDIT_CASCADE        = "CREDIT_CASCADE"
    FED_EMERGENCY_CUT     = "FED_EMERGENCY_CUT"
    # Kinetic / Geopolitical
    CEASEFIRE_SIGNAL      = "CEASEFIRE_SIGNAL"
    CEASEFIRE_COLLAPSE    = "CEASEFIRE_COLLAPSE"
    ISRAEL_UNILATERAL     = "ISRAEL_UNILATERAL"
    IRGC_MINE_DEPLOYMENT  = "IRGC_MINE_DEPLOYMENT"
    BAB_AL_MANDAB         = "BAB_AL_MANDAB"
    YANBU_STRIKE          = "YANBU_STRIKE"
    HORMUZ_PARTIAL_OPEN   = "HORMUZ_PARTIAL_OPEN"
    PGSA_TOLLS_FORMALIZED = "PGSA_TOLLS_FORMALIZED"
    # Energy / Commodity
    SPR_DEGRADED          = "SPR_DEGRADED"
    BRENT_SPIKE           = "BRENT_SPIKE"
    CRACK_SPREAD_PEAK     = "CRACK_SPREAD_PEAK"
    # Actor decisions
    SAUDI_PEG_REVIEW      = "SAUDI_PEG_REVIEW"
    BOJ_REASSURANCE       = "BOJ_REASSURANCE"
    USD_JPY_BREAKS_155    = "USD_JPY_BREAKS_155"
    IRC_PHASE5_FAMINE     = "IRC_PHASE5_FAMINE"

@dataclass
class Event:
    type:        EventType
    week:        int
    date:        date
    source:      str
    description: str
    leg_impacts: Dict[str, float] = field(default_factory=dict)  # leg → delta
    confirmed:   bool = True
    magnitude:   float = 1.0   # 0–1 scale; 1.0 = full impact

# ── World State ───────────────────────────────────────────────────────────────

@dataclass
class WorldState:
    """
    Complete state of the simulation at a given week.
    All actor decisions and coupling propagations modify this object.
    """
    week:   int
    date:   date

    # ── Leg scores (0–1 probability of thesis confirmation) ──
    leg_scores: Dict[str, float] = field(default_factory=lambda: {
        "leg_1": 0.99,   # War / Energy Chokepoints
        "leg_2": 0.89,   # GCC / Petrodollar Strain
        "leg_3": 0.67,   # Private Credit / NBFI
        "leg_4": 0.32,   # Rails / XRP / Stablecoin
        "leg_5": 0.83,   # Food / Fertilizer
        "leg_6": 0.45,   # Munitions / MIC
        "leg_7": 0.42,   # Semiconductor / Taiwan
        "leg_8": 0.89,   # Maritime / Insurance — updated Jun 8: Houthi declared blockade confirmed
        "leg_9": 0.38,   # AI / Labor
        "cross": 1.00,   # Cross-cutting JPY carry
    })

    # ── Macro variables ──
    brent_price:   float = 118.0   # $/bbl
    usd_jpy:       float = 159.0
    vix:           float = 22.0
    us_10y_yield:  float = 4.60    # %
    boj_rate:      float = 0.75    # %
    spr_mmbbl:     float = 357.1
    henry_hub:     float = 3.80    # $/MMBtu

    # ── Structural flags ──
    hormuz_status:        HormuzStatus  = HormuzStatus.TOLL
    constraint_band:      ConstraintBand = ConstraintBand.CB_D
    pgsa_active:          bool = True
    vol_regime_hi:        bool = False   # P-035: calibrated 2-state vol regime

    # ── Event flags (irreversible once set) ──
    boj_hiked:            bool = False
    carry_unwind_active:  bool = False
    flash_crash_occurred: bool = False
    bab_al_mandab_declared: bool = True   # Jun 8 confirmed: Houthi declared Israeli ship ban
    bab_al_mandab_closed: bool = False    # physical AIS closure — not yet confirmed
    yanbu_struck:         bool = False
    ceasefire_active:     bool = False
    israel_struck:        bool = False
    irgc_mines_deployed:  bool = False
    genius_act_signed:    bool = False
    tether_depegged:      bool = False
    credit_cascade:       bool = False
    spr_floor_hit:        bool = False
    saudi_peg_review:     bool = False
    famine_declared:      bool = False
    fed_cut:              bool = False
    tic_selling_confirmed:bool = False

    # ── Scenario probabilities ──
    scenario_probs: Dict[str, float] = field(default_factory=lambda: {
        "A": 0.12,   # Strike / Zero Restraint
        "B": 0.38,   # Back Down / Duration (base case)
        "C": 0.28,   # Back Channel Deal
        "D": 0.15,   # IRGC Mines
        "E": 0.07,   # Dual Chokepoint
    })

    # ── Active events this week ──
    events_this_week: List[Event] = field(default_factory=list)

    @property
    def composite_score(self) -> float:
        weights = {
            "leg_1": 0.20, "leg_2": 0.15, "leg_3": 0.12,
            "leg_4": 0.08, "leg_5": 0.12, "leg_6": 0.08,
            "leg_7": 0.08, "leg_8": 0.10, "leg_9": 0.07,
        }
        return sum(self.leg_scores.get(k, 0) * w for k, w in weights.items())

    @property
    def dominant_scenario(self) -> str:
        return max(self.scenario_probs, key=self.scenario_probs.get)

    def clone(self) -> "WorldState":
        ws = deepcopy(self)
        # preserve events_this_week so history records what fired each week
        return ws

# ── Weighted Probability — Weight / Counterweight Framework ───────────────────

class WeightedProb:
    """
    Computes effective weekly event probability as:
        base × Π(escalation multipliers) × Π(suppression multipliers)

    Escalation weights:   conditions that increase probability (threat, revenue pressure, etc.)
    Counterweights:       conditions that suppress probability (deterrence, ceasefire,
                          institutional incentives like PGSA toll revenue, Fed backstop, etc.)

    The distinction between weights and counterweights is analytical, not just
    mathematical — counterweights represent the God's Eye coupling rules operating
    in the suppressive direction.

    Example:
        prob = (WeightedProb(0.012)
                .escalate_if(state.israel_struck,    4.0, "IRGC retaliatory pressure")
                .escalate_if(state.brent_price < 85, 2.0, "Revenue floor breach")
                .suppress_if(state.pgsa_active,      0.35,"PGSA toll revenue — closure costly")
                .suppress_if(state.ceasefire_active, 0.20,"Active ceasefire window")
                .suppress_if(state.fed_cut,          0.60,"Fed backstop reduces cascade speed")
                .cap(0.18)
                .compute())
    """

    def __init__(self, base: float):
        self._p   = float(base)
        self._log: list = []

    def escalate_if(self, condition: bool, multiplier: float,
                    reason: str = "") -> "WeightedProb":
        if condition and multiplier > 1.0:
            self._p *= multiplier
            self._log.append(("+", multiplier, reason))
        return self

    def suppress_if(self, condition: bool, multiplier: float,
                    reason: str = "") -> "WeightedProb":
        """multiplier should be < 1.0 (e.g. 0.35 = 65% suppression)."""
        if condition and 0.0 <= multiplier < 1.0:
            self._p *= multiplier
            self._log.append(("-", multiplier, reason))
        return self

    def cap(self, maximum: float) -> "WeightedProb":
        self._p = min(self._p, maximum)
        return self

    def floor_val(self, minimum: float) -> "WeightedProb":
        self._p = max(self._p, minimum)
        return self

    def compute(self) -> float:
        return max(0.0, min(1.0, self._p))

    def roll(self, rng: random.Random) -> bool:
        return rng.random() < self.compute()

# ── Coupling Rules Engine ─────────────────────────────────────────────────────

class CouplingEngine:
    """
    Implements the 7 God's Eye coupling rules.
    When an event fires, propagate its effects across all connected state variables.
    """

    @staticmethod
    def propagate(event: Event, state: WorldState) -> WorldState:
        handler = {
            EventType.BOJ_HIKE:              CouplingEngine._boj_hike,
            EventType.CARRY_UNWIND_BEGINS:   CouplingEngine._carry_unwind,
            EventType.FLASH_CRASH:           CouplingEngine._flash_crash,
            EventType.TIC_OFFICIAL_SELLING:  CouplingEngine._tic_selling,
            EventType.SPR_FLOOR_HIT:         CouplingEngine._spr_floor,
            EventType.BAB_AL_MANDAB:         CouplingEngine._bab_al_mandab,
            EventType.YANBU_STRIKE:          CouplingEngine._yanbu_strike,
            EventType.CEASEFIRE_SIGNAL:      CouplingEngine._ceasefire,
            EventType.CEASEFIRE_COLLAPSE:    CouplingEngine._ceasefire_collapse,
            EventType.ISRAEL_UNILATERAL:     CouplingEngine._israel_strike,
            EventType.IRGC_MINE_DEPLOYMENT:  CouplingEngine._irgc_mines,
            EventType.HORMUZ_PARTIAL_OPEN:   CouplingEngine._hormuz_open,
            EventType.PRIVATE_CREDIT_GATE:   CouplingEngine._credit_gate,
            EventType.CREDIT_CASCADE:        CouplingEngine._credit_cascade,
            EventType.GENIUS_ACT_SIGNED:     CouplingEngine._genius_act,
            EventType.TETHER_DEPEG:          CouplingEngine._tether,
            EventType.SAUDI_PEG_REVIEW:      CouplingEngine._saudi_peg,
            EventType.FED_EMERGENCY_CUT:     CouplingEngine._fed_cut,
            EventType.IRC_PHASE5_FAMINE:     CouplingEngine._famine,
            EventType.USD_JPY_BREAKS_155:    CouplingEngine._usd_jpy_break,
        }.get(event.type)

        if handler:
            state = handler(event, state)

        # Apply direct leg impacts from event definition
        for leg, delta in event.leg_impacts.items():
            if leg in state.leg_scores:
                state.leg_scores[leg] = min(1.0, max(0.0,
                    state.leg_scores[leg] + delta * event.magnitude))

        state.events_this_week.append(event)
        return state

    # ── Coupling Rule 1: Energy–Currency ──

    @staticmethod
    def _boj_hike(event: Event, state: WorldState) -> WorldState:
        """BOJ hike triggers carry unwind cascade. Minimum 1.67× Aug 2024."""
        state.boj_hiked = True
        state.boj_rate += 0.25
        # Yen strengthens immediately
        state.usd_jpy -= random.uniform(3.0, 6.0)
        # Carry unwind begins within days
        state.carry_unwind_active = True
        # Leg 2 update: BOJ distributing USTs to fund repatriation
        state.leg_scores["leg_2"] = min(1.0, state.leg_scores["leg_2"] + 0.03)
        # Scenario probabilities: B (flash crash) becomes dominant
        state.scenario_probs["B"] = min(0.90, state.scenario_probs["B"] + 0.15)
        state.scenario_probs["C"] -= 0.05
        state.scenario_probs["A"] -= 0.03
        _normalize_scenarios(state)
        return state

    @staticmethod
    def _carry_unwind(event: Event, state: WorldState) -> WorldState:
        """Coupling Rule 2: Carry cascade. 1.67× Aug 2024 at min."""
        multiplier = 1.67 * event.magnitude
        state.vix          = min(90.0, state.vix + 35.0 * multiplier)
        state.us_10y_yield += 0.40 * multiplier
        state.usd_jpy      -= random.uniform(4.0, 8.0) * multiplier
        state.carry_unwind_active = True
        # VIX > 40 → flash crash triggers — emit as a tracked event
        if state.vix > 40.0 and not state.flash_crash_occurred:
            crash_event = Event(
                type=EventType.FLASH_CRASH,
                week=state.week,
                date=state.date,
                source="CouplingEngine._carry_unwind",
                description=(f"Flash crash. VIX {state.vix:.0f}. USD/JPY {state.usd_jpy:.1f}. "
                             f"S&P breaks 6,200. 10Y +50bp. 1.67× Aug 2024. No containment floor."),
                magnitude=min(2.0, state.vix / 35.0),
            )
            state = CouplingEngine._flash_crash(crash_event, state)
            state.events_this_week.append(crash_event)
        return state

    @staticmethod
    def _flash_crash(event: Event, state: WorldState) -> WorldState:
        state.flash_crash_occurred = True
        state.vix = max(state.vix, 45.0 + random.uniform(0, 25.0))
        state.us_10y_yield += 0.30
        state.constraint_band = ConstraintBand.CB_E
        state.leg_scores["leg_3"] = min(1.0, state.leg_scores["leg_3"] + 0.08)
        state.scenario_probs["B"] = min(0.95, state.scenario_probs["B"] + 0.20)
        _normalize_scenarios(state)
        return state

    # ── Coupling Rule 2: Carry Trade Cascade ──

    @staticmethod
    def _usd_jpy_break(event: Event, state: WorldState) -> WorldState:
        """USD/JPY breaks below 155 → algorithmic stop-losses trigger."""
        if state.usd_jpy > 155.0:
            return state
        state.carry_unwind_active = True
        state.vix = min(80.0, state.vix + 20.0)
        state.leg_scores["cross"] = 1.0
        state.scenario_probs["B"] = min(0.85, state.scenario_probs["B"] + 0.12)
        _normalize_scenarios(state)
        return state

    # ── Coupling Rule 3: Private Credit Gate Cascade ──

    @staticmethod
    def _credit_gate(event: Event, state: WorldState) -> WorldState:
        """New fund gating event. 3+ = cascade."""
        state.leg_scores["leg_3"] = min(1.0, state.leg_scores["leg_3"] + 0.05)
        if state.leg_scores["leg_3"] > 0.80:
            state.credit_cascade = True
        return state

    @staticmethod
    def _credit_cascade(event: Event, state: WorldState) -> WorldState:
        state.credit_cascade = True
        state.leg_scores["leg_3"] = min(1.0, state.leg_scores["leg_3"] + 0.12)
        state.vix = min(85.0, state.vix + 15.0)
        state.constraint_band = ConstraintBand.CB_E
        return state

    # ── Coupling Rule 4: Petrodollar Erosion ──

    @staticmethod
    def _tic_selling(event: Event, state: WorldState) -> WorldState:
        state.tic_selling_confirmed = True
        state.leg_scores["leg_2"] = min(1.0, state.leg_scores["leg_2"] + 0.04)
        state.us_10y_yield += 0.15
        return state

    @staticmethod
    def _saudi_peg(event: Event, state: WorldState) -> WorldState:
        """Saudi dollar peg review = Leg 2 nuclear option."""
        state.saudi_peg_review = True
        state.leg_scores["leg_2"] = min(1.0, state.leg_scores["leg_2"] + 0.12)
        state.scenario_probs["D"] = min(0.30, state.scenario_probs["D"] + 0.05)
        _normalize_scenarios(state)
        return state

    # ── Coupling Rule 5: Energy Supply Destruction Permanence ──

    @staticmethod
    def _spr_floor(event: Event, state: WorldState) -> WorldState:
        """SPR floor hit: last price suppression mechanism expires."""
        state.spr_floor_hit = True
        state.leg_scores["leg_1"] = min(1.0, state.leg_scores["leg_1"] + 0.005)
        # Brent spikes as suppression mechanism removes
        state.brent_price += random.uniform(8.0, 18.0)
        return state

    # ── Coupling Rule 6: Ceasefire–Yield Correlation ──

    @staticmethod
    def _ceasefire(event: Event, state: WorldState) -> WorldState:
        """Ceasefire signal: temporary yield compression, Leg 1 softening."""
        state.ceasefire_active = True
        state.us_10y_yield -= 0.12  # Temporary suppression
        state.brent_price -= random.uniform(5.0, 12.0)
        state.leg_scores["leg_1"] = max(0.45, state.leg_scores["leg_1"] - 0.08)
        state.scenario_probs["C"] = min(0.60, state.scenario_probs["C"] + 0.15)
        state.scenario_probs["B"] -= 0.08
        state.scenario_probs["A"] -= 0.05
        _normalize_scenarios(state)
        return state

    @staticmethod
    def _ceasefire_collapse(event: Event, state: WorldState) -> WorldState:
        """Ceasefire collapse: yield rebounds, Leg 1 returns."""
        state.ceasefire_active = False
        state.us_10y_yield += 0.15
        state.brent_price += random.uniform(4.0, 10.0)
        state.leg_scores["leg_1"] = min(1.0, state.leg_scores["leg_1"] + 0.05)
        state.scenario_probs["C"] = max(0.05, state.scenario_probs["C"] - 0.10)
        state.scenario_probs["B"] += 0.05
        _normalize_scenarios(state)
        return state

    # ── Coupling Rule 7: SPR Degradation ──

    @staticmethod
    def _hormuz_open(event: Event, state: WorldState) -> WorldState:
        """Partial Hormuz open (Scenario C): thesis delayed, not broken."""
        state.hormuz_status = HormuzStatus.PARTIAL
        state.brent_price -= random.uniform(15.0, 25.0)
        state.leg_scores["leg_1"] = max(0.45, state.leg_scores["leg_1"] - 0.15)
        state.leg_scores["leg_8"] = max(0.40, state.leg_scores["leg_8"] - 0.10)
        state.scenario_probs["C"] = min(0.70, state.scenario_probs["C"] + 0.25)
        state.scenario_probs["B"] -= 0.15
        state.scenario_probs["D"] -= 0.05
        _normalize_scenarios(state)
        return state

    # ── Kinetic Events ──

    @staticmethod
    def _bab_al_mandab(event: Event, state: WorldState) -> WorldState:
        """Dual chokepoint: Hormuz + Bab al-Mandab → Scenario E."""
        state.bab_al_mandab_closed = True
        state.leg_scores["leg_8"] = min(1.0, state.leg_scores["leg_8"] + 0.10)
        state.leg_scores["leg_1"] = min(1.0, state.leg_scores["leg_1"] + 0.005)
        state.brent_price += random.uniform(20.0, 40.0)
        state.scenario_probs["E"] = min(0.60, state.scenario_probs["E"] + 0.25)
        state.scenario_probs["B"] -= 0.10
        state.scenario_probs["C"] -= 0.10
        _normalize_scenarios(state)
        return state

    @staticmethod
    def _yanbu_strike(event: Event, state: WorldState) -> WorldState:
        """Yanbu strike: Saudi bypass eliminated. Scenario E locked."""
        state.yanbu_struck = True
        state.brent_price += random.uniform(40.0, 70.0)
        state.leg_scores["leg_1"] = 1.0
        state.leg_scores["leg_8"] = min(1.0, state.leg_scores["leg_8"] + 0.10)
        state.scenario_probs["E"] = min(0.85, state.scenario_probs["E"] + 0.40)
        _normalize_scenarios(state)
        return state

    @staticmethod
    def _israel_strike(event: Event, state: WorldState) -> WorldState:
        """Israel unilateral strike: forces US escalation, Scenario A."""
        state.israel_struck = True
        state.brent_price += random.uniform(15.0, 30.0)
        state.vix = min(80.0, state.vix + 20.0)
        state.leg_scores["leg_1"] = 1.0
        state.leg_scores["leg_6"] = min(1.0, state.leg_scores["leg_6"] + 0.15)
        state.scenario_probs["A"] = min(0.70, state.scenario_probs["A"] + 0.30)
        state.scenario_probs["C"] -= 0.15
        state.scenario_probs["B"] -= 0.10
        _normalize_scenarios(state)
        return state

    @staticmethod
    def _irgc_mines(event: Event, state: WorldState) -> WorldState:
        """IRGC mine deployment: physical Hormuz closure, Scenario D."""
        state.irgc_mines_deployed = True
        state.hormuz_status = HormuzStatus.CLOSED
        state.brent_price += random.uniform(25.0, 45.0)
        state.leg_scores["leg_1"] = 1.0
        state.leg_scores["leg_8"] = min(1.0, state.leg_scores["leg_8"] + 0.10)
        state.scenario_probs["D"] = min(0.75, state.scenario_probs["D"] + 0.35)
        state.scenario_probs["C"] -= 0.15
        state.scenario_probs["B"] -= 0.10
        _normalize_scenarios(state)
        return state

    # ── Financial / Policy Events ──

    @staticmethod
    def _genius_act(event: Event, state: WorldState) -> WorldState:
        state.genius_act_signed = True
        state.leg_scores["leg_4"] = min(1.0, state.leg_scores["leg_4"] + 0.15)
        # Partial Leg 2 offset — stablecoin UST demand
        state.leg_scores["leg_2"] = max(0.0, state.leg_scores["leg_2"] - 0.02)
        return state

    @staticmethod
    def _tether(event: Event, state: WorldState) -> WorldState:
        state.tether_depegged = True
        state.leg_scores["leg_4"] = min(1.0, state.leg_scores["leg_4"] + 0.10)
        state.vix = min(80.0, state.vix + 10.0)
        return state

    @staticmethod
    def _fed_cut(event: Event, state: WorldState) -> WorldState:
        """Emergency Fed cut: 5-8% equity bounce, temporary. Does not fix supply shock."""
        state.fed_cut = True
        state.vix = max(15.0, state.vix - 15.0)
        state.us_10y_yield -= 0.25
        # Does not change leg scores — supply shock unchanged
        return state

    @staticmethod
    def _famine(event: Event, state: WorldState) -> WorldState:
        state.famine_declared = True
        state.leg_scores["leg_5"] = min(1.0, state.leg_scores["leg_5"] + 0.08)
        return state


def _normalize_scenarios(state: WorldState):
    """Ensure scenario probabilities sum to 1.0 and stay non-negative."""
    total = sum(state.scenario_probs.values())
    if total > 0:
        for k in state.scenario_probs:
            state.scenario_probs[k] = max(0.01, state.scenario_probs[k] / total)

# ── Actor Base Class ──────────────────────────────────────────────────────────

class AgentBase:
    """
    Base class for all God's Eye actors.
    Each actor evaluates world state weekly and emits events based on
    their utility function and behavior model.
    """
    name:    str = "base"
    tier:    int = 4

    def __init__(self):
        self.activated = False

    def decide(self, state: WorldState, rng: random.Random) -> List[Event]:
        """
        Evaluate world state. Return list of events this actor fires this week.
        Subclasses implement their specific IF/THEN behavior model.
        """
        return []

    def _make_event(self, etype: EventType, state: WorldState,
                    desc: str, leg_impacts: Dict = None,
                    magnitude: float = 1.0) -> Event:
        return Event(
            type=etype,
            week=state.week,
            date=state.date,
            source=self.name,
            description=desc,
            leg_impacts=leg_impacts or {},
            magnitude=magnitude,
        )

# ── Actor Implementations ─────────────────────────────────────────────────────

class JapanBOJ(AgentBase):
    """
    Two-Fire Trap: Cannot hike into contracting energy-shock economy.
    Cannot hold while yen bleeds toward 165+.
    Base case: hike June 16–17. 85% probability given hawkish split.
    """
    name = "Japan / BOJ"
    tier = 3

    # Stochastic parameters (sampled per run)
    def __init__(self, hike_week_offset: int = 0, hike_probability: float = 0.85):
        super().__init__()
        self.hike_week_offset  = hike_week_offset
        self.hike_probability  = hike_probability
        self.base_hike_week    = 1   # Week 1 ≈ June 16-17
        self.hike_fired        = False
        self.hike_week_actual  = None
        self.distributing_ust  = False
        self.unwind_fired      = False

    def decide(self, state: WorldState, rng: random.Random) -> List[Event]:
        events = []
        target_week = max(1, self.base_hike_week + self.hike_week_offset)

        # ── BOJ hike ──────────────────────────────────────────────────────────
        # Counterweight: if ceasefire and Brent drops sharply, BOJ may hold
        if not self.hike_fired and state.week == target_week:
            hold_prob = 0.0
            if state.ceasefire_active and state.brent_price < 95.0:
                hold_prob = 0.25   # Energy relief gives BOJ cover to hold
            effective_prob = self.hike_probability * (1.0 - hold_prob)
            if rng.random() < effective_prob:
                self.hike_fired       = True
                self.hike_week_actual = state.week
                events.append(self._make_event(
                    EventType.BOJ_HIKE, state,
                    "BOJ hikes to 1.0% — first hike since 1995. 6-3 hawkish split. "
                    "No reassurance option: supply-side inflation prevents pivot. "
                    "Counterweight unavailable — Aug 2024 containment script is gone.",
                    {"leg_2": 0.02}
                ))

        # ── Carry unwind fires 1–2 weeks after hike ───────────────────────────
        # This is the missing link: hike → unwind → VIX spike → flash crash
        # Counterweight: coordinated G7 FX intervention (low probability, not modeled as active)
        if self.hike_fired and not self.unwind_fired:
            weeks_since_hike = state.week - self.hike_week_actual
            # Unwind probability ramps up over 2 weeks: 70% wk1, 90% wk2
            unwind_prob = 0.70 if weeks_since_hike == 1 else 0.90
            if weeks_since_hike >= 1 and rng.random() < unwind_prob:
                self.unwind_fired = True
                events.append(self._make_event(
                    EventType.CARRY_UNWIND_BEGINS, state,
                    f"Carry unwind begins Week {state.week} (+{weeks_since_hike}wk post-hike). "
                    f"$4–20T notional JPY-funded positions unwinding simultaneously. "
                    f"1.67× Aug 2024 scale. No BOJ reassurance floor.",
                    magnitude=1.67
                ))

        # ── UST distribution (same week as unwind) ────────────────────────────
        if self.hike_fired and not self.distributing_ust and self.unwind_fired:
            self.distributing_ust = True
            events.append(self._make_event(
                EventType.TIC_OFFICIAL_SELLING, state,
                "Japan distributing USTs to fund yen repatriation. "
                "$1.24T position — abrupt reversal from +$113B 12-month accumulation.",
                {"leg_2": 0.03}
            ))

        # ── USD/JPY break → algorithmic stop-losses ───────────────────────────
        if state.boj_hiked and state.usd_jpy < 155.0 and not state.carry_unwind_active:
            events.append(self._make_event(
                EventType.USD_JPY_BREAKS_155, state,
                f"USD/JPY breaks 155 ({state.usd_jpy:.1f}). Algorithmic stop-losses trigger. "
                "Second wave of selling begins.",
            ))

        # ── Flash crash: fires once VIX breaches 40 during unwind ────────────
        if state.carry_unwind_active and not state.flash_crash_occurred:
            if state.vix > 40.0 or state.usd_jpy < 150.0:
                events.append(self._make_event(
                    EventType.FLASH_CRASH, state,
                    f"Flash crash. VIX {state.vix:.0f}. USD/JPY {state.usd_jpy:.1f}. "
                    f"S&P breaks 6,200. 10Y +50bp. No containment floor.",
                    magnitude=min(2.0, max(1.0, state.vix / 35.0))
                ))

        return events


class FederalReserve(AgentBase):
    """
    Reactive stabilizer. All tools are demand-side; supply shock is not curable.
    Emergency cut = patch, not fix.
    """
    name = "Federal Reserve"
    tier = 4

    def __init__(self):
        super().__init__()
        self.cut_fired = False
        self.emergency_facilities_active = False

    def decide(self, state: WorldState, rng: random.Random) -> List[Event]:
        events = []

        # Emergency cut threshold: VIX > 60 or flash crash confirmed
        if not self.cut_fired and (state.vix > 58.0 or state.flash_crash_occurred):
            if rng.random() < 0.80:  # 80% probability of emergency cut
                self.cut_fired = True
                events.append(self._make_event(
                    EventType.FED_EMERGENCY_CUT, state,
                    "Fed emergency inter-meeting cut. Patch, not fix. "
                    "5-8% equity bounce expected. Supply shock unchanged.",
                ))

        return events


class USTreasury(AgentBase):
    """
    Managing UST demand collapse while funding war + deficit.
    GENIUS Act as structural demand replacement.
    """
    name = "US Treasury"
    tier = 4

    def __init__(self, genius_act_week: int = 8):
        super().__init__()
        self.genius_act_week = genius_act_week  # Stochastic: 6-16 weeks out

    def decide(self, state: WorldState, rng: random.Random) -> List[Event]:
        events = []

        # One-shot at designated week — not a retry loop
        if not state.genius_act_signed and state.week == self.genius_act_week:
            if rng.random() < 0.68:
                events.append(self._make_event(
                    EventType.GENIUS_ACT_SIGNED, state,
                    "GENIUS Act signed. Stablecoin issuers required to hold UST. "
                    "$144B+ stablecoin complex becomes captive UST buyer.",
                    {"leg_4": 0.15}
                ))

        return events


class IranIRGC(AgentBase):
    """
    Dual utility structure. Civilian government ≠ IRGC.
    PGSA is the primary instrument. IRGC acts autonomously.
    """
    name = "Iran / IRGC"
    tier = 1

    def __init__(self, mine_probability_per_week: float = 0.025):
        super().__init__()
        self.mine_prob = mine_probability_per_week
        self.negotiating = False
        self.ceasefire_attempt = 0

    def decide(self, state: WorldState, rng: random.Random) -> List[Event]:
        events = []

        # ── IRGC mine deployment ───────────────────────────────────────────────
        # KEY COUNTERWEIGHT: PGSA toll revenue gives Iran income WITHOUT full closure.
        # Full closure eliminates toll revenue — economically self-defeating.
        # IRGC mines only if: US/Israel escalation forces it, OR revenue collapses entirely.
        if not state.irgc_mines_deployed and state.hormuz_status != HormuzStatus.CLOSED:
            mine_prob = (WeightedProb(self.mine_prob)
                # Escalation weights
                .escalate_if(state.israel_struck,          4.5,  "Israeli strike — IRGC retaliatory mandate")
                .escalate_if(state.brent_price < 80.0,     2.5,  "Revenue floor breach — fiscal desperation")
                .escalate_if(state.flash_crash_occurred,   1.5,  "US financial crisis = reduced deterrence capacity")
                .escalate_if(state.credit_cascade,         1.3,  "Western financial stress = reduced response capacity")
                # Counterweights
                .suppress_if(state.pgsa_active,            0.30, "PGSA toll revenue — full closure is self-defeating")
                .suppress_if(state.ceasefire_active,       0.15, "Ceasefire: civilian government restraining IRGC")
                .suppress_if(not state.israel_struck
                             and state.week < 6,           0.50, "Early weeks: US carrier deterrence at max")
                .cap(0.20)
            )
            if mine_prob.roll(rng):
                events.append(self._make_event(
                    EventType.IRGC_MINE_DEPLOYMENT, state,
                    "IRGC deploys mines in Hormuz. Physical closure — overrides PGSA toll model. "
                    "Autonomous IRGC decision; civilian government not consulted.",
                    {"leg_1": 0.005, "leg_8": 0.08}
                ))

        # Civilian government ceasefire interest
        if state.brent_price > 130.0 and not self.negotiating:
            self.negotiating = True

        return events


class IsraelNode(AgentBase):
    """
    Unilateral actor. Will exceed US authorization to prevent nuclear restart.
    South Pars strike is the confirmed behavioral baseline.
    """
    name = "Israel"
    tier = 1

    def __init__(self, unilateral_prob_per_week: float = 0.010):
        super().__init__()
        self.unilateral_prob = unilateral_prob_per_week

    def decide(self, state: WorldState, rng: random.Random) -> List[Event]:
        events = []

        if not state.israel_struck:
            strike_prob = (WeightedProb(self.unilateral_prob)
                # Escalation weights
                .escalate_if(state.irgc_mines_deployed,    3.5,  "Physical Hormuz closure = existential threat")
                .escalate_if(state.week > 12,              1.8,  "Extended conflict window — Netanyahu coalition pressure")
                .escalate_if(state.bab_al_mandab_closed,   2.0,  "Dual chokepoint — escalation ladder maxed")
                # Counterweights
                .suppress_if(state.ceasefire_active,       0.20, "Ceasefire window — US restraint pressure on Israel")
                .suppress_if(state.flash_crash_occurred,   0.50, "Flash crash — US focused on financial crisis; Israel restraint")
                .suppress_if(state.week <= 3,              0.40, "Early weeks — US carrier deterrence at maximum")
                .cap(0.12)
            )
            if strike_prob.roll(rng):
                events.append(self._make_event(
                    EventType.ISRAEL_UNILATERAL, state,
                    "Israel strikes Iranian nuclear/missile infrastructure without US authorization. "
                    "South Pars behavioral precedent confirmed. US forced to follow.",
                    {"leg_1": 0.005, "leg_6": 0.10}
                ))

        return events


class HouthisAxisOfResistance(AgentBase):
    """
    Partially autonomous. Houthis can act independently of Iran civilian ceasefire.
    Bab al-Mandab is the primary tail risk.
    """
    name = "Axis of Resistance / Houthis"
    tier = 1

    def __init__(self, bab_prob_per_week: float = 0.018):
        super().__init__()
        self.bab_prob = bab_prob_per_week

    def decide(self, state: WorldState, rng: random.Random) -> List[Event]:
        events = []

        # ── Escalation ladder constraint ───────────────────────────────────────
        # Bab al-Mandab physical closure requires prior Red Sea harassment campaign.
        # Cannot jump straight to closure without prior escalation buildup (weeks 0-4).
        # This is the escalation ladder — each step requires prior steps.
        if not state.bab_al_mandab_closed:
            bab_prob = (WeightedProb(self.bab_prob)
                # Escalation weights
                .escalate_if(state.irgc_mines_deployed,    3.5,  "IRGC authorized dual chokepoint")
                .escalate_if(state.israel_struck,          2.5,  "Israeli strike — Houthi activation authorized")
                .escalate_if(state.week > 8,               1.6,  "Extended conflict — Houthi operational tempo high")
                # Counterweights
                .suppress_if(state.week < 2
                             and not state.bab_al_mandab_declared, 0.15,
                             "Escalation ladder: too early, no prior campaign")
                # Jun 8: declared blockade removes early-week escalation ladder constraint
                .suppress_if(state.ceasefire_active,       0.30, "Ceasefire — Houthis may continue but IRGC pressure to pause")
                .suppress_if(state.hormuz_status == HormuzStatus.PARTIAL, 0.25,
                                                           "Scenario C active — dual chokepoint would kill the deal")
                .suppress_if(not state.irgc_mines_deployed
                             and not state.israel_struck,  0.45, "No IRGC authorization yet — Houthis acting alone")
                .cap(0.15)
            )
            if bab_prob.roll(rng):
                events.append(self._make_event(
                    EventType.BAB_AL_MANDAB, state,
                    "Houthis confirm Bab al-Mandab physical closure (AIS confirmed). "
                    "Dual chokepoint. Scenario E analog. $200+ Brent trajectory.",
                    {"leg_8": 0.10, "leg_1": 0.005}
                ))

        # ── Yanbu strike — tail risk, requires Bab al-Mandab as prerequisite ──
        if not state.yanbu_struck:
            yanbu_prob = (WeightedProb(0.005)
                .escalate_if(state.bab_al_mandab_closed,   4.0,  "Dual chokepoint campaign — Yanbu is next")
                .suppress_if(not state.bab_al_mandab_closed, 0.10, "Yanbu requires prior Bab closure — escalation ladder")
                .suppress_if(state.ceasefire_active,       0.10, "Ceasefire window")
                .cap(0.08)
            )
            if yanbu_prob.roll(rng):
                events.append(self._make_event(
                    EventType.YANBU_STRIKE, state,
                    "Houthi strike on Yanbu refinery complex. Saudi export bypass eliminated. "
                    "Scenario E locked. $200+ Brent.",
                    {"leg_1": 0.01}
                ))

        return events


class QatarMediator(AgentBase):
    """
    Only Western-aligned actor with direct Tehran back-channel.
    Primary Scenario C pathway. Commercial interest aligned with mediation.
    """
    name = "Qatar"
    tier = 2

    def __init__(self, mediation_prob_base: float = 0.06):
        super().__init__()
        self.mediation_prob = mediation_prob_base
        self.attempted = False

    def decide(self, state: WorldState, rng: random.Random) -> List[Event]:
        events = []

        # Mediation probability increases with energy price (revenue pressure → urgency)
        prob = self.mediation_prob
        if state.brent_price > 125.0:
            prob *= 1.8
        if state.ceasefire_active:
            prob *= 0.3  # Already in negotiation
        if state.irgc_mines_deployed or state.bab_al_mandab_closed:
            prob *= 0.2  # Escalation kills mediation window

        if not state.ceasefire_active and rng.random() < prob:
            events.append(self._make_event(
                EventType.CEASEFIRE_SIGNAL, state,
                "Qatar activates Tehran back-channel. FM meeting confirmed. "
                "Scenario C pathway opens. Temporary Leg 1 softening expected.",
                {"leg_1": -0.05}
            ))

        # Ceasefire progresses to partial Hormuz open (~30% once ceasefire active for 2+ weeks)
        if state.ceasefire_active and state.week >= 3:
            if state.hormuz_status == HormuzStatus.TOLL and rng.random() < 0.10:
                events.append(self._make_event(
                    EventType.HORMUZ_PARTIAL_OPEN, state,
                    "Hormuz partial open under Qatar-brokered deal. "
                    "PGSA tolls remain — not binary open/close. Scenario C active.",
                    {"leg_1": -0.08}
                ))

        # Ceasefire collapse probability — IRGC autonomous action
        if state.ceasefire_active and rng.random() < 0.22:
            events.append(self._make_event(
                EventType.CEASEFIRE_COLLAPSE, state,
                "Ceasefire collapses. IRGC autonomous action torpedoed negotiation. "
                "Pattern documented: each extension correlated with yield pressure.",
            ))

        return events


class SaudiArabia(AgentBase):
    """
    GCC anchor. Dollar peg is the Leg 2 nuclear option.
    Peg review language = Leg 2 → 90%+.
    """
    name = "Saudi Arabia"
    tier = 2

    def __init__(self):
        super().__init__()

    def decide(self, state: WorldState, rng: random.Random) -> List[Event]:
        events = []

        # Peg review: triggered by severe US security guarantee degradation
        peg_prob = (WeightedProb(0.008)
            .escalate_if(state.flash_crash_occurred,        3.5,  "Dollar confidence crisis during carry unwind")
            .escalate_if(state.tic_selling_confirmed,       2.0,  "Official UST selling confirmed at scale")
            .escalate_if(state.brent_price > 150.0,         1.5,  "Extreme energy prices stress US security guarantee")
            .suppress_if(not state.flash_crash_occurred,    0.30, "Pre-crash: dollar still dominant reserve")
            .suppress_if(state.ceasefire_active,            0.40, "Ceasefire reduces urgency of peg review")
            .cap(0.06)
        ).compute()

        if not state.saudi_peg_review and rng.random() < peg_prob:  # type: ignore
            events.append(self._make_event(
                EventType.SAUDI_PEG_REVIEW, state,
                "Saudi Arabia signals dollar peg review. Any form of review language = "
                "Leg 2 → 90%+. GCC periphery watches for follow-on.",
                {"leg_2": 0.12}
            ))

        return events


class PrivateCreditComplex(AgentBase):
    """
    Apollo/Barings gating confirmed. Third fund = cascade signal.
    Leg 3 at 67% — systemic stress confirmed.
    """
    name = "Private Credit Complex"
    tier = 4

    def __init__(self, gate_prob_per_week: float = 0.08):
        super().__init__()
        self.gate_prob = gate_prob_per_week
        self.gates_fired = 2   # Apollo + Barings already confirmed

    def decide(self, state: WorldState, rng: random.Random) -> List[Event]:
        events = []

        if not state.credit_cascade:
            gate_prob = (WeightedProb(self.gate_prob)
                # Escalation weights
                .escalate_if(state.flash_crash_occurred,   4.5,  "Flash crash — redemptions surge simultaneously")
                .escalate_if(state.brent_price > 130.0,    2.0,  "Energy sector leveraged loan defaults")
                .escalate_if(state.vix > 45.0,             2.5,  "VIX spike — liquidity withdrawal across NBFI")
                .escalate_if(state.boj_hiked,              1.5,  "BOJ hike — Japanese exposure in private credit")
                # Counterweights
                .suppress_if(state.fed_cut,                0.45, "Fed emergency cut: temporary redemption pause")
                .suppress_if(not state.flash_crash_occurred
                             and state.week < 5,           0.40, "Pre-flash crash: stress building but not cascade")
                .suppress_if(state.fed_cut,                0.45, "Fed Section 13(3) backstop: temporary redemption pause")
                .suppress_if(state.week < 8,             0.50, "Early weeks: shadow banking stress building, not cascade")
                .cap(0.05)   # max 5%/week even post-crash
            )
            if gate_prob.roll(rng):
                self.gates_fired += 1
                if self.gates_fired >= 3:
                    events.append(self._make_event(
                        EventType.CREDIT_CASCADE, state,
                        f"Fund #{self.gates_fired} gates — cascade threshold crossed. "
                        "Leg 3 systemic: liquidity mismatch → credit freeze.",
                        {"leg_3": 0.10}
                    ))
                else:
                    events.append(self._make_event(
                        EventType.PRIVATE_CREDIT_GATE, state,
                        f"Private credit fund #{self.gates_fired} gates. "
                        f"Apollo + Barings confirmed; #{self.gates_fired} approaches cascade threshold.",
                        {"leg_3": 0.05}
                    ))

        return events


class StablecoinComplex(AgentBase):
    """
    Tether de-peg is the tail risk. GENIUS Act is the structural fix.
    """
    name = "Stablecoin / Settlement Rails"
    tier = 4

    def __init__(self, depeg_prob_per_week: float = 0.006):
        super().__init__()
        self.depeg_prob = depeg_prob_per_week

    def decide(self, state: WorldState, rng: random.Random) -> List[Event]:
        events = []

        if not state.tether_depegged:
            depeg_prob = (WeightedProb(self.depeg_prob)
                # Escalation weights — Tether is a flash crash amplifier, not a base case
                .escalate_if(state.flash_crash_occurred,   8.0,  "Flash crash — exchange solvency stress; mass redemptions")
                .escalate_if(state.credit_cascade,         4.0,  "Credit cascade — capital flight to regulated alternatives")
                .escalate_if(state.vix > 55.0,             3.0,  "Extreme VIX — crypto market correlation spike")
                # Counterweights
                .suppress_if(state.genius_act_signed,        0.40, "GENIUS Act: regulatory pressure forces reserve audit")
                .suppress_if(not state.flash_crash_occurred, 0.12, "No flash crash: base case is stable")
                .cap(0.025)  # max 2.5%/week even post-crash
            )
            if depeg_prob.roll(rng):
                events.append(self._make_event(
                    EventType.TETHER_DEPEG, state,
                    "Tether de-pegs under redemption pressure. Exchange solvency stress. "
                    "$144B complex becomes emergency UST seller. XRP demand spike.",
                    {"leg_4": 0.10}
                ))

        return events


class SPRMechanism(AgentBase):
    """
    Not a decision actor — a physical mechanism.
    Draw rate decelerating due to cavern pressure degradation.
    Functional floor (380 mmbbl) already breached.
    Heel floor (330 mmbbl) ~July 1.
    """
    name = "SPR Mechanism"
    tier = 4

    def decide(self, state: WorldState, rng: random.Random) -> List[Event]:
        events = []

        # SPR draws down each week (degrading rate)
        if state.spr_mmbbl > 273.0:
            draw_rate = _spr_draw_rate(state.spr_mmbbl)
            state.spr_mmbbl = max(273.0, state.spr_mmbbl - draw_rate)

        # Heel floor hit
        if state.spr_mmbbl <= 330.0 and not state.spr_floor_hit:
            events.append(self._make_event(
                EventType.SPR_FLOOR_HIT, state,
                f"SPR reaches heel-corrected floor ({state.spr_mmbbl:.1f} mmbbl ≤ 330). "
                "Last price suppression mechanism expires. No policy buffer remains.",
            ))

        return events


def _spr_draw_rate(spr_mmbbl: float) -> float:
    """
    SPR draw rate degrades as cavern pressure drops.
    Degradation curve calibrated from EIA WCSSTUS1 data.
    Peak: 9.9 mmbbl/wk at 374 mmbbl → declining slope toward heel.
    """
    if spr_mmbbl > 380.0:
        return 8.5    # Pre-degradation: ~1.2 mb/d
    elif spr_mmbbl > 350.0:
        return 7.5    # Mild degradation
    elif spr_mmbbl > 330.0:
        return 5.0    # Heavy degradation
    else:
        return 1.5    # Near-heel: essentially exhausted


class FoodFertilizerMechanism(AgentBase):
    """
    QAFCO force majeure: 14% global seaborne urea offline.
    Fertilizer shock not yet in 2026 USDA estimates — 2027 crop year is the catalyst.
    """
    name = "Food / Fertilizer (Leg 5)"
    tier = 4

    def decide(self, state: WorldState, rng: random.Random) -> List[Event]:
        events = []

        # IRC Phase 5 famine declaration (probabilistic)
        if not state.famine_declared and state.week >= 12:
            if rng.random() < 0.05:
                events.append(self._make_event(
                    EventType.IRC_PHASE5_FAMINE, state,
                    "IRC declares Phase 5 famine in named import-dependent countries. "
                    "Leg 5 → critical. CF/NTR urea premium confirmed.",
                    {"leg_5": 0.08}
                ))

        return events


# ── Simulation Engine ─────────────────────────────────────────────────────────

class SimulationEngine:
    """
    Orchestrates the God's Eye simulation.
    Each week: actors decide → events fire → coupling engine propagates → state updates.
    """

    def __init__(self, agents: List[AgentBase], rng: random.Random):
        self.agents  = agents
        self.rng     = rng
        self.coupling = CouplingEngine()

    def run(self, initial_state: WorldState) -> List[WorldState]:
        """
        Run simulation from initial_state for WEEKLY_STEPS weeks.
        Returns list of weekly WorldState snapshots.
        """
        history = [initial_state]
        state   = initial_state.clone()

        for step in range(1, WEEKLY_STEPS + 1):
            state.week = step
            state.date = SIM_START + timedelta(weeks=step)
            state.events_this_week = []

            # Collect all agent decisions
            all_events: List[Event] = []
            for agent in self.agents:
                events = agent.decide(state, self.rng)
                all_events.extend(events)

            # Sort by event priority (kinetic > financial > policy)
            all_events.sort(key=lambda e: _event_priority(e.type))

            # Propagate through coupling engine
            for event in all_events:
                state = CouplingEngine.propagate(event, state)

            # Natural Brent drift (supply destruction permanent)
            _update_macro(state, self.rng)

            # Clamp all leg scores
            for k in state.leg_scores:
                state.leg_scores[k] = min(1.0, max(0.0, state.leg_scores[k]))

            _normalize_scenarios(state)

            history.append(state.clone())

        return history


def _event_priority(etype: EventType) -> int:
    """Lower number = higher priority (processed first)."""
    kinetic  = {EventType.YANBU_STRIKE, EventType.BAB_AL_MANDAB,
                EventType.ISRAEL_UNILATERAL, EventType.IRGC_MINE_DEPLOYMENT}
    monetary = {EventType.BOJ_HIKE, EventType.FLASH_CRASH, EventType.CARRY_UNWIND_BEGINS,
                EventType.USD_JPY_BREAKS_155, EventType.CREDIT_CASCADE}
    if etype in kinetic:   return 0
    if etype in monetary:  return 1
    return 2


def _update_macro(state: WorldState, rng: random.Random):
    """Weekly macro variable drift.

    P-035: mean-reversion TARGETS and speeds are framework theses (hand-set,
    unchanged); NOISE scales come from CALIB (fitted on full FRED history) with
    a calibrated 2-state vol regime. Prev hand-set noise was ~1.8x too small on
    Brent/VIX and ~4.5x too small on USD/JPY vs 1971-2026 history.
    """
    # ── Vol regime transition (calibrated: stationary frac + mean duration) ──
    reg = CALIB["SDE_DCOILBRENTEU_REGIME"]
    frac = max(0.01, min(0.9, reg["mu"]))
    dur_wk = max(1.0, reg.get("weight", 16.0) / 7.0)   # weight = duration in days
    p_exit = 1.0 / dur_wk
    p_enter = p_exit * frac / (1.0 - frac)
    if state.vol_regime_hi:
        if rng.random() < p_exit:  state.vol_regime_hi = False
    else:
        if rng.random() < p_enter: state.vol_regime_hi = True
    # Stress states force the high-vol regime (framework coupling, not data)
    if (state.flash_crash_occurred or state.hormuz_status == HormuzStatus.CLOSED
            or state.bab_al_mandab_closed or state.yanbu_struck):
        state.vol_regime_hi = True

    def _sig(series: str) -> float:
        base, hi = CALIB[series]["sigma"], CALIB[series + "_REGIME"]["sigma"]
        return _wk(hi if state.vol_regime_hi else base)

    # Brent: structural floor from supply destruction; mean-reversion toward $115-125
    target = 120.0
    if state.hormuz_status == HormuzStatus.CLOSED:   target = 165.0
    if state.bab_al_mandab_closed:                    target = 175.0
    if state.yanbu_struck:                            target = 210.0
    if state.ceasefire_active:                        target = 98.0
    if state.hormuz_status == HormuzStatus.PARTIAL:   target = 98.0

    noise = state.brent_price * rng.gauss(0, _sig("SDE_DCOILBRENTEU"))
    state.brent_price += (target - state.brent_price) * 0.08 + noise
    state.brent_price = max(55.0, min(250.0, state.brent_price))

    # VIX mean-reversion (calibrated log-noise scale)
    vix_target = 18.0 if not state.flash_crash_occurred else 35.0
    state.vix   += (vix_target - state.vix) * 0.12 + state.vix * rng.gauss(0, _sig("SDE_VIXCLS"))
    state.vix    = max(12.0, min(90.0, state.vix))

    # USD/JPY: structural yen appreciation after BOJ hike (drift = framework thesis;
    # noise scale = calibrated)
    jpy_noise = state.usd_jpy * rng.gauss(0, _sig("SDE_DEXJPUS"))
    if state.boj_hiked:
        state.usd_jpy += -0.3 + jpy_noise
    else:
        state.usd_jpy += 0.1 + jpy_noise
    state.usd_jpy = max(130.0, min(170.0, state.usd_jpy))

    # 10Y yield: structural climb post-flash crash
    yield_target = 5.50 if state.flash_crash_occurred else 4.80
    state.us_10y_yield += (yield_target - state.us_10y_yield) * 0.05 + rng.gauss(0, 0.08)
    state.us_10y_yield = max(3.0, min(9.0, state.us_10y_yield))


# ── Monte Carlo Runner ────────────────────────────────────────────────────────

@dataclass
class MonteCarloResults:
    n_runs:       int
    n_weeks:      int
    # Scenario probability bands [week][scenario] -> {mean, p5, p25, p75, p95}
    scenario_bands: Dict
    # Leg score bands [week][leg] -> {mean, p5, p95}
    leg_bands:      Dict
    # Macro bands [week] -> {brent_mean, vix_mean, usd_jpy_mean, yield_mean}
    macro_bands:    Dict
    # Event frequency [event_type] -> fraction of runs in which it occurred
    event_frequency: Dict[str, float]
    # Flash crash timing distribution
    flash_crash_timing: Dict
    # Final scenario distribution (week 29)
    final_scenario_dist: Dict[str, float]
    # Event pair correlation matrix (Pearson, co-occurrence rates)
    event_correlations: Dict


class MonteCarloRunner:

    def __init__(self, n_runs: int = DEFAULT_RUNS, seed: Optional[int] = None):
        self.n_runs = n_runs
        self.seed   = seed

    def run(self) -> MonteCarloResults:
        """Run n_runs independent simulations. Aggregate into probability bands."""

        all_histories: List[List[WorldState]] = []

        for run_idx in range(self.n_runs):
            rng = random.Random(self.seed + run_idx if self.seed else None)
            agents, initial_state = self._build_run(rng)
            engine  = SimulationEngine(agents, rng)
            history = engine.run(initial_state)
            all_histories.append(history)

        self._last_histories = all_histories  # stored for regime clustering

        return self._aggregate(all_histories)

    def _build_run(self, rng: random.Random) -> Tuple[List[AgentBase], WorldState]:
        """
        Build agents and initial state for a single run.
        Stochastic parameters are sampled here.
        """
        agents = [
            JapanBOJ(
                hike_week_offset = rng.randint(-1, 2),          # BOJ timing: week 0–3 (Jun 9 – Jun 29)
                hike_probability = rng.gauss(0.85, 0.07),       # 85% ± 7%
            ),
            FederalReserve(),
            USTreasury(
                genius_act_week = rng.randint(6, 20),            # GENIUS Act: 6-20 weeks out
            ),
            IranIRGC(
                mine_probability_per_week = rng.uniform(0.015, 0.040),
            ),
            IsraelNode(
                unilateral_prob_per_week = rng.uniform(0.008, 0.022),
            ),
            HouthisAxisOfResistance(
                # Jun 8: Declared blockade operative — base elevated from latent to active campaign
                bab_prob_per_week = rng.uniform(0.020, 0.045),
            ),
            QatarMediator(
                mediation_prob_base = rng.uniform(0.04, 0.09),
            ),
            SaudiArabia(),
            PrivateCreditComplex(
                gate_prob_per_week = rng.uniform(0.05, 0.12),
            ),
            StablecoinComplex(
                depeg_prob_per_week = rng.uniform(0.008, 0.025),
            ),
            SPRMechanism(),
            FoodFertilizerMechanism(),
        ]

        # Initial world state (confirmed data as of June 8, 2026)
        state = WorldState(week=0, date=SIM_START)

        # Add small noise to initial macro variables
        state.brent_price  += rng.gauss(0, 2.5)
        state.usd_jpy      += rng.gauss(0, 1.0)
        state.vix          += rng.gauss(0, 1.5)
        state.spr_mmbbl    += rng.gauss(0, 1.0)

        return agents, state

    def _aggregate(self, all_histories: List[List[WorldState]]) -> MonteCarloResults:
        """Aggregate N simulation runs into probability bands."""
        n_weeks = WEEKLY_STEPS + 1

        # Build per-week arrays
        scenario_data: Dict[str, List[List[float]]] = {s: [[] for _ in range(n_weeks)] for s in "ABCDE"}
        leg_data:      Dict[str, List[List[float]]] = {l: [[] for _ in range(n_weeks)]
                                                        for l in ["leg_1","leg_2","leg_3","leg_4","leg_5",
                                                                  "leg_6","leg_7","leg_8","leg_9","cross"]}
        macro_data: Dict[str, List[List[float]]] = {
            k: [[] for _ in range(n_weeks)]
            for k in ["brent_price","vix","usd_jpy","us_10y_yield","spr_mmbbl"]
        }

        # Key events for correlation matrix
        CORR_EVENTS = [
            "BOJ_HIKE", "FLASH_CRASH", "BAB_AL_MANDAB",
            "IRGC_MINE_DEPLOYMENT", "ISRAEL_UNILATERAL", "CEASEFIRE_SIGNAL",
            "CREDIT_CASCADE", "TETHER_DEPEG", "SAUDI_PEG_REVIEW",
            "GENIUS_ACT_SIGNED", "YANBU_STRIKE", "SPR_FLOOR_HIT",
        ]
        event_counts: Dict[str, int] = {}
        flash_crash_weeks: List[int] = []
        # Per-run event occurrence: corr_matrix[run_idx][event] = 0/1
        per_run_events: List[Dict[str, int]] = []

        for history in all_histories:
            flash_week = None
            run_event_types = set()

            for ws in history:
                w = ws.week
                for s in "ABCDE":
                    scenario_data[s][w].append(ws.scenario_probs.get(s, 0))
                for leg in leg_data:
                    leg_data[leg][w].append(ws.leg_scores.get(leg, 0))
                for key in macro_data:
                    macro_data[key][w].append(getattr(ws, key, 0))

                for event in ws.events_this_week:
                    run_event_types.add(event.type.value)
                    if event.type == EventType.FLASH_CRASH and flash_week is None:
                        flash_week = w

            if flash_week is not None:
                flash_crash_weeks.append(flash_week)
            for et in run_event_types:
                event_counts[et] = event_counts.get(et, 0) + 1
            per_run_events.append({evt: (1 if evt in run_event_types else 0) for evt in CORR_EVENTS})

        def bands(data_list: List[float]) -> Dict:
            if not data_list:
                return {"mean": 0, "p5": 0, "p25": 0, "p75": 0, "p95": 0}
            s = sorted(data_list)
            n = len(s)
            return {
                "mean": sum(s) / n,
                "p5":   s[max(0, int(n * 0.05))],
                "p25":  s[max(0, int(n * 0.25))],
                "p75":  s[min(n-1, int(n * 0.75))],
                "p95":  s[min(n-1, int(n * 0.95))],
            }

        scenario_bands = {
            s: [bands(scenario_data[s][w]) for w in range(n_weeks)]
            for s in "ABCDE"
        }
        leg_bands = {
            l: [bands(leg_data[l][w]) for w in range(n_weeks)]
            for l in leg_data
        }
        macro_bands = [
            {k: bands(macro_data[k][w])["mean"] for k in macro_data}
            for w in range(n_weeks)
        ]

        event_frequency = {k: v / self.n_runs for k, v in event_counts.items()}

        flash_timing = {}
        if flash_crash_weeks:
            flash_timing = {
                "probability": len(flash_crash_weeks) / self.n_runs,
                "mean_week":   sum(flash_crash_weeks) / len(flash_crash_weeks),
                "distribution": {str(w): flash_crash_weeks.count(w) / self.n_runs
                                 for w in range(n_weeks)},
            }

        final = {s: scenario_data[s][-1] for s in "ABCDE"}
        final_dist = {s: sum(final[s]) / len(final[s]) if final[s] else 0 for s in "ABCDE"}

        # ── Event correlation matrix ──────────────────────────────────────────
        # Pearson correlation of per-run binary occurrence vectors
        # co_occur[A][B] = fraction of runs where both A and B fired
        def pearson_corr(xa: List[float], xb: List[float]) -> float:
            n = len(xa)
            if n < 2: return 0.0
            ma = sum(xa) / n
            mb = sum(xb) / n
            num = sum((xa[i] - ma) * (xb[i] - mb) for i in range(n))
            da  = sum((v - ma) ** 2 for v in xa) ** 0.5
            db  = sum((v - mb) ** 2 for v in xb) ** 0.5
            return num / (da * db) if da > 0 and db > 0 else 0.0

        event_vectors = {evt: [r[evt] for r in per_run_events] for evt in CORR_EVENTS}
        event_correlations: Dict = {
            "events":   CORR_EVENTS,
            "matrix":   {},
            "co_occur": {},
        }
        for ea in CORR_EVENTS:
            event_correlations["matrix"][ea] = {}
            event_correlations["co_occur"][ea] = {}
            for eb in CORR_EVENTS:
                if ea == eb:
                    event_correlations["matrix"][ea][eb] = 1.0
                    event_correlations["co_occur"][ea][eb] = event_frequency.get(ea, 0.0)
                else:
                    corr = pearson_corr(event_vectors[ea], event_vectors[eb])
                    event_correlations["matrix"][ea][eb] = round(corr, 4)
                    n_both = sum(event_vectors[ea][i] * event_vectors[eb][i]
                                 for i in range(self.n_runs))
                    event_correlations["co_occur"][ea][eb] = round(n_both / self.n_runs, 4)

        return MonteCarloResults(
            n_runs=self.n_runs,
            n_weeks=n_weeks,
            scenario_bands=scenario_bands,
            leg_bands=leg_bands,
            macro_bands=macro_bands,
            event_frequency=event_frequency,
            flash_crash_timing=flash_timing,
            final_scenario_dist=final_dist,
            event_correlations=event_correlations,
        )

# ── Output formatting ─────────────────────────────────────────────────────────

def results_to_dict(results: MonteCarloResults) -> Dict:
    """Serialize results to JSON-compatible dict for React demo consumption."""
    dates = [(SIM_START + timedelta(weeks=w)).isoformat() for w in range(results.n_weeks)]

    return {
        "meta": {
            "n_runs":     results.n_runs,
            "n_weeks":    results.n_weeks,
            "start_date": SIM_START.isoformat(),
            "end_date":   SIM_END.isoformat(),
            "framework":  "God's Eye v4.1 — Full Convergence Strategic Intelligence",
            "composite_init": 0.91,
        },
        "dates":              dates,
        "scenario_bands":     results.scenario_bands,
        "leg_bands":          results.leg_bands,
        "macro_bands":        results.macro_bands,
        "event_frequency":    results.event_frequency,
        "flash_crash_timing": results.flash_crash_timing,
        "final_scenario_dist":results.final_scenario_dist,
        "scenario_labels": {
            "A": "Strike / Zero Restraint",
            "B": "Back Down / Duration (Flash Crash)",
            "C": "Back Channel Deal",
            "D": "IRGC Mine Deployment",
            "E": "Dual Chokepoint ($200+ Brent)",
        },
        "leg_labels": {
            "leg_1": "War / Energy Chokepoints",
            "leg_2": "GCC / Petrodollar Strain",
            "leg_3": "Private Credit / NBFI",
            "leg_4": "Rails / XRP / Stablecoin",
            "leg_5": "Food / Fertilizer",
            "leg_6": "Munitions / MIC",
            "leg_7": "Semiconductor / Taiwan",
            "leg_8": "Maritime / Insurance",
            "leg_9": "AI / Labor",
            "cross": "Cross-Cutting JPY Carry",
        },
        "event_correlations": results.event_correlations,
    }


def print_summary(results: MonteCarloResults):
    """Print human-readable summary to stdout."""
    print("\n" + "═" * 62)
    print("  GOD'S EYE — MONTE CARLO SIMULATION RESULTS")
    print(f"  {results.n_runs:,} runs | {results.n_weeks} weeks | {SIM_START} → {SIM_END}")
    print("═" * 62)

    print("\n  FINAL SCENARIO DISTRIBUTION (Dec 31, 2026)")
    print("  " + "─" * 58)
    for scenario, prob in sorted(results.final_scenario_dist.items(),
                                 key=lambda x: -x[1]):
        bar = "█" * int(prob * 40)
        labels = {
            "A": "Strike / Zero Restraint      ",
            "B": "Back Down / Duration          ",
            "C": "Back Channel Deal             ",
            "D": "IRGC Mine Deployment          ",
            "E": "Dual Chokepoint ($200+ Brent) ",
        }
        print(f"  {scenario}  {labels[scenario]}  {prob:.1%}  {bar}")

    print("\n  FLASH CRASH")
    print("  " + "─" * 58)
    fc = results.flash_crash_timing
    if fc:
        mean_date = SIM_START + timedelta(weeks=fc.get("mean_week", 0))
        print(f"  Probability:    {fc['probability']:.1%}")
        print(f"  Mean trigger:   Week {fc['mean_week']:.1f} (~{mean_date.strftime('%b %d')})")
    else:
        print("  No flash crash events in simulation.")

    print("\n  KEY EVENT FREQUENCIES")
    print("  " + "─" * 58)
    key_events = [
        ("BOJ_HIKE",             "BOJ hike to 1.0%         "),
        ("FLASH_CRASH",          "Flash crash               "),
        ("BAB_AL_MANDAB",        "Bab al-Mandab closure     "),
        ("IRGC_MINE_DEPLOYMENT", "IRGC mine deployment      "),
        ("ISRAEL_UNILATERAL",    "Israel unilateral strike  "),
        ("CEASEFIRE_SIGNAL",     "Ceasefire signal          "),
        ("HORMUZ_PARTIAL_OPEN",  "Hormuz partial open       "),
        ("GENIUS_ACT_SIGNED",    "GENIUS Act signed         "),
        ("CREDIT_CASCADE",       "Private credit cascade    "),
        ("TETHER_DEPEG",         "Tether de-peg             "),
        ("SPR_FLOOR_HIT",        "SPR heel floor hit        "),
        ("SAUDI_PEG_REVIEW",     "Saudi peg review          "),
    ]
    for key, label in key_events:
        freq = results.event_frequency.get(key, 0)
        bar  = "█" * int(freq * 30)
        print(f"  {label}  {freq:.1%}  {bar}")

    # Event correlation matrix
    ec = results.event_correlations
    if ec:
        events = ec.get("events", [])
        short = {
            "BOJ_HIKE": "BOJ",
            "FLASH_CRASH": "CRASH",
            "BAB_AL_MANDAB": "BAB",
            "IRGC_MINE_DEPLOYMENT": "MINES",
            "ISRAEL_UNILATERAL": "ISRAEL",
            "CEASEFIRE_SIGNAL": "CFIRE",
            "CREDIT_CASCADE": "CREDIT",
            "TETHER_DEPEG": "TETHER",
            "SAUDI_PEG_REVIEW": "SAUDI",
            "GENIUS_ACT_SIGNED": "GENIUS",
            "YANBU_STRIKE": "YANBU",
            "SPR_FLOOR_HIT": "SPR",
        }
        hdr = "  " + " " * 8 + "".join(f"{short.get(e,e[:5]):>7}" for e in events)
        print("\n  EVENT CORRELATION MATRIX (Pearson)")
        print("  " + "─" * 58)
        print(hdr)
        mx = ec.get("matrix", {})
        for ea in events:
            row = f"  {short.get(ea,ea[:6]):<8}"
            for eb in events:
                v = mx.get(ea, {}).get(eb, 0)
                if ea == eb:
                    row += "   1.00"
                elif v > 0.5:
                    row += f"  \033[31m{v:>5.2f}\033[0m"
                elif v < -0.3:
                    row += f"  \033[34m{v:>5.2f}\033[0m"
                else:
                    row += f"  {v:>5.2f}"
            print(row)

        # Top correlated pairs
        pairs = []
        for i, ea in enumerate(events):
            for eb in events[i+1:]:
                v = mx.get(ea, {}).get(eb, 0)
                pairs.append((abs(v), v, ea, eb))
        pairs.sort(reverse=True)
        print(f"\n  TOP CORRELATED PAIRS:")
        for _, v, ea, eb in pairs[:5]:
            co = ec.get("co_occur", {}).get(ea, {}).get(eb, 0)
            sign = "+" if v > 0 else ""
            print(f"    {short.get(ea,ea):<8} ↔ {short.get(eb,eb):<8}  r={sign}{v:.3f}  co-occur={co:.0%}")

    print("\n" + "═" * 62 + "\n")


# ── CLI entry point ───────────────────────────────────────────────────────────

# ── GMM Regime Discovery ──────────────────────────────────────────────────────

class RegimeClusterer:
    """
    Discovers emergent outcome regimes from Monte Carlo simulation runs using
    Gaussian Mixture Models (soft probabilistic clustering) on run feature vectors.

    Upgrade from K-Means v1.0:
      - GMM assigns soft probabilities (not hard labels) — a run can be 60% Regime 1 / 40% Regime 2
      - BIC (Bayesian Information Criterion) selects n_components (penalizes overfitting)
      - Covariance ellipses in PCA space show regime overlap and uncertainty
      - Regime uncertainty score: mean entropy of run-level responsibility vectors

    Analytical value: The A-E scenario taxonomy is analyst-imposed.
    This layer asks: what does the SIMULATION think the natural outcome regimes are?
    Convergence between taxonomy and clusters validates the framework.
    Divergence reveals hidden regimes the taxonomy missed.
    Soft assignments reveal ambiguous runs where multiple futures are plausible.

    Feature vector per run (dim = weeks × features):
      - Final leg scores (9 legs + cross)
      - Final macro variables (Brent, VIX, USD/JPY, 10Y yield, SPR)
      - Event flag booleans (12 key events)
      - Final scenario probability distribution (5 values)
    """

    def __init__(self, k_range: range = range(2, 8)):
        self.k_range = k_range
        self.feature_names: List[str] = []

    def build_feature_matrix(self, all_histories: List[List[WorldState]]) -> "np.ndarray":
        """Convert each simulation run into a fixed-length feature vector."""
        try:
            import numpy as np
        except ImportError:
            raise ImportError("numpy required for regime clustering: pip install numpy")

        vectors = []
        for history in all_histories:
            final = history[-1]
            mid   = history[len(history)//2] if len(history) > 1 else history[-1]

            vec = []

            # Final leg scores
            for leg in ["leg_1","leg_2","leg_3","leg_4","leg_5",
                        "leg_6","leg_7","leg_8","leg_9","cross"]:
                vec.append(final.leg_scores.get(leg, 0.0))

            # Final macro variables (normalized)
            vec.append(final.brent_price / 200.0)
            vec.append(final.vix / 90.0)
            vec.append((final.usd_jpy - 130.0) / 40.0)
            vec.append(final.us_10y_yield / 9.0)
            vec.append(final.spr_mmbbl / 415.0)

            # Event flags (0/1)
            vec.append(float(final.boj_hiked))
            vec.append(float(final.flash_crash_occurred))
            vec.append(float(final.bab_al_mandab_closed))
            vec.append(float(final.irgc_mines_deployed))
            vec.append(float(final.israel_struck))
            vec.append(float(final.ceasefire_active))
            vec.append(float(final.credit_cascade))
            vec.append(float(final.tether_depegged))
            vec.append(float(final.spr_floor_hit))
            vec.append(float(final.saudi_peg_review))
            vec.append(float(final.genius_act_signed))
            vec.append(float(final.yanbu_struck))

            # Final scenario probabilities
            for s in "ABCDE":
                vec.append(final.scenario_probs.get(s, 0.0))

            # Mid-simulation state (captures path, not just endpoint)
            vec.append(mid.brent_price / 200.0)
            vec.append(mid.vix / 90.0)
            vec.append(float(mid.flash_crash_occurred))

            vectors.append(vec)

        if not self.feature_names:
            self.feature_names = (
                [f"leg_{i}" for i in range(1, 10)] + ["cross"] +
                ["brent_f","vix_f","usd_jpy_f","yield_f","spr_f"] +
                ["boj_hiked","flash_crash","bab_closed","irgc_mines","israel_struck",
                 "ceasefire","credit_cascade","tether","spr_floor","saudi_peg",
                 "genius_act","yanbu"] +
                [f"p_{s}" for s in "ABCDE"] +
                ["brent_mid","vix_mid","crash_mid"]
            )

        return np.array(vectors, dtype=float)

    def find_optimal_components(self, X_scaled: "np.ndarray") -> Tuple[int, Dict]:
        """
        BIC (Bayesian Information Criterion) model selection for GMM.
        Lower BIC = better model (fewer parameters for same fit quality).
        Also computes silhouette score for hard-assignment comparison.
        Returns: (best_n_components, {k: bic_score}, {k: silhouette_score})
        """
        try:
            from sklearn.mixture import GaussianMixture
            from sklearn.metrics import silhouette_score
            import numpy as np
        except ImportError:
            raise ImportError("scikit-learn required: pip install scikit-learn")

        bic_scores = {}
        sil_scores = {}
        for k in self.k_range:
            gmm = GaussianMixture(n_components=k, covariance_type="full",
                                  random_state=42, n_init=3, max_iter=200)
            gmm.fit(X_scaled)
            bic_scores[k] = gmm.bic(X_scaled)
            labels = gmm.predict(X_scaled)
            if len(set(labels)) > 1:
                sil_scores[k] = silhouette_score(X_scaled, labels)
            else:
                sil_scores[k] = -1.0

        best_k = min(bic_scores, key=bic_scores.get)
        return best_k, bic_scores, sil_scores

    # Keep legacy name for compatibility
    def find_optimal_k(self, X: "np.ndarray") -> Tuple[int, Dict]:
        from sklearn.preprocessing import StandardScaler
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)
        best_k, bic_scores, sil_scores = self.find_optimal_components(X_scaled)
        return best_k, sil_scores

    def cluster(self, all_histories: List[List[WorldState]],
                k: Optional[int] = None) -> Dict:
        """
        Run GMM regime discovery. Returns soft cluster probabilities + regime profiles.

        Key outputs vs K-Means v1.0:
          - responsibilities: N×K matrix of soft cluster membership probabilities
          - regime_uncertainty: mean entropy of per-run responsibility vectors (0=certain, 1=max ambiguity)
          - bic_by_k: BIC curve for model selection validation
          - covariance_ellipses: 2D PCA covariance ellipse params for visualization
        """
        try:
            from sklearn.mixture import GaussianMixture
            from sklearn.preprocessing import StandardScaler
            from sklearn.decomposition import PCA
            from sklearn.metrics import silhouette_score
            import numpy as np
        except ImportError:
            raise ImportError("scikit-learn required: pip install scikit-learn")

        X = self.build_feature_matrix(all_histories)
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)

        # Model selection: BIC criterion
        bic_scores = {}
        sil_scores = {}
        if k is None:
            k, bic_scores, sil_scores = self.find_optimal_components(X_scaled)

        # Fit final GMM
        gmm = GaussianMixture(n_components=k, covariance_type="full",
                              random_state=42, n_init=5, max_iter=300)
        gmm.fit(X_scaled)

        # Hard labels and soft responsibilities
        labels        = gmm.predict(X_scaled)
        responsibilities = gmm.predict_proba(X_scaled)   # N × K

        # Silhouette on hard labels
        sil = silhouette_score(X_scaled, labels) if len(set(labels)) > 1 else 0.0

        # Regime uncertainty: entropy of per-run responsibility vector
        eps = 1e-10
        entropies = -np.sum(responsibilities * np.log(responsibilities + eps), axis=1)
        max_entropy = np.log(k)
        uncertainty = float(np.mean(entropies) / max_entropy) if max_entropy > 0 else 0.0

        # PCA for 2D visualization
        pca = PCA(n_components=2)
        X_2d = pca.fit_transform(X_scaled)

        # Compute 2D covariance ellipses for each GMM component
        # Project full-dim GMM means/covars into 2D PCA space
        covariance_ellipses = []
        pca_components = pca.components_  # shape (2, n_features)
        for comp_idx in range(k):
            mean_full = gmm.means_[comp_idx]          # (n_features,)
            cov_full  = gmm.covariances_[comp_idx]    # (n_features, n_features)
            mean_2d   = pca_components @ mean_full
            cov_2d    = pca_components @ cov_full @ pca_components.T
            # Eigendecomposition for ellipse axes
            eigvals, eigvecs = np.linalg.eigh(cov_2d)
            eigvals = np.maximum(eigvals, 0)
            angle = float(np.degrees(np.arctan2(eigvecs[1, 1], eigvecs[0, 1])))
            covariance_ellipses.append({
                "cx":    float(mean_2d[0]),
                "cy":    float(mean_2d[1]),
                "rx":    float(2.0 * np.sqrt(eigvals[1])),  # 2-sigma
                "ry":    float(2.0 * np.sqrt(eigvals[0])),
                "angle": angle,
                "weight": float(gmm.weights_[comp_idx]),
            })

        # Build regime profiles (using hard assignments for interpretability)
        regimes = {}
        for cluster_id in range(k):
            mask = labels == cluster_id
            cluster_runs = [all_histories[i][-1] for i in range(len(labels)) if mask[i]]
            n = int(sum(mask))
            if n == 0:
                continue

            avg_legs = {}
            for leg in ["leg_1","leg_2","leg_3","leg_4","leg_5",
                        "leg_6","leg_7","leg_8","leg_9"]:
                avg_legs[leg] = float(np.mean([r.leg_scores.get(leg,0) for r in cluster_runs]))

            event_rates = {
                "flash_crash":   float(np.mean([r.flash_crash_occurred  for r in cluster_runs])),
                "bab_closed":    float(np.mean([r.bab_al_mandab_closed  for r in cluster_runs])),
                "irgc_mines":    float(np.mean([r.irgc_mines_deployed   for r in cluster_runs])),
                "ceasefire":     float(np.mean([r.ceasefire_active       for r in cluster_runs])),
                "israel_struck": float(np.mean([r.israel_struck          for r in cluster_runs])),
                "credit_cascade":float(np.mean([r.credit_cascade         for r in cluster_runs])),
                "saudi_peg":     float(np.mean([r.saudi_peg_review       for r in cluster_runs])),
            }

            avg_macro = {
                "brent":   float(np.mean([r.brent_price    for r in cluster_runs])),
                "vix":     float(np.mean([r.vix            for r in cluster_runs])),
                "usd_jpy": float(np.mean([r.usd_jpy        for r in cluster_runs])),
                "yield":   float(np.mean([r.us_10y_yield   for r in cluster_runs])),
            }

            # Dominant analyst scenario
            scenario_votes = {s: 0 for s in "ABCDE"}
            for r in cluster_runs:
                dom = max(r.scenario_probs, key=r.scenario_probs.get)
                scenario_votes[dom] += 1
            dominant_scenario = max(scenario_votes, key=scenario_votes.get)

            # Mean soft membership for this cluster
            mean_responsibility = float(np.mean(responsibilities[mask, cluster_id]))

            label = _auto_label_regime(event_rates, avg_macro, avg_legs)

            regimes[cluster_id] = {
                "n_runs":              n,
                "pct_of_total":        float(n / len(labels)),
                "gmm_weight":          float(gmm.weights_[cluster_id]),
                "mean_responsibility": mean_responsibility,
                "dominant_scenario":   dominant_scenario,
                "auto_label":          label,
                "avg_legs":            avg_legs,
                "event_rates":         event_rates,
                "avg_macro":           avg_macro,
            }

        # PCA coords with soft responsibilities
        pca_coords = []
        for i in range(len(labels)):
            pca_coords.append({
                "x":       float(X_2d[i, 0]),
                "y":       float(X_2d[i, 1]),
                "cluster": int(labels[i]),
                "probs":   [float(p) for p in responsibilities[i]],
            })

        return {
            "k":                    k,
            "method":               "gmm",
            "bic_by_k":             {str(k_): float(v) for k_, v in bic_scores.items()},
            "silhouette_score":     float(sil),
            "silhouette_by_k":      {str(k_): float(v) for k_, v in sil_scores.items()},
            "regime_uncertainty":   uncertainty,
            "pca_variance":         float(pca.explained_variance_ratio_.sum()),
            "covariance_ellipses":  covariance_ellipses,
            "regimes":              regimes,
            "pca_coords":           pca_coords,
            "labels":               labels.tolist(),
        }


def _auto_label_regime(event_rates: Dict, macro: Dict, legs: Dict) -> str:
    """Heuristic regime labeling from cluster centroid characteristics."""
    if macro["brent"] > 180:
        return "Dual Chokepoint / Extreme Supply Shock"
    if event_rates["flash_crash"] > 0.7 and event_rates["irgc_mines"] < 0.3:
        return "Financial Cascade / BOJ Flash Crash"
    if event_rates["ceasefire"] > 0.6 and event_rates["flash_crash"] < 0.4:
        return "Diplomatic Resolution / Scenario C"
    if event_rates["irgc_mines"] > 0.5 and not event_rates["bab_closed"] > 0.5:
        return "Physical Hormuz Closure / Scenario D"
    if event_rates["israel_struck"] > 0.5 and event_rates["irgc_mines"] > 0.4:
        return "Full Kinetic Escalation / Scenario A"
    if event_rates["flash_crash"] > 0.5 and event_rates["credit_cascade"] > 0.5:
        return "Synchronized Financial + Energy Crisis"
    if event_rates["saudi_peg"] > 0.4 and legs.get("leg_2", 0) > 0.93:
        return "Petrodollar Architecture Breakdown"
    return "Prolonged Duration / Mixed Escalation"


def run_sensitivity_analysis(n_runs: int = 300, seed: int = 42) -> Dict:
    """
    Vary each key parameter ±20% from baseline, one at a time.
    Measure d(scenario_prob)/d(param) for each scenario.

    Parameters swept:
      - boj_hike_prob: baseline 0.85
      - houthi_escalation: baseline 1.0 (multiplier on bab_prob_per_week)
      - ceasefire_suppression: baseline 1.0 (multiplier on ceasefire prob)
      - mine_prob_per_week: baseline midpoint 0.0275
      - credit_gate_prob: baseline midpoint 0.085

    Returns tornado chart data: parameter → {+20%: Δscenario_probs, -20%: Δscenario_probs}
    """
    import copy

    # Parameter definitions: (name, attr_path_or_callable, baseline, delta_frac)
    PARAMS = [
        ("boj_hike_prob",       "JapanBOJ.hike_probability",   0.85, 0.20),
        ("houthi_bab_prob",     "HouthisAxisOfResistance.bab_prob_per_week_mid", 0.0325, 0.30),
        ("ceasefire_base",      "QatarMediator.mediation_prob_base_mid", 0.065, 0.25),
        ("mine_prob",           "IranIRGC.mine_probability_mid", 0.0275, 0.30),
        ("credit_gate_prob",    "PrivateCreditComplex.gate_prob_mid", 0.085, 0.25),
        ("initial_brent",       "WorldState.brent_price",       100.0, 0.15),
        ("initial_vix",         "WorldState.vix",               22.0,  0.25),
    ]

    print("\n  SENSITIVITY ANALYSIS")
    print("  " + "─" * 72)
    print(f"  Baseline: {n_runs} runs · seed {seed}")
    print(f"  {'Parameter':<28} {'Δ':<4} {'A':>6} {'B':>6} {'C':>6} {'D':>6} {'E':>6} {'CRASH':>7}")
    print("  " + "─" * 72)

    def run_with_patch(patch_fn, n=n_runs, s=seed):
        """Run simulation with a monkey-patch applied to _build_run."""
        original_build = MonteCarloRunner._build_run

        def patched_build(self, rng):
            agents, state = original_build(self, rng)
            patch_fn(agents, state, rng)
            return agents, state

        MonteCarloRunner._build_run = patched_build
        try:
            runner = MonteCarloRunner(n_runs=n, seed=s)
            results = runner.run()
        finally:
            MonteCarloRunner._build_run = original_build
        dist = results.final_scenario_dist
        fc = results.flash_crash_timing.get("probability", 0.0)
        return dist, fc

    # Baseline
    baseline_dist, baseline_fc = run_with_patch(lambda a, s, r: None)

    sensitivity_data = {}

    for param_name, path, baseline_val, delta_frac in PARAMS:
        deltas = {"+": 1.0 + delta_frac, "-": 1.0 - delta_frac}
        sensitivity_data[param_name] = {"baseline": baseline_val}

        for sign, multiplier in deltas.items():
            new_val = baseline_val * multiplier

            # Build patch function based on which parameter we're varying
            def make_patch(pname, nv):
                def patch(agents, state, rng):
                    for agent in agents:
                        if pname == "boj_hike_prob" and isinstance(agent, JapanBOJ):
                            agent.hike_probability = max(0, min(1, nv))
                        elif pname == "houthi_bab_prob" and isinstance(agent, HouthisAxisOfResistance):
                            agent.bab_prob_per_week = max(0, nv)
                        elif pname == "ceasefire_base" and isinstance(agent, QatarMediator):
                            agent.mediation_prob_base = max(0, nv)
                        elif pname == "mine_prob" and isinstance(agent, IranIRGC):
                            agent.mine_probability_per_week = max(0, nv)
                        elif pname == "credit_gate_prob" and isinstance(agent, PrivateCreditComplex):
                            agent.gate_prob_per_week = max(0, nv)
                        elif pname == "initial_brent":
                            state.brent_price = nv
                        elif pname == "initial_vix":
                            state.vix = nv
                return patch

            dist, fc = run_with_patch(make_patch(param_name, new_val), n=n_runs, s=seed)

            delta_str = f"{sign}{int(delta_frac*100)}%"
            row = f"  {param_name:<28} {delta_str:<4}"
            for scen in "ABCDE":
                d = dist[scen] - baseline_dist[scen]
                color = ""
                if abs(d) > 0.03:
                    color = "\033[31m" if d > 0 else "\033[34m"
                    reset = "\033[0m"
                else:
                    color = reset = ""
                row += f"  {color}{d:+.3f}{reset}"
            d_fc = fc - baseline_fc
            fc_color = "\033[31m" if d_fc > 0.05 else "\033[34m" if d_fc < -0.05 else ""
            fc_reset = "\033[0m" if fc_color else ""
            row += f"  {fc_color}{d_fc:+.3f}{fc_reset}"
            print(row)

            sensitivity_data[param_name][sign] = {
                "value": new_val,
                "delta_dist": {s: dist[s] - baseline_dist[s] for s in "ABCDE"},
                "delta_fc": fc - baseline_fc,
            }

        print()  # blank line between params

    # Tornado summary: rank parameters by impact on flash crash
    print("  TORNADO — IMPACT ON FLASH CRASH PROBABILITY")
    print("  " + "─" * 60)
    tornado = []
    for pname, data in sensitivity_data.items():
        if "+" in data and "-" in data:
            swing = data["+"]["delta_fc"] - data["-"]["delta_fc"]
            tornado.append((abs(swing), swing, pname,
                            data["+"]["delta_fc"], data["-"]["delta_fc"]))
    tornado.sort(reverse=True)
    for _, swing, pname, dpos, dneg in tornado:
        bar_pos = "█" * int(abs(dpos) * 80)
        bar_neg = "█" * int(abs(dneg) * 80)
        print(f"  {pname:<28}  +chg: {dpos:+.3f} {bar_pos}")
        print(f"  {'':<28}  -chg: {dneg:+.3f} {bar_neg}")

    print("\n  BASELINE DISTRIBUTION:")
    for s in "ABCDE":
        print(f"    {s}: {baseline_dist[s]:.1%}", end="  ")
    print(f"\n    Flash crash: {baseline_fc:.1%}")
    print()

    return sensitivity_data


def main():
    parser = argparse.ArgumentParser(
        description="God's Eye Agent-Based Simulation Engine v1.0"
    )
    parser.add_argument("--simulations", type=int, default=DEFAULT_RUNS,
                        help=f"Number of Monte Carlo runs (default: {DEFAULT_RUNS})")
    parser.add_argument("--output", type=str, default=None,
                        help="Save results JSON to this path (for React demo)")
    parser.add_argument("--seed", type=int, default=None,
                        help="Random seed for reproducibility")
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress summary output")
    parser.add_argument("--sensitivity", action="store_true",
                        help="Run sensitivity analysis (sweeps key params ±20-30%)")
    args = parser.parse_args()

    if not args.quiet:
        print(f"\n  Initializing God's Eye simulation engine...")
        print(f"  {args.simulations:,} Monte Carlo runs × {WEEKLY_STEPS} weekly steps")
        print(f"  Horizon: {SIM_START} → {SIM_END}")
        print(f"  Actors: 12 agents | 7 coupling rules | 5 scenarios")
    load_calibrated_priors(quiet=args.quiet)
    if not args.quiet:
        print()

    runner  = MonteCarloRunner(n_runs=args.simulations, seed=args.seed)
    results = runner.run()

    if not args.quiet:
        print_summary(results)

    # Run regime clustering if scikit-learn available
    cluster_results = None
    try:
        if not args.quiet:
            print(f"  Running GMM regime discovery (BIC model selection)...")
        clusterer = RegimeClusterer(k_range=range(2, 7))
        cluster_results = clusterer.cluster(runner._last_histories)

        if not args.quiet:
            k_found   = cluster_results['k']
            sil_score = cluster_results['silhouette_score']
            uncert    = cluster_results['regime_uncertainty']
            print(f"\n  REGIME DISCOVERY — GMM (K={k_found}, "
                  f"silhouette={sil_score:.3f}, uncertainty={uncert:.3f})")
            bic = cluster_results.get('bic_by_k', {})
            if bic:
                bic_str = "  BIC by K: " + " | ".join(
                    f"K={k_}:{v:.0f}" for k_, v in sorted(bic.items(), key=lambda x: int(x[0]))
                )
                print(bic_str)
            print(f"  {'─'*58}")
            for cid, regime in cluster_results["regimes"].items():
                resp = regime.get("mean_responsibility", 0)
                print(f"  Regime {cid+1}: {regime['auto_label']}")
                print(f"           {regime['pct_of_total']:.1%} of runs | "
                      f"GMM weight {regime['gmm_weight']:.2f} | "
                      f"Mean responsibility {resp:.2f}")
                print(f"           Brent ${regime['avg_macro']['brent']:.0f} | "
                      f"VIX {regime['avg_macro']['vix']:.0f} | "
                      f"Flash crash {regime['event_rates']['flash_crash']:.0%}")
            print()
    except (ImportError, Exception) as e:
        if not args.quiet:
            print(f"  Regime clustering skipped: {e}")

    if args.output:
        data = results_to_dict(results)
        if cluster_results:
            data["regime_clusters"] = cluster_results
        with open(args.output, "w") as f:
            json.dump(data, f, indent=2)
        print(f"  Results saved → {args.output}")

    if args.sensitivity:
        run_sensitivity_analysis(n_runs=min(args.simulations, 300), seed=args.seed or 42)

    return results


if __name__ == "__main__":
    main()
