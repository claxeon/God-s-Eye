#!/usr/bin/env python3
"""
God's Eye — Shared library for Modules 1-9 (Treasury plumbing, Japan channels,
dollar funding, refined products, fertilizer clock, country vulnerability,
policy response, claims/falsification engine, dashboard outputs).
=====================================================================

Added 2026-08-18 at user request, to extend the existing framework's causal
resolution (distinguish normal repricing from impaired market function,
separate independent transmission mechanisms, expose timing/procurement
constraints, make claims falsifiable) WITHOUT touching existing scoring.

This file holds ONLY shared infrastructure used by module{1..9}_*.py:
  - Signal-state / confidence-tier vocabulary (matches schema_v2's
    'confirmed'/'inferred'/'speculative' text values in leg_components.confidence,
    extended here with the WATCH/ELEVATED/CRITICAL/DATA_QUALITY_FAILURE states
    the new modules need and the old leg pipeline does not).
  - A ModuleResult / Observable dataclass pair every module returns, so
    module9_dashboard_outputs.py can aggregate them generically.
  - A ProviderInterface ABC + an UnavailableProvider stub, per the spec's
    requirement that any paid/authenticated/unscrapable source be represented
    by an interface and a documented fallback rather than a fabricated number.
  - Calculation helpers with unit tests in tests/test_godseye_modules.py:
    bps conversion, matched-contract crack spread (with the data-quality gate),
    rolling z-score, stale-data detection, generic threshold->signal-state
    transition.

Does NOT modify state_vector_compute.py, gods_eye_engine.py, or any existing
leg score. Modules that want to feed a new observable into the existing L(t)
pipeline should write to `leg_components` with a new series_id and let a human
decide whether/how to fold it into a leg weight — same discipline the vault's
TIC/IRGC notes used (document + recommend, never silently reweight).

Run: not directly executable. Imported by module{1..9}_*.py.
"""

from __future__ import annotations

import json
import os
import statistics
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_DIR = SCRIPT_DIR / "config"

# Same Supabase project + publishable (anon, non-secret) key already used by
# yen_mechanics.py / carry_mechanics.py / market_mechanics.py — reused here,
# not duplicated as a new credential.
SUPA_URL = "https://snykuqyceqpplnzmyksp.supabase.co"
SUPA_KEY = "sb_publishable_TJg65x5w56CulOEdWFJNyQ_89loJtit"


# ─────────────────────────────────────────────────────────────────────────────
# Vocabulary
# ─────────────────────────────────────────────────────────────────────────────

class SignalState(str, Enum):
    NORMAL = "NORMAL"
    WATCH = "WATCH"
    ELEVATED = "ELEVATED"
    CRITICAL = "CRITICAL"
    DATA_QUALITY_FAILURE = "DATA_QUALITY_FAILURE"


# Ordering used for "does this breach exceed that one" comparisons.
_SIGNAL_ORDER = [
    SignalState.NORMAL,
    SignalState.WATCH,
    SignalState.ELEVATED,
    SignalState.CRITICAL,
]


class ConfidenceTier(str, Enum):
    CONFIRMED = "CONFIRMED"
    INFERRED = "INFERRED"
    SPECULATIVE = "SPECULATIVE"

    def to_legacy(self) -> str:
        """Map to the lowercase values schema_v2's leg_components.confidence expects."""
        return self.value.lower()


class ClaimStatus(str, Enum):
    ACTIVE = "ACTIVE"
    WEAKENING = "WEAKENING"
    FALSIFIED = "FALSIFIED"
    RESOLVED = "RESOLVED"
    DATA_INSUFFICIENT = "DATA_INSUFFICIENT"


# ─────────────────────────────────────────────────────────────────────────────
# Provider interface — every live data pull goes through one of these so a
# missing/paid/authenticated source is explicit, never silently fabricated.
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ProviderResult:
    value: Optional[float]
    unit: Optional[str]
    source: str
    obs_date: Optional[str]           # ISO date string, or None if unknown
    confidence: ConfidenceTier
    is_fallback: bool = False
    note: str = ""

    @property
    def is_unknown(self) -> bool:
        return self.value is None


class ProviderInterface(ABC):
    """Every module data source implements this. `name` and `requires` are
    metadata surfaced in the dashboard's Data Freshness panel."""

    name: str = "unnamed_provider"
    requires: str = ""  # e.g. "free (EIA API key)", "paid (Bloomberg terminal)"

    @abstractmethod
    def fetch(self, **kwargs) -> ProviderResult:
        ...


class UnavailableProvider(ProviderInterface):
    """Documented fallback for any track item that needs a paid/authenticated
    feed this deployment doesn't have (MOVE index, bid-to-cover history,
    repo fails, FX risk reversals, FIMA repo usage, India tender detail, etc.).
    Returns value=None ("unknown") rather than inventing a number, satisfying
    engineering requirement #5/#6 in the module spec."""

    def __init__(self, name: str, reason: str, requires: str = "paid/authenticated feed"):
        self.name = name
        self.reason = reason
        self.requires = requires

    def fetch(self, **kwargs) -> ProviderResult:
        return ProviderResult(
            value=None,
            unit=None,
            source=self.name,
            obs_date=None,
            confidence=ConfidenceTier.SPECULATIVE,
            is_fallback=True,
            note=f"UNKNOWN — {self.reason} (requires: {self.requires})",
        )


# ─────────────────────────────────────────────────────────────────────────────
# Observable / ModuleResult — the common return shape for modules 1-8,
# consumed generically by module9_dashboard_outputs.py.
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Observable:
    name: str
    value: Optional[float]
    unit: Optional[str]
    source: str
    obs_date: Optional[str]
    confidence: ConfidenceTier
    is_stale: bool = False
    is_unknown: bool = False
    note: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["confidence"] = self.confidence.value
        return d


@dataclass
class ModuleResult:
    module_id: str                 # e.g. "M1_treasury_plumbing"
    purpose: str
    computed_at: str               # ISO timestamp
    signal_state: SignalState
    confidence: ConfidenceTier
    metrics: dict = field(default_factory=dict)
    observables: list = field(default_factory=list)   # list[Observable]
    falsifiers: list = field(default_factory=list)     # list[str]
    triggered_falsifiers: list = field(default_factory=list)  # list[str], subset that actually fired
    data_quality_notes: list = field(default_factory=list)
    missing_data: list = field(default_factory=list)   # observable names that came back unknown
    source_freshness: dict = field(default_factory=dict)  # {source: days_stale}

    def to_dict(self) -> dict:
        return {
            "module_id": self.module_id,
            "purpose": self.purpose,
            "computed_at": self.computed_at,
            "signal_state": self.signal_state.value,
            "confidence": self.confidence.value,
            "metrics": self.metrics,
            "observables": [o.to_dict() for o in self.observables],
            "falsifiers": self.falsifiers,
            "triggered_falsifiers": self.triggered_falsifiers,
            "data_quality_notes": self.data_quality_notes,
            "missing_data": self.missing_data,
            "source_freshness": self.source_freshness,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)


# ─────────────────────────────────────────────────────────────────────────────
# Calculation helpers (unit-tested in tests/test_godseye_modules.py)
# ─────────────────────────────────────────────────────────────────────────────

def pct_to_bps(pct: float) -> float:
    """Percent (e.g. 0.5 for 0.5%) -> basis points (50)."""
    return pct * 100.0


def decimal_to_bps(x: float) -> float:
    """Decimal fraction (e.g. 0.005 for 0.5%) -> basis points (50)."""
    return x * 10_000.0


def bps_to_decimal(bps: float) -> float:
    return bps / 10_000.0


def rolling_zscore(series: list, window: Optional[int] = None) -> Optional[float]:
    """z-score of the LAST value of `series` against the mean/stdev of the
    preceding values (or the whole series if window is None / >= len).
    Returns None if fewer than 2 prior observations exist (can't estimate
    sigma) rather than raising or silently returning 0."""
    series = [x for x in series if x is not None]
    if len(series) < 3:
        return None
    if window is not None and window < len(series):
        hist = series[-(window + 1):-1]
        latest = series[-1]
    else:
        hist = series[:-1]
        latest = series[-1]
    if len(hist) < 2:
        return None
    mu = statistics.mean(hist)
    sigma = statistics.pstdev(hist) if len(hist) > 1 else 0.0
    if sigma == 0:
        return None
    return (latest - mu) / sigma


def is_stale(obs_date: Optional[date], max_age_days: int, as_of: Optional[date] = None) -> bool:
    """True if obs_date is missing or older than max_age_days relative to as_of
    (defaults to today). Missing data is treated as stale, never as fresh."""
    if obs_date is None:
        return True
    as_of = as_of or date.today()
    if isinstance(obs_date, str):
        obs_date = datetime.fromisoformat(obs_date).date()
    return (as_of - obs_date) > timedelta(days=max_age_days)


def matched_crack_spread(
    product_price: Optional[float],
    crude_price: Optional[float],
    product_unit: str,
    crude_unit: str,
    product_month: Optional[str],
    crude_month: Optional[str],
    product_is_futures: bool,
    crude_is_futures: bool,
    conversion_log: Optional[list] = None,
) -> dict:
    """
    Data-quality-gated crack spread calculation per Module 4's explicit rule:
    "Never calculate or alert on a crack spread unless contracts are in
    comparable units, delivery months are matched, currency basis is known,
    product quotes are clearly futures/wholesale/physical, and any conversion
    is logged. If conditions are not met, return DATA_QUALITY_FAILURE."

    product_price / crude_price are expected pre-converted to $/bbl by the
    caller, which must append every conversion step it performed to
    conversion_log (e.g. "$/gal -> $/bbl: x * 42"). This function's job is to
    ENFORCE the gate, not to guess unit conversions itself.

    Returns {"status": "OK", "crack": float, "log": [...]}
            or {"status": "DATA_QUALITY_FAILURE", "reason": str, "log": [...]}
    """
    conversion_log = list(conversion_log or [])
    reasons = []

    if product_price is None or crude_price is None:
        reasons.append("missing product or crude price")
    if product_unit != "$/bbl" or crude_unit != "$/bbl":
        reasons.append(
            f"units not both $/bbl (product={product_unit!r}, crude={crude_unit!r}) "
            "with no logged conversion to a common basis"
        )
    if product_month is None or crude_month is None or product_month != crude_month:
        reasons.append(
            f"delivery months not matched (product={product_month!r}, crude={crude_month!r})"
        )
    if not product_is_futures or not crude_is_futures:
        # Not a hard failure — physical/wholesale assessments are allowed by
        # the spec, but ONLY if both sides are the same kind of quote and that
        # is explicitly logged (mixing a futures print against a physical
        # assessment is exactly the "matched contract months and units" trap
        # this gate exists to catch).
        if product_is_futures != crude_is_futures:
            reasons.append(
                "mismatched quote types: one side is futures, the other is not "
                "(physical/futures mismatch, not a clean crack)"
            )
        else:
            conversion_log.append("both legs are physical/wholesale assessments, not futures — logged, not a failure on its own")

    if (product_unit == "$/bbl" and crude_unit == "$/bbl") and not conversion_log:
        reasons.append("no conversion log entries — unit match cannot be verified as deliberate vs. coincidental")

    if reasons:
        return {
            "status": SignalState.DATA_QUALITY_FAILURE.value,
            "reason": "; ".join(reasons),
            "log": conversion_log,
        }

    return {
        "status": "OK",
        "crack": product_price - crude_price,
        "log": conversion_log,
    }


def threshold_signal_state(breaches: dict) -> SignalState:
    """
    Generic escalation: given a dict of {condition_name: bool} already
    evaluated by the caller against its own module-specific thresholds, plus
    an optional 'critical' key for conditions that alone justify CRITICAL,
    return the worst state reached.

    Expected keys (all optional, default False):
      watch, elevated, critical, data_quality_failure
    A module computes these booleans itself from its config thresholds, then
    calls this to get a consistent SignalState back rather than hand-rolling
    if/elif ladders differently in every module.
    """
    if breaches.get("data_quality_failure"):
        return SignalState.DATA_QUALITY_FAILURE
    if breaches.get("critical"):
        return SignalState.CRITICAL
    if breaches.get("elevated"):
        return SignalState.ELEVATED
    if breaches.get("watch"):
        return SignalState.WATCH
    return SignalState.NORMAL


def worse_state(a: SignalState, b: SignalState) -> SignalState:
    """Combine two signal states, keeping the more severe one.
    DATA_QUALITY_FAILURE is treated as more severe than CRITICAL for
    reporting purposes (it means "we don't know," not "we know it's fine"),
    but callers should still surface it distinctly rather than conflating it
    with a real CRITICAL reading — see ModuleResult.signal_state vs.
    ModuleResult.data_quality_notes."""
    if SignalState.DATA_QUALITY_FAILURE in (a, b):
        return SignalState.DATA_QUALITY_FAILURE
    ia, ib = _SIGNAL_ORDER.index(a), _SIGNAL_ORDER.index(b)
    return a if ia >= ib else b


# ─────────────────────────────────────────────────────────────────────────────
# Config loading — thresholds live outside source code (config/*.json), per
# requirement #3 ("Threshold configuration outside source code") and #2
# ("Do not modify existing weights/thresholds without an explicit versioned
# configuration override" — these are NEW module configs, not edits to
# state_vector_compute.py's existing baselines).
# ─────────────────────────────────────────────────────────────────────────────

def load_config(module_name: str) -> dict:
    path = CONFIG_DIR / f"{module_name}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"Missing config for {module_name}: {path}. "
            "Every module's thresholds must live in config/, not hardcoded."
        )
    with open(path) as f:
        return json.load(f)


# ─────────────────────────────────────────────────────────────────────────────
# Best-effort Supabase write — mirrors the try/except-and-continue discipline
# already used in yen_mechanics.py / carry_mechanics.py, so a network or
# schema issue never blocks state_vector_daily.sh (every existing call in
# that script is already suffixed `|| true`).
# ─────────────────────────────────────────────────────────────────────────────

def supabase_upsert(table: str, rows: list, on_conflict: Optional[str] = None) -> bool:
    try:
        import requests  # already a hard dependency (requirements.txt)
    except ImportError:
        print(f"  [supabase] requests not installed, skipping write to {table}", file=sys.stderr)
        return False
    if not rows:
        return True
    url = f"{SUPA_URL}/rest/v1/{table}"
    if on_conflict:
        url += f"?on_conflict={on_conflict}"
    headers = {
        "apikey": SUPA_KEY,
        "Authorization": f"Bearer {SUPA_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates",
    }
    try:
        r = requests.post(url, headers=headers, json=rows, timeout=15)
        if r.status_code >= 300:
            print(f"  [supabase] {table} write failed ({r.status_code}): {r.text[:300]}", file=sys.stderr)
            return False
        return True
    except Exception as e:  # noqa: BLE001 — deliberately broad, this is best-effort telemetry
        print(f"  [supabase] {table} write raised: {e}", file=sys.stderr)
        return False


def now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"
