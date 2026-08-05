#!/usr/bin/env python3
"""
God's Eye — China Crude Import Tracker (P58)

Tracks the single highest-information variable in the framework: whether China
has returned to the crude market.

WHY THIS EXISTS
---------------
Through mid-2026 the world ran a ~7.5 mb/d supply deficit while Brent sat in
the $80s. A large part of the answer is not market psychology but inventory
mechanics: China cut crude imports to a near-decade low (~6.4-7.2 mb/d vs an
~11 mb/d pre-war baseline) and covered the gap by drawing its own stockpile.
Barrels kept arriving -- just from tanks rather than Gulf loadings.

That buffer is finite. When it empties China must return to the market, and
the deficit becomes priceable. So the trigger to watch is Chinese import
volume, NOT escalation headlines. This script tracks it.

DATA SOURCES AND THEIR HONEST LIMITS
------------------------------------
1. EIA STEO (automated) -- PATC_CH (China liquid fuels consumption) and
   PAPR_CH (China crude + liquid fuels supply). Their difference is China's
   NET LIQUIDS IMPORT REQUIREMENT. This is a forecast/balance construct, is
   ALL-LIQUIDS (not crude-only), and does NOT capture destocking. It is the
   benchmark, not the observation.

2. Actual monthly crude imports (manual) -- from China customs (GACC),
   published monthly and widely reported within days. There is no free API:
   EIA's international endpoint carries China crude imports ANNUALLY and stops
   at 2018 (verified 2026-07-29). Rather than ship a fragile scraper against a
   source that has already burned this pipeline three times (Brent, JGB,
   CFTC), actuals live in ACTUALS below and are updated by hand.

   *** The automated half is honest about being a benchmark. The observed half
   is honest about being manual. Do not conflate them. ***

P58: "China's monthly crude oil imports rebound to within 10% of the ~11 mb/d
pre-war baseline" -> threshold 9.90 mb/d.

Usage:
    python3 china_import_tracker.py           # human-readable
    python3 china_import_tracker.py --json    # machine-readable for the pipeline
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))

# ── P58 parameters ───────────────────────────────────────────────────────────
PREWAR_BASELINE_MBD = 11.0
P58_THRESHOLD_MBD = PREWAR_BASELINE_MBD * 0.90   # "within 10%" -> 9.90

# ── Observed monthly CRUDE imports, mb/d ─────────────────────────────────────
# source: "gacc" = China customs confirmed; "reported" = press-reported, not
# yet verified at source. Update monthly. Keep the tag honest -- the runway
# math below is only as good as these numbers.
ACTUALS = {
    "2026-05": (6.36, "reported"),   # refineries ran ~13.5 mb/d against this
    "2026-06": (7.20, "reported"),   # -40% y/y, near-decade low
}

# ── Crude-balance inputs for the destocking calc, mb/d ───────────────────────
# NOTE: destocking must be computed on a CRUDE basis:
#     destock = refinery_runs - crude_imports - domestic_crude_production
# Using STEO's all-liquids supply (~5.5) instead of crude-only production
# (~4.3) inflates the result. This exact error was made and corrected on
# 2026-07-29 -- it turned 2.84 mb/d into a spurious 7.0.
DOMESTIC_CRUDE_PROD_MBD = 4.30   # ~4.32 mb/d in 2025, stable
REFINERY_RUNS = {
    "2026-05": 13.50,
}

# ── Stockpile runway assumptions ─────────────────────────────────────────────
# Total inventory entering 2026 was ~1.4 bn bbl (~360 mb government, ~1 bn
# commercial). The DRAWABLE fraction above operational minimums is the key
# unknown and is far smaller than the headline. Bands, not a point estimate.
USABLE_STOCK_BANDS_MB = [500, 600, 700, 800]
DESTOCK_START = date(2026, 3, 1)   # global balance flipped negative in March

STEO_SERIES = {"consumption": "PATC_CH", "supply": "PAPR_CH"}


def _eia_key():
    """Read the EIA key without ever printing it (see SECRET_HANDLING.md)."""
    env = os.path.join(HERE, ".env")
    if not os.path.exists(env):
        return os.environ.get("EIA_API_KEY", "")
    for line in open(env):
        line = line.strip()
        if line.startswith("EIA_API_KEY") and "=" in line:
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get("EIA_API_KEY", "")


def fetch_steo(series_id, key, start="2025-10", end="2026-12"):
    """Fetch a monthly STEO series. curl, matching the rest of the stack."""
    url = (f"https://api.eia.gov/v2/steo/data/?api_key={key}&frequency=monthly"
           f"&data[0]=value&facets[seriesId][]={series_id}"
           f"&start={start}&end={end}"
           f"&sort[0][column]=period&sort[0][direction]=asc")
    # --globoff is REQUIRED here. EIA v2 URLs contain literal [ ] in the facet
    # and sort params (facets[seriesId][], sort[0][column]); without -g curl
    # treats them as glob ranges and exits rc=3 URL-malformed with empty
    # stdout. The FRED calls elsewhere in this repo have no brackets, so the
    # existing curl pattern doesn't cover this case.
    r = subprocess.run(["curl", "-s", "--globoff", "--max-time", "60", url],
                       capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        return {}
    try:
        rows = json.loads(r.stdout)["response"]["data"]
    except (json.JSONDecodeError, KeyError):
        return {}
    out = {}
    for x in rows:
        try:
            out[x["period"]] = float(x["value"])
        except (TypeError, ValueError, KeyError):
            continue
    return out


def implied_destock(period):
    """Crude-basis destocking for a period, or None if inputs are missing."""
    runs = REFINERY_RUNS.get(period)
    act = ACTUALS.get(period)
    if runs is None or act is None:
        return None
    return round(runs - act[0] - DOMESTIC_CRUDE_PROD_MBD, 3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="emit JSON only")
    args = ap.parse_args()

    key = _eia_key()
    if not key:
        print("ERROR: no EIA_API_KEY available", file=sys.stderr)
        return 2

    cons = fetch_steo(STEO_SERIES["consumption"], key)
    supp = fetch_steo(STEO_SERIES["supply"], key)
    if not cons or not supp:
        print("ERROR: STEO fetch failed", file=sys.stderr)
        return 2

    periods = sorted(set(cons) & set(supp))
    requirement = {p: round(cons[p] - supp[p], 3) for p in periods}

    # Latest observed actual
    latest_actual_p = max(ACTUALS) if ACTUALS else None
    latest_actual = ACTUALS.get(latest_actual_p, (None, None))[0]

    # Destocking: use the most recent period we can compute, else fall back to
    # the documented 2.84 mb/d May figure.
    destocks = {p: implied_destock(p) for p in ACTUALS}
    destocks = {p: v for p, v in destocks.items() if v is not None}
    current_destock = destocks[max(destocks)] if destocks else None

    # Runway
    runway = []
    if current_destock and current_destock > 0:
        days_elapsed = (date.today() - DESTOCK_START).days
        drawn = round(current_destock * days_elapsed, 1)
        for usable in USABLE_STOCK_BANDS_MB:
            rem = usable - drawn
            runway.append({
                "usable_stock_mb": usable,
                "remaining_mb": round(rem, 1),
                "days_left": round(rem / current_destock, 0) if rem > 0 else 0,
                "exhausted": rem <= 0,
            })
    else:
        days_elapsed, drawn = 0, 0.0

    p58_met = latest_actual is not None and latest_actual >= P58_THRESHOLD_MBD
    gap = round(P58_THRESHOLD_MBD - latest_actual, 2) if latest_actual else None

    result = {
        "as_of": date.today().isoformat(),
        "p58_threshold_mbd": P58_THRESHOLD_MBD,
        "latest_actual_period": latest_actual_p,
        "latest_actual_imports_mbd": latest_actual,
        "p58_condition_met": p58_met,
        "gap_to_threshold_mbd": gap,
        "current_destock_mbd": current_destock,
        "destock_days_elapsed": days_elapsed,
        "destocked_to_date_mb": drawn,
        "runway_bands": runway,
        "steo_net_import_requirement_mbd": requirement,
        "stale_actuals": latest_actual_p is not None
                         and latest_actual_p < date.today().strftime("%Y-%m"),
    }

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    print("=" * 68)
    print(f"  China Crude Import Tracker (P58)   {result['as_of']}")
    print("=" * 68)
    print()
    print("  STEO net liquids import REQUIREMENT (automated benchmark)")
    for p in periods[-8:]:
        print(f"    {p}   {requirement[p]:6.2f} mb/d"
              f"   (cons {cons[p]:5.2f} - supply {supp[p]:4.2f})")
    print()
    print("  OBSERVED crude imports (manual, from GACC)")
    for p in sorted(ACTUALS):
        v, tag = ACTUALS[p]
        d = implied_destock(p)
        ds = f"   implied destock {d:+.2f} mb/d" if d is not None else ""
        print(f"    {p}   {v:6.2f} mb/d   [{tag}]{ds}")
    if result["stale_actuals"]:
        print(f"    ⚠️  no actual for {date.today().strftime('%Y-%m')} — update ACTUALS")
    print()
    print("  ── P58 ──")
    print(f"    threshold (within 10% of {PREWAR_BASELINE_MBD} mb/d): "
          f"{P58_THRESHOLD_MBD:.2f} mb/d")
    if latest_actual is not None:
        status = "✓ CONDITION MET" if p58_met else "✗ not yet"
        print(f"    latest {latest_actual_p}: {latest_actual:.2f} mb/d   {status}"
              f"   (gap {gap:+.2f} mb/d)")
    print()
    if current_destock:
        print("  ── Stockpile runway ──")
        print(f"    destock rate      {current_destock:.2f} mb/d")
        print(f"    days since {DESTOCK_START}   {days_elapsed}")
        print(f"    drawn to date     ~{drawn:.0f} mb")
        print()
        for b in runway:
            if b["exhausted"]:
                print(f"    if usable {b['usable_stock_mb']} mb -> EXHAUSTED "
                      f"({b['remaining_mb']:.0f} mb)")
            else:
                print(f"    if usable {b['usable_stock_mb']} mb -> "
                      f"{b['remaining_mb']:6.0f} mb left = {b['days_left']:4.0f} days")
        print()
        print("    ⚠️  Bands, not a forecast. The drawable fraction above")
        print("        operational minimums is not publicly known.")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
