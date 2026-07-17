#!/usr/bin/env python3
"""G-033 — buffer-depletion CALIB component: validation attempt (2026-07-17).

G-028's WebSearch research recommended a new CALIB entry driven by
cushing_stocks_inv/crude_stocks_inv (reused, not recomputed) to model
"jawboning loses credibility as physical buffers deplete" — the mechanism
behind P16's worst-ever miss (Brent <$85, framework said 4%, it happened).

This script tests that hypothesis empirically against the REAL production
scoring logic and REAL historical data — not assumed, not shipped blind,
per G-033's own explicit instruction ("do not implement blind... validate
against G-026's backtest harness before trusting on any live prediction").

REQUIRES backtest_harness.py's G-033 extension (real EIA history for
cushing_stocks_inv/crude_stocks_inv via inventory_tracker.eia_fetch_series
— previously always None in the backtest, a real gap this closes).

RESULT (2026-07-17, first and only run so far): NULL. Neither formulation
tested shows meaningful predictive power for the backtest's outcome
(6-month >=30% Brent drawdown), over 303-306 quasi-resolutions, 2000-2025:
  - Buffer LEVEL (avg z-score of cushing_stocks_inv/crude_stocks_inv):
    Pearson r = -0.0124 (negligible; quintile bucketed frequencies are
    non-monotonic noise, not a trend: 0.082/0.066/0.049/0.131/0.032)
  - Buffer-depletion RATE (3-month change in the level, matching the
    brief's own "rate" naming): Pearson r = +0.0570 (still negligible,
    r^2 ~ 0.003; quintiles show a WEAK upward trend but Q3-Q5 are flat
    within noise: 0.067/0.017/0.083/0.100/0.095)

CONCLUSION: the buffer-depletion CALIB component was NOT added to
state_vector_compute.py. Shipping a new weighted term with this little
empirical support would add complexity and false precision to a live
framework, not fix P16-class errors. G-033 closed as a validated-null
result, not silently abandoned and not shipped blind either.

HONEST SCOPE CAVEAT: this only tests the UNCONDITIONAL form of the
hypothesis (does buffer depletion predict drawdown across ALL history).
The brief's actual mechanism is conditional — buffer depletion should only
matter WHILE an active jawboning/suppression episode is underway, which is
most of history NOT being. Testing the interaction (buffer_dev x active-
tension) would require a historical tension proxy (hormuz_status,
ceasefire_escalation) with deep free history, which does not exist (both
are MANUAL_STATE live snapshots with no historical series) — this is a
real, structural limitation of what can currently be validated, not a
choice. If a historical geopolitical-tension proxy is ever sourced, the
interaction form is worth retesting; the unconditional form tested here
should not be.

SEPARATE, MORE PROMISING LEAD (God's Eye/Audit - June 30, 2026.md, the
framework's own contemporaneous post-mortem, not this session's WebSearch):
Brent fell BEFORE the diplomatic MoU existed, and the audit's own diagnosis
is "partial PGSA-tolled throughput reducing scarcity premium — a scenario
the framework didn't explicitly model." That points at hormuz_status
needing granularity (partial toll-based flow vs. genuine closure), not a
buffer-depletion term. Queued as a separate follow-up goal, not attempted
in this script.

Run: python3 g033_buffer_depletion_validation.py
"""
import sys
import os

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from backtest_harness import build_monthly_frame, components_for_month  # noqa: E402
from state_vector_compute import zscore  # noqa: E402


def buffer_dev(comps_by_month, m):
    c = comps_by_month.get(m, {})
    cush, crude = c.get("cushing_stocks_inv"), c.get("crude_stocks_inv")
    zs = []
    if cush is not None:
        zs.append(zscore(cush, 0.0, 9.05))
    if crude is not None:
        zs.append(zscore(crude, 0.0, 20.0))
    return sum(zs) / len(zs) if zs else None


def pearson(pairs):
    n = len(pairs)
    if n == 0:
        return None
    mx = sum(p[0] for p in pairs) / n
    my = sum(p[1] for p in pairs) / n
    cov = sum((x - mx) * (y - my) for x, y in pairs) / n
    vx = sum((x - mx) ** 2 for x, y in pairs) / n
    vy = sum((y - my) ** 2 for x, y in pairs) / n
    if vx == 0 or vy == 0:
        return None
    return cov / ((vx ** 0.5) * (vy ** 0.5))


def quintile_report(label, rows, key):
    rows = sorted(rows, key=lambda r: r[key])
    n = len(rows)
    q = n // 5
    print(f"\n=== {label} ===")
    for i in range(5):
        lo, hi = i * q, (i + 1) * q if i < 4 else n
        chunk = rows[lo:hi]
        freq = sum(r["outcome"] for r in chunk) / len(chunk)
        vals = [r[key] for r in chunk]
        print(f"  Q{i+1} ({key} {min(vals):+.2f} to {max(vals):+.2f}): "
              f"n={len(chunk)}  drawdown_freq={freq:.3f}")


def main():
    frame = build_monthly_frame("2000-01", "2025-12")
    months = sorted(frame.keys())
    comps_by_month = {m: components_for_month(frame[m]) for m in months}

    brent_by_month = {m: frame[m].get("brent_spot") for m in months
                       if frame[m].get("brent_spot") is not None}
    idx = sorted(brent_by_month.keys())

    level_rows, rate_rows = [], []
    for i, m in enumerate(idx):
        if i + 6 >= len(idx):
            continue
        fwd = idx[i + 6]
        y0, mo0 = int(m[:4]), int(m[5:7])
        y1, mo1 = int(fwd[:4]), int(fwd[5:7])
        if (y1 - y0) * 12 + (mo1 - mo0) != 6:
            continue
        p0, p1 = brent_by_month[m], brent_by_month[fwd]
        if p0 is None or p1 is None or p0 <= 0:
            continue
        outcome = 1 if (p1 / p0) <= 0.70 else 0

        dev = buffer_dev(comps_by_month, m)
        if dev is not None:
            level_rows.append({"month": m, "buffer_dev": dev, "outcome": outcome})

        mi = months.index(m)
        if mi >= 3:
            dev_prior = buffer_dev(comps_by_month, months[mi - 3])
            if dev is not None and dev_prior is not None:
                rate_rows.append({"month": m, "rate": dev - dev_prior, "outcome": outcome})

    print(f"Level formulation: {len(level_rows)} resolutions with real inventory data")
    quintile_report("Buffer LEVEL vs drawdown frequency (positive = depleted)",
                     level_rows, "buffer_dev")
    r_level = pearson([(r["buffer_dev"], r["outcome"]) for r in level_rows])
    print(f"\nPearson r (level, outcome): {r_level:.4f}" if r_level is not None else "undefined")

    print(f"\n\nRate formulation: {len(rate_rows)} resolutions with valid 3mo rate")
    quintile_report("Buffer DEPLETION RATE vs drawdown frequency (positive = depleting faster)",
                     rate_rows, "rate")
    r_rate = pearson([(r["rate"], r["outcome"]) for r in rate_rows])
    print(f"\nPearson r (rate, outcome): {r_rate:.4f}" if r_rate is not None else "undefined")

    print("\nSee module docstring for the full conclusion and honest scope caveat.")


if __name__ == "__main__":
    main()
