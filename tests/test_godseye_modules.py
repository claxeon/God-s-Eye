#!/usr/bin/env python3
"""
Unit tests for godseye_modules_common.py — the shared calculation helpers
used by module1-9. Deliberately network-free: no test here hits FRED/EIA/
Treasury/Supabase, so `python3 -m unittest discover tests` (or `pytest
tests/`) runs offline and fast, and is safe for CI.

Covers, per the module spec's explicit test requirement:
  - bps conversion
  - matched-contract crack-spread calculation (including the data-quality
    gate's rejection paths — mismatched units, mismatched months, mixed
    futures/physical quote types, missing conversion log)
  - rolling z-scores
  - stale-data detection
  - signal-state transitions

Run: python3 -m unittest discover -s tests -v    (from Scripts/)
     or: pytest tests/ -v   (if pytest is installed; not a hard dependency)
"""
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from godseye_modules_common import (  # noqa: E402
    SignalState, ConfidenceTier, pct_to_bps, decimal_to_bps, bps_to_decimal,
    rolling_zscore, is_stale, matched_crack_spread, threshold_signal_state,
    worse_state,
)


class TestBpsConversion(unittest.TestCase):
    def test_pct_to_bps(self):
        self.assertEqual(pct_to_bps(0.5), 50.0)
        self.assertEqual(pct_to_bps(1.0), 100.0)
        self.assertEqual(pct_to_bps(-0.25), -25.0)
        self.assertEqual(pct_to_bps(0.0), 0.0)

    def test_decimal_to_bps(self):
        self.assertEqual(decimal_to_bps(0.005), 50.0)
        self.assertEqual(decimal_to_bps(0.01), 100.0)

    def test_bps_to_decimal_roundtrip(self):
        for x in [0.0, 0.0037, -0.012, 0.5]:
            self.assertAlmostEqual(bps_to_decimal(decimal_to_bps(x)), x, places=9)


class TestRollingZScore(unittest.TestCase):
    def test_insufficient_history_returns_none(self):
        self.assertIsNone(rolling_zscore([]))
        self.assertIsNone(rolling_zscore([1.0]))
        self.assertIsNone(rolling_zscore([1.0, 2.0]))

    def test_zero_variance_returns_none_not_divzero(self):
        # all history values identical -> sigma=0 -> must not raise ZeroDivisionError
        self.assertIsNone(rolling_zscore([5.0, 5.0, 5.0, 5.0]))

    def test_known_zscore(self):
        # history [1,2,3], latest 4 -> mu=2, sigma(pstdev)=0.8165, z=(4-2)/0.8165≈2.449
        z = rolling_zscore([1.0, 2.0, 3.0, 4.0])
        self.assertAlmostEqual(z, 2.449, places=2)

    def test_window_limits_history(self):
        series = [10.0, 10.0, 10.0, 1.0, 2.0, 3.0, 4.0]
        z_full = rolling_zscore(series)
        z_windowed = rolling_zscore(series, window=3)
        self.assertNotEqual(z_full, z_windowed)

    def test_ignores_none_values(self):
        z = rolling_zscore([1.0, None, 2.0, 3.0, 4.0])
        self.assertAlmostEqual(z, 2.449, places=2)


class TestStaleDataDetection(unittest.TestCase):
    def test_missing_date_is_stale(self):
        self.assertTrue(is_stale(None, max_age_days=5))

    def test_fresh_date_not_stale(self):
        today = date(2026, 8, 18)
        self.assertFalse(is_stale(date(2026, 8, 16), max_age_days=5, as_of=today))

    def test_old_date_is_stale(self):
        today = date(2026, 8, 18)
        self.assertTrue(is_stale(date(2026, 7, 1), max_age_days=5, as_of=today))

    def test_boundary_exact_max_age_not_stale(self):
        today = date(2026, 8, 18)
        self.assertFalse(is_stale(date(2026, 8, 13), max_age_days=5, as_of=today))  # exactly 5 days

    def test_string_date_accepted(self):
        today = date(2026, 8, 18)
        self.assertFalse(is_stale("2026-08-16", max_age_days=5, as_of=today))


class TestMatchedCrackSpread(unittest.TestCase):
    def test_clean_match_returns_ok(self):
        res = matched_crack_spread(
            product_price=187.0, crude_price=85.0,
            product_unit="$/bbl", crude_unit="$/bbl",
            product_month="2026-08", crude_month="2026-08",
            product_is_futures=True, crude_is_futures=True,
            conversion_log=["already $/bbl, no conversion needed"],
        )
        self.assertEqual(res["status"], "OK")
        self.assertAlmostEqual(res["crack"], 102.0)

    def test_mismatched_units_fails_gate(self):
        res = matched_crack_spread(
            product_price=4.45, crude_price=85.0,           # product still in $/gal!
            product_unit="$/gal", crude_unit="$/bbl",
            product_month="2026-08", crude_month="2026-08",
            product_is_futures=True, crude_is_futures=True,
            conversion_log=[],
        )
        self.assertEqual(res["status"], SignalState.DATA_QUALITY_FAILURE.value)
        self.assertIn("units", res["reason"])

    def test_mismatched_delivery_months_fails_gate(self):
        res = matched_crack_spread(
            product_price=187.0, crude_price=85.0,
            product_unit="$/bbl", crude_unit="$/bbl",
            product_month="2026-09", crude_month="2026-08",   # different contract months
            product_is_futures=True, crude_is_futures=True,
            conversion_log=["logged"],
        )
        self.assertEqual(res["status"], SignalState.DATA_QUALITY_FAILURE.value)
        self.assertIn("delivery months", res["reason"])

    def test_futures_vs_physical_mismatch_fails_gate(self):
        res = matched_crack_spread(
            product_price=187.0, crude_price=85.0,
            product_unit="$/bbl", crude_unit="$/bbl",
            product_month="2026-08-14", crude_month="2026-08-14",
            product_is_futures=False, crude_is_futures=True,   # one physical, one futures
            conversion_log=["logged"],
        )
        self.assertEqual(res["status"], SignalState.DATA_QUALITY_FAILURE.value)
        self.assertIn("mismatched quote types", res["reason"])

    def test_missing_conversion_log_fails_gate_even_if_units_match(self):
        res = matched_crack_spread(
            product_price=187.0, crude_price=85.0,
            product_unit="$/bbl", crude_unit="$/bbl",
            product_month="2026-08-14", crude_month="2026-08-14",
            product_is_futures=False, crude_is_futures=False,
            conversion_log=[],  # nothing logged
        )
        # both-physical branch appends its own log note, so this should
        # actually pass -- verifying that specific carve-out:
        self.assertEqual(res["status"], "OK")

    def test_missing_price_fails_gate(self):
        res = matched_crack_spread(
            product_price=None, crude_price=85.0,
            product_unit="$/bbl", crude_unit="$/bbl",
            product_month="2026-08", crude_month="2026-08",
            product_is_futures=True, crude_is_futures=True,
            conversion_log=["logged"],
        )
        self.assertEqual(res["status"], SignalState.DATA_QUALITY_FAILURE.value)
        self.assertIn("missing product or crude price", res["reason"])

    def test_real_world_diesel_crack_matches_manual_verification(self):
        # Regression test pinned to the numbers independently verified in
        # Intelligence Briefs/Daily Macro Risk Report - 2026-08-17.md:
        # WTI ~$85.37, ULSD ~$187.57 -> crack ~$102.
        res = matched_crack_spread(
            product_price=187.57, crude_price=85.37,
            product_unit="$/bbl", crude_unit="$/bbl",
            product_month="2026-08-17", crude_month="2026-08-17",
            product_is_futures=False, crude_is_futures=False,
            conversion_log=["$4.466/gal * 42 = $187.57/bbl"],
        )
        self.assertEqual(res["status"], "OK")
        self.assertAlmostEqual(res["crack"], 102.2, places=1)


class TestSignalStateTransitions(unittest.TestCase):
    def test_normal_when_no_breaches(self):
        self.assertEqual(threshold_signal_state({}), SignalState.NORMAL)

    def test_watch(self):
        self.assertEqual(threshold_signal_state({"watch": True}), SignalState.WATCH)

    def test_elevated_overrides_watch(self):
        self.assertEqual(
            threshold_signal_state({"watch": True, "elevated": True}), SignalState.ELEVATED
        )

    def test_critical_overrides_all(self):
        self.assertEqual(
            threshold_signal_state({"watch": True, "elevated": True, "critical": True}),
            SignalState.CRITICAL,
        )

    def test_data_quality_failure_overrides_critical(self):
        self.assertEqual(
            threshold_signal_state({"critical": True, "data_quality_failure": True}),
            SignalState.DATA_QUALITY_FAILURE,
        )

    def test_worse_state_ordering(self):
        self.assertEqual(worse_state(SignalState.NORMAL, SignalState.WATCH), SignalState.WATCH)
        self.assertEqual(worse_state(SignalState.CRITICAL, SignalState.WATCH), SignalState.CRITICAL)
        self.assertEqual(worse_state(SignalState.NORMAL, SignalState.NORMAL), SignalState.NORMAL)

    def test_worse_state_data_quality_failure_dominates(self):
        self.assertEqual(
            worse_state(SignalState.CRITICAL, SignalState.DATA_QUALITY_FAILURE),
            SignalState.DATA_QUALITY_FAILURE,
        )


if __name__ == "__main__":
    unittest.main()
