#!/usr/bin/env python3
"""
God's Eye — Japan Petroleum Inventory Model

Estimates Japanese oil inventory during the reporting gap by rolling the last
hard observation forward on a consumption-vs-import balance.

WHY
---
`strategic_inventories` last carries Japan at 2026-04-30 (385 mmbbl total /
225 mmbbl government). METI publishes petroleum statistics on roughly a
TWO-MONTH LAG -- that lag is structural and long-standing, not a wartime
blackout -- so a gap of this size is expected, and part of this one is our own
ingestion gap rather than Japanese withholding. Either way the framework is
flying on an April anchor, and Japan is the single most Hormuz-exposed major
economy in the model. So: model it.

THE TRAP THIS MODEL EXISTS TO AVOID
-----------------------------------
Naively extrapolating the observed April drawdown (-1.857 mb/d between the
Apr 7 and Apr 21 METI/Reuters prints) across the gap implies Japan burning
~170 mmbbl by end-July and approaching its statutory floor. That would be
badly wrong. The April rate was an emergency-release rate, and the releases
stopped:

  * ~80 mmbbl (~45 days) released from mid-March -- a record
  * ~20 days' worth released late April
  * NO third release in June
  * June procurement ~80% of year-ago volume
  * July procurement ~100% of year-ago, "exceeding the crude required"

So imports recovered as METI stood up non-Hormuz supply routes. The model
therefore runs IMPORTS AS A PERCENTAGE OF YEAR-AGO PROCUREMENT, anchored on
those statements, rather than freezing a crisis-peak draw rate.

DAYS-OF-COVER CONVENTION
------------------------
METI's published days-of-cover uses a NET-IMPORT denominator, not total
liquids consumption. Both hard observations agree on it:
    Apr 7 : 422 mmbbl / 228 days = 1.851 mb/d
    Apr 21: 396 mmbbl / 214 days = 1.850 mb/d
So cover is reported here on the same ~1.85 mb/d basis, making these numbers
directly comparable to METI's own -- and to the 90-day IEA obligation.

Usage:
    python3 japan_inventory_model.py
    python3 japan_inventory_model.py --json
"""

import argparse
import json
import subprocess
import sys
from datetime import date

# ── Last hard observation (strategic_inventories, country='JP') ──────────────
ANCHOR_DATE = date(2026, 4, 30)
ANCHOR_TOTAL_MMBBL = 385.0
ANCHOR_GOV_MMBBL = 225.0
ANCHOR_SOURCE = "JP_interpolated_80mmbbl_program"

# METI days-of-cover denominator, implied by both hard prints (mb/d)
METI_COVER_DENOM_MBD = 1.85
IEA_OBLIGATION_DAYS = 90

# ── Import scenarios: imports as % of YEAR-AGO same-month consumption ────────
# Base is anchored on METI/Argus/S&P reporting: no third release in June,
# June procurement ~80% y/y, July ~100% y/y and exceeding monthly requirement.
SCENARIOS = {
    "bear":  {"2026-05": 0.60, "2026-06": 0.68, "2026-07": 0.80,
              "2026-08": 0.80, "2026-09": 0.80},
    "base":  {"2026-05": 0.70, "2026-06": 0.80, "2026-07": 1.00,
              "2026-08": 1.00, "2026-09": 1.00},
    "bull":  {"2026-05": 0.78, "2026-06": 0.90, "2026-07": 1.08,
              "2026-08": 1.10, "2026-09": 1.10},
}
SCENARIO_NOTE = {
    "bear": "Hormuz worsens / alt routes falter; procurement stalls at 80% y/y",
    "base": "METI-stated path: 80% y/y June, ~100% July, held flat after",
    "bull": "Alt-route buildout over-delivers; active restocking resumes",
}

DAYS_IN = {"2026-05": 31, "2026-06": 30, "2026-07": 31,
           "2026-08": 31, "2026-09": 30}


def _eia_key():
    """Read the key without printing it (see SECRET_HANDLING.md)."""
    for line in open("/Users/leehutton/Downloads/God's Eye/Scripts/.env"):
        line = line.strip()
        if line.startswith("EIA_API_KEY") and "=" in line:
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def steo_japan_consumption(key):
    """Monthly Japan liquid fuels consumption, mb/d. --globoff: EIA v2 URLs
    contain literal [ ] which curl otherwise treats as globs (rc=3)."""
    url = (f"https://api.eia.gov/v2/steo/data/?api_key={key}&frequency=monthly"
           f"&data[0]=value&facets[seriesId][]=PATC_JA&start=2025-01&end=2026-12"
           f"&sort[0][column]=period&sort[0][direction]=asc")
    r = subprocess.run(["curl", "-s", "--globoff", "--max-time", "90", url],
                       capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        return {}
    try:
        rows = json.loads(r.stdout)["response"]["data"]
    except (json.JSONDecodeError, KeyError):
        return {}
    return {x["period"]: float(x["value"]) for x in rows if x.get("value") is not None}


def run_scenario(cons, pcts, through):
    """Roll the anchor forward. Returns (level_mmbbl, monthly detail)."""
    level = ANCHOR_TOTAL_MMBBL
    detail = []
    for period in sorted(pcts):
        if period > through:
            break
        c26 = cons.get(period)
        c25 = cons.get("2025" + period[4:])
        if c26 is None or c25 is None:
            continue
        days = DAYS_IN[period]
        # Partial month if we are inside it
        if period == through:
            days = min(days, date.today().day)
        imports = pcts[period] * c25
        net = imports - c26                      # mb/d, +ve = build
        delta = net * days                       # mmbbl
        level += delta
        detail.append({
            "period": period, "days_counted": days,
            "consumption_mbd": round(c26, 3),
            "yearago_consumption_mbd": round(c25, 3),
            "import_pct_of_yearago": pcts[period],
            "implied_imports_mbd": round(imports, 3),
            "net_mbd": round(net, 3),
            "delta_mmbbl": round(delta, 1),
            "level_end_mmbbl": round(level, 1),
        })
    return round(level, 1), detail


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--through", default=None,
                    help="last period to model, YYYY-MM (default: current month)")
    args = ap.parse_args()

    key = _eia_key()
    if not key:
        print("ERROR: no EIA_API_KEY", file=sys.stderr)
        return 2
    cons = steo_japan_consumption(key)
    if not cons:
        print("ERROR: STEO Japan consumption fetch failed", file=sys.stderr)
        return 2

    through = args.through or date.today().strftime("%Y-%m")
    gap_days = (date.today() - ANCHOR_DATE).days

    results = {}
    for name, pcts in SCENARIOS.items():
        lvl, detail = run_scenario(cons, pcts, through)
        results[name] = {
            "level_mmbbl": lvl,
            "change_from_anchor_mmbbl": round(lvl - ANCHOR_TOTAL_MMBBL, 1),
            "days_cover_meti_basis": round(lvl / METI_COVER_DENOM_MBD, 0),
            "vs_iea_obligation_days": round(lvl / METI_COVER_DENOM_MBD
                                            - IEA_OBLIGATION_DAYS, 0),
            "note": SCENARIO_NOTE[name],
            "monthly": detail,
        }

    # The counterfactual this model exists to refute
    naive_rate = -1.857
    naive_level = round(ANCHOR_TOTAL_MMBBL + naive_rate * gap_days, 1)

    out = {
        "as_of": date.today().isoformat(),
        "anchor": {"date": ANCHOR_DATE.isoformat(),
                   "total_mmbbl": ANCHOR_TOTAL_MMBBL,
                   "gov_mmbbl": ANCHOR_GOV_MMBBL,
                   "source": ANCHOR_SOURCE},
        "gap_days": gap_days,
        "modelled_through": through,
        "scenarios": results,
        "naive_extrapolation": {
            "rate_mbd": naive_rate,
            "level_mmbbl": naive_level,
            "days_cover": round(naive_level / METI_COVER_DENOM_MBD, 0),
            "why_wrong": "Freezes the Apr 7-21 emergency-release rate. Releases "
                         "stopped: no third release in June, procurement ~80% "
                         "y/y June and ~100% y/y July.",
        },
    }

    if args.json:
        print(json.dumps(out, indent=2))
        return 0

    print("=" * 74)
    print(f"  Japan Petroleum Inventory Model        {out['as_of']}")
    print("=" * 74)
    print(f"  Anchor : {ANCHOR_DATE}  {ANCHOR_TOTAL_MMBBL} mmbbl total "
          f"({ANCHOR_GOV_MMBBL} gov)")
    print(f"  Gap    : {gap_days} days unobserved   modelled through {through}")
    print(f"  Cover  : METI net-import basis, {METI_COVER_DENOM_MBD} mb/d denominator")
    print()
    for name in ("bear", "base", "bull"):
        r = results[name]
        print(f"  ── {name.upper()}  ({r['note']})")
        for m in r["monthly"]:
            print(f"     {m['period']}  imports {m['import_pct_of_yearago']:.0%} y/y "
                  f"= {m['implied_imports_mbd']:.2f}  vs cons {m['consumption_mbd']:.2f}"
                  f"  net {m['net_mbd']:+.2f} mb/d  ->  {m['level_end_mmbbl']:.0f} mmbbl")
        print(f"     ESTIMATE {r['level_mmbbl']:.0f} mmbbl "
              f"({r['change_from_anchor_mmbbl']:+.0f} vs anchor)  "
              f"= {r['days_cover_meti_basis']:.0f} days cover  "
              f"({r['vs_iea_obligation_days']:+.0f} vs 90-day IEA obligation)")
        print()
    n = out["naive_extrapolation"]
    print("  ── COUNTERFACTUAL: naive extrapolation of the April draw")
    print(f"     {n['rate_mbd']} mb/d x {gap_days}d -> {n['level_mmbbl']:.0f} mmbbl "
          f"= {n['days_cover']:.0f} days")
    print(f"     ⚠️  {n['why_wrong']}")
    print()
    print("  " + "-" * 70)
    base = results["base"]
    print(f"  READ: Japan is NOT near a stockpile crisis. Base case leaves it")
    print(f"        ~{base['days_cover_meti_basis']:.0f} days of cover, "
          f"{base['vs_iea_obligation_days']:+.0f} vs the 90-day obligation.")
    print("        The binding risk is not depletion — it is whether the")
    print("        non-Hormuz procurement routes HOLD. Bab al-Mandab is closed")
    print("        and Jazan was struck; the July recovery is fragile, not safe.")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
