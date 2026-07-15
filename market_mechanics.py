#!/usr/bin/env python3
"""
God's Eye — Market Mechanics Divergence Tracker
=================================================
Cross-references physical inventory signals against financial futures positioning
to identify whether price divergence is driven by:

  A) Supply mechanics  — import disruption / demand pull  (REAL physical cause)
  B) Financing mechanics — paper shorts suppressing futures price
  C) Intervention       — state-level coordinated selling via swap dealers

Physical cascade (Step 1 upstream):
  [1] Inventory draw → [2] Backwardation → [3] Spot reprice → [4] Inflation → [5] Macro

When price does NOT respond to Step 1, it means Steps 2-3 are being interrupted.
This script identifies HOW and by WHOM.

Data sources (all free):
  CFTC Disaggregated COT: kh3c-gbw2 Socrata dataset
  EIA weekly: WCRIMUS2 (imports), WCREXUS2 (exports), WPULEUS3 (refinery util)
  EIA weekly: WRPUPUS2 (product demand), WCRRIUS2 (refiner crude input)
  Supabase: inventory_levels (z-scores from inventory_tracker.py)
  FRED: DCOILBRENTEU (spot price)

Run: python3 market_mechanics.py
Schedule: daily via state_vector_daily.sh (after inventory_tracker.py)
"""

import json
import math
import os
import subprocess
import sys
from datetime import date, timedelta
from typing import Optional

# ── Config ────────────────────────────────────────────────────────────────────
SUPA_URL = "https://snykuqyceqpplnzmyksp.supabase.co"
SUPA_KEY = "sb_publishable_TJg65x5w56CulOEdWFJNyQ_89loJtit"
EIA_KEY  = os.environ.get("EIA_API_KEY", "6JlB2qAQoHxNGL6kEiiZ6fIRt8cU5FlqR8ReVWYE")
EIA_BASE = "https://api.eia.gov/v2/petroleum/sum/sndw/data/"
FRED_BASE = "https://fred.stlouisfed.org/graph/fredgraph.csv?id="
CFTC_URL = "https://publicreporting.cftc.gov/resource/kh3c-gbw2.json"
TODAY    = date.today().isoformat()

# Brent spot CALIB (from G-010 calibrate_calib.py — FRED DCOILBRENTEU 2015-2025)
BRENT_MU    = 66.43
BRENT_SIGMA = 18.71

# 5-year averages for EIA flow series (approximate — will update as data accumulates)
# These are rough baselines; z-scores improve as history builds
EIA_FLOW_BASELINES = {
    "WCRIMUS2":  {"mu": 6200.0, "sigma": 800.0,  "name": "Crude Imports",       "unit": "MBBL/D"},
    "WCREXUS2":  {"mu": 3800.0, "sigma": 700.0,  "name": "Crude Exports",       "unit": "MBBL/D"},
    "WPULEUS3":  {"mu": 90.5,   "sigma": 3.0,    "name": "Refinery Utilization", "unit": "%"},
    "WRPUPUS2":  {"mu": 20100.0,"sigma": 600.0,  "name": "Product Demand",      "unit": "MBBL/D"},
    "WCRRIUS2":  {"mu": 15500.0,"sigma": 700.0,  "name": "Refiner Crude Input", "unit": "MBBL/D"},
}

# WTI CFTC contract name (from API discovery)
WTI_CONTRACT = "CRUDE OIL, LIGHT SWEET-WTI"


# ── Helpers ───────────────────────────────────────────────────────────────────

def curl_get(url: str, headers: Optional[dict] = None, max_time: int = 20) -> Optional[object]:
    cmd = ["curl", "-s", f"--max-time={max_time}", url]
    if headers:
        for k, v in headers.items():
            cmd += ["-H", f"{k}: {v}"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return None


def supa_headers() -> dict:
    return {
        "apikey": SUPA_KEY,
        "Authorization": f"Bearer {SUPA_KEY}",
        "Content-Type": "application/json",
    }


def eia_latest(series_id: str) -> Optional[float]:
    url = (EIA_BASE
           + f"?frequency=weekly&data%5B0%5D=value"
           + f"&facets%5Bseries%5D%5B%5D={series_id}"
           + f"&sort%5B0%5D%5Bcolumn%5D=period&sort%5B0%5D%5Bdirection%5D=desc&length=2"
           + f"&api_key={EIA_KEY}")
    r = subprocess.run(["curl", "-s", "--max-time=20", "-g", url],
                       capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        return None
    try:
        rows = json.loads(r.stdout)["response"]["data"]
        return float(rows[0]["value"]) if rows else None
    except Exception:
        return None


def fred_latest(series_id: str) -> Optional[float]:
    r = subprocess.run(["curl", "-s", "--max-time=20", FRED_BASE + series_id],
                       capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        return None
    for line in reversed(r.stdout.strip().split("\n")[1:]):
        parts = line.split(",")
        if len(parts) == 2 and parts[1].strip() not in ("", "."):
            try:
                return float(parts[1])
            except ValueError:
                pass
    return None


# ── CFTC COT fetch ────────────────────────────────────────────────────────────

def fetch_cftc_wti() -> Optional[dict]:
    """
    Pull latest CFTC disaggregated COT for WTI crude (combined futures+options).
    Returns dict with managed money, swap dealer, and producer positioning.
    """
    # URL-encode the WHERE clause for Socrata API
    where = f"contract_market_name='{WTI_CONTRACT}' AND futonly_or_combined='Combined'"
    url = (CFTC_URL
           + f"?$limit=1"
           + f"&$order=report_date_as_yyyy_mm_dd DESC"
           + f"&$where={where}")
    # Socrata needs URL encoding
    import urllib.parse
    encoded_url = (CFTC_URL
                   + "?%24limit=1"
                   + "&%24order=report_date_as_yyyy_mm_dd%20DESC"
                   + "&%24where=" + urllib.parse.quote(f"contract_market_name='{WTI_CONTRACT}' AND futonly_or_combined='Combined'"))
    rows = curl_get(encoded_url)
    if not isinstance(rows, list) or not rows:
        return None
    r = rows[0]

    def _i(key): return int(r.get(key) or 0)

    oi      = _i("open_interest_all")
    mm_l    = _i("m_money_positions_long_all")
    mm_s    = _i("m_money_positions_short_all")
    swap_l  = _i("swap_positions_long_all")
    swap_s  = _i("swap__positions_short_all")
    prod_l  = _i("prod_merc_positions_long")
    prod_s  = _i("prod_merc_positions_short")

    mm_net   = mm_l   - mm_s
    swap_net = swap_l - swap_s
    prod_net = prod_l - prod_s

    return {
        "report_date":    r.get("report_date_as_yyyy_mm_dd", "")[:10],
        "open_interest":  oi,
        "mm_long":        mm_l,
        "mm_short":       mm_s,
        "mm_net":         mm_net,
        "mm_pct_oi":      round(mm_net / oi * 100, 2) if oi else None,
        "swap_long":      swap_l,
        "swap_short":     swap_s,
        "swap_net":       swap_net,
        "swap_pct_oi":    round(swap_net / oi * 100, 2) if oi else None,
        "producer_long":  prod_l,
        "producer_short": prod_s,
        "producer_net":   prod_net,
        "producer_pct_oi": round(prod_net / oi * 100, 2) if oi else None,
    }


# ── EIA flows ─────────────────────────────────────────────────────────────────

def fetch_eia_flows() -> dict:
    """Fetch latest weekly EIA crude flow and refinery metrics."""
    flows = {}
    for series_id, meta in EIA_FLOW_BASELINES.items():
        val = eia_latest(series_id)
        z = round((val - meta["mu"]) / meta["sigma"], 3) if val is not None else None
        flows[series_id] = {"value": val, "z": z, **meta}
    # Convert imports/exports to mb/d
    for s in ("WCRIMUS2", "WCREXUS2", "WCRRIUS2", "WRPUPUS2"):
        if flows.get(s, {}).get("value") is not None:
            flows[s]["value_mbd"] = round(flows[s]["value"] / 1000.0, 2)
    return flows


# ── Inventory z-scores from Supabase ─────────────────────────────────────────

def fetch_inventory_zscores() -> dict:
    """Pull latest z-scores from inventory_levels table."""
    url = (SUPA_URL + "/rest/v1/inventory_levels"
           + "?select=series_id,z_vs_5yr,value_mbbl,as_of_date"
           + "&order=as_of_date.desc&limit=10")
    rows = curl_get(url, supa_headers())
    if not isinstance(rows, list):
        return {}
    seen = {}
    for row in rows:
        sid = row.get("series_id")
        if sid not in seen and row.get("z_vs_5yr") is not None:
            seen[sid] = {"z": float(row["z_vs_5yr"]), "value": row.get("value_mbbl")}
    return seen


# ── Divergence scoring ────────────────────────────────────────────────────────

def compute_divergence(inv_z: dict, brent: Optional[float],
                       cot: Optional[dict], flows: dict) -> dict:
    """
    Compute physical-financial divergence score and classify dominant mechanic.

    physical_tightness: positive when inventories below seasonal → price SHOULD rise
    price_z:            how much Brent has already responded vs its historical mean
    divergence_score:   physical_tightness - price_z
                        positive = price has NOT caught up with physical reality

    Returns dict with all scores and hypothesis classification.
    """
    # Physical tightness: mean of negative z-scores (all series below seasonal)
    valid_z = [v["z"] for v in inv_z.values() if v["z"] is not None]
    physical_tightness = round(-sum(valid_z) / len(valid_z), 3) if valid_z else None

    # Brent z vs CALIB baseline
    price_z = round((brent - BRENT_MU) / BRENT_SIGMA, 3) if brent else None

    # Divergence: how much "price catch-up" is owed
    divergence_score = (round(physical_tightness - price_z, 3)
                        if physical_tightness is not None and price_z is not None
                        else None)

    # ── Signal classification ─────────────────────────────────────────────────
    util  = flows.get("WPULEUS3", {}).get("value")
    imp   = flows.get("WCRIMUS2", {}).get("value_mbd")
    exp   = flows.get("WCREXUS2", {}).get("value_mbd")
    net_import = round(imp - exp, 2) if (imp and exp) else None

    # A) Supply mechanics: imports below average + refiners running hard
    imp_z = flows.get("WCRIMUS2", {}).get("z")
    supply_signal = (
        "strong"   if (util and util > 94 and imp_z and imp_z < -0.5) else
        "moderate" if (util and util > 92) else
        "weak"
    )

    # B) Financing mechanics: managed money + swap dealers both heavily short
    mm_pct  = cot.get("mm_pct_oi") if cot else None
    swp_pct = cot.get("swap_pct_oi") if cot else None
    financing_signal = (
        "strong"   if (mm_pct and mm_pct < -2 and swp_pct and swp_pct < -8) else
        "moderate" if (mm_pct and mm_pct < 0  and swp_pct and swp_pct < -5) else
        "weak"
    )

    # C) Intervention: swap dealers anomalously short + producers anomalously long
    prod_pct = cot.get("producer_pct_oi") if cot else None
    intervention_signal = (
        "strong"   if (swp_pct and swp_pct < -10 and prod_pct and prod_pct > 5) else
        "moderate" if (swp_pct and swp_pct < -7  and prod_pct and prod_pct > 2) else
        "weak"
    )

    # Dominant mechanic
    rank = {"strong": 3, "moderate": 2, "weak": 1}
    scores = {
        "supply":       rank[supply_signal],
        "financing":    rank[financing_signal],
        "intervention": rank[intervention_signal],
    }
    top_score = max(scores.values())
    top = [k for k, v in scores.items() if v == top_score]
    dominant = top[0] if len(top) == 1 else "mixed"

    # Hypothesis notes
    notes = []
    if supply_signal == "strong":
        notes.append(f"Imports {imp_z:+.1f}σ vs avg; refiners at {util:.1f}% capacity — supply disruption real")
    if financing_signal in ("strong", "moderate"):
        notes.append(f"MM {mm_pct:+.1f}% OI, Swap {swp_pct:+.1f}% OI — paper shorts depressing futures")
    if intervention_signal in ("strong", "moderate"):
        notes.append(f"Swap {swp_pct:+.1f}% OI + Producer {prod_pct:+.1f}% OI (anomalous long) — state-level suppression fingerprint")

    return {
        "physical_tightness":    physical_tightness,
        "price_z":               price_z,
        "divergence_score":      divergence_score,
        "supply_signal":         supply_signal,
        "financing_signal":      financing_signal,
        "intervention_signal":   intervention_signal,
        "dominant_mechanic":     dominant,
        "hypothesis_notes":      "; ".join(notes),
        "net_import_mbd":        net_import,
    }


# ── Dashboard print ───────────────────────────────────────────────────────────

def print_dashboard(cot: Optional[dict], flows: dict,
                    inv_z: dict, brent: Optional[float], d: dict):

    print("\n" + "═" * 72)
    print("  MARKET MECHANICS DIVERGENCE TRACKER")
    print("═" * 72)

    # COT block
    print("\n  ── CFTC Positioning (WTI Light Sweet, Combined) ──")
    if cot:
        print(f"  Report date:    {cot['report_date']}")
        print(f"  Open Interest:  {cot['open_interest']:,}")
        print()
        def _pos(label, net, pct):
            bar = "▼" * min(abs(int(pct or 0)), 15) if (pct or 0) < 0 else "▲" * min(int(pct or 0), 15)
            print(f"  {label:<18} net={net:>+9,}  ({pct:>+6.1f}% OI)  {bar}")
        _pos("Managed Money",  cot["mm_net"],       cot["mm_pct_oi"] or 0)
        _pos("Swap Dealers",   cot["swap_net"],     cot["swap_pct_oi"] or 0)
        _pos("Producers",      cot["producer_net"], cot["producer_pct_oi"] or 0)
    else:
        print("  [CFTC data unavailable]")

    # EIA flows block
    print("\n  ── EIA Weekly Flows ──")
    util = flows.get("WPULEUS3", {})
    imp  = flows.get("WCRIMUS2", {})
    exp  = flows.get("WCREXUS2", {})
    rfi  = flows.get("WCRRIUS2", {})
    dem  = flows.get("WRPUPUS2", {})
    net  = d.get("net_import_mbd")

    def _eia(label, meta, suffix=""):
        v = meta.get("value"); z = meta.get("z")
        if v is None: return
        v_str = f"{v/1000:.2f} mb/d" if meta.get("unit") == "MBBL/D" else f"{v:.1f}%"
        z_str = f"z={z:+.2f}" if z is not None else ""
        flag = " 🔴" if (z and z <= -1.5) else (" 🟠" if (z and z <= -0.5) else
               (" 🟢" if (z and z >= 1.5) else ""))
        print(f"  {label:<24} {v_str:>12}  {z_str:>8}{flag}{suffix}")

    _eia("Crude Imports",        imp)
    _eia("Crude Exports",        exp)
    if net is not None:
        print(f"  {'Net Import':24} {net:>+8.2f} mb/d")
    _eia("Refinery Utilization", util)
    _eia("Refiner Crude Input",  rfi)
    _eia("Product Demand",       dem)

    # Divergence block
    print("\n  ── Physical-Financial Divergence ──")
    pt = d.get("physical_tightness")
    pz = d.get("price_z")
    ds = d.get("divergence_score")
    brent_str = f"${brent:.2f}" if brent else "N/A"
    print(f"  Brent spot:              {brent_str}")
    print(f"  Physical tightness:      {pt:+.3f}  (mean -z of inventory series; +ve = tight)" if pt else "  Physical tightness:      N/A")
    print(f"  Brent price z:           {pz:+.3f}  (vs CALIB mu=${BRENT_MU:.0f}, σ=${BRENT_SIGMA:.0f})" if pz else "  Brent price z:           N/A")
    print(f"  Divergence score:        {ds:+.3f}  (>0 = price owes physical an upward move)" if ds else "  Divergence score:        N/A")

    # Hypothesis classification
    print("\n  ── Dominant Mechanism ──")
    signals = {
        "Supply mechanics":    d["supply_signal"],
        "Financing mechanics": d["financing_signal"],
        "Intervention":        d["intervention_signal"],
    }
    for name, sig in signals.items():
        icon = {"strong": "🔴", "moderate": "🟠", "weak": "⚪"}.get(sig, "?")
        print(f"  {name:<24} {icon} {sig.upper()}")
    print(f"\n  DOMINANT MECHANIC: {d['dominant_mechanic'].upper()}")

    # Notes
    if d.get("hypothesis_notes"):
        print("\n  Evidence:")
        for note in d["hypothesis_notes"].split("; "):
            if note:
                print(f"  → {note}")

    print("═" * 72)


# ── Supabase upsert ───────────────────────────────────────────────────────────

def upsert_row(row: dict) -> bool:
    r = subprocess.run(
        ["curl", "-s", "--max-time=30", "-X", "POST",
         "-H", "Content-Type: application/json",
         "-H", f"apikey: {SUPA_KEY}",
         "-H", f"Authorization: Bearer {SUPA_KEY}",
         "-H", "Prefer: resolution=merge-duplicates,return=minimal",
         "-d", json.dumps(row),
         SUPA_URL + "/rest/v1/market_mechanics_daily?on_conflict=as_of_date"],
        capture_output=True, text=True
    )
    return r.returncode == 0


# ── Main ──────────────────────────────────────────────────────────────────────

def run_market_mechanics() -> dict:
    print(f"\n  God's Eye — Market Mechanics  ({TODAY})")
    print("  " + "─" * 50)

    # 1. CFTC positioning
    print("  Fetching CFTC COT (WTI managed money)...", end=" ", flush=True)
    cot = fetch_cftc_wti()
    print(f"MM net={cot['mm_net']:+,}  Swap={cot['swap_net']:+,}" if cot else "FAILED")

    # 2. EIA flows
    print("  Fetching EIA crude flows...")
    flows = fetch_eia_flows()
    for sid, meta in flows.items():
        v = meta.get("value"); z = meta.get("z")
        if v is not None:
            v_str = f"{v/1000:.2f} mb/d" if meta["unit"] == "MBBL/D" else f"{v:.1f}%"
            print(f"    {meta['name']}: {v_str}" + (f"  z={z:+.2f}" if z else ""))

    # 3. Inventory z-scores (already in Supabase from inventory_tracker.py)
    inv_z = fetch_inventory_zscores()
    print(f"  Inventory z-scores loaded: {len(inv_z)} series")

    # 4. Brent spot
    brent = fred_latest("DCOILBRENTEU")
    print(f"  Brent spot: ${brent:.2f}" if brent else "  Brent spot: unavailable")

    # 5. Compute divergence
    d = compute_divergence(inv_z, brent, cot, flows)

    # 6. Print dashboard
    print_dashboard(cot, flows, inv_z, brent, d)

    # 7. Upsert to Supabase
    row = {
        "as_of_date":           TODAY,
        "cot_report_date":      cot["report_date"] if cot else None,
        "wti_open_interest":    cot["open_interest"] if cot else None,
        "mm_long":              cot["mm_long"] if cot else None,
        "mm_short":             cot["mm_short"] if cot else None,
        "mm_net":               cot["mm_net"] if cot else None,
        "mm_pct_oi":            cot["mm_pct_oi"] if cot else None,
        "swap_long":            cot["swap_long"] if cot else None,
        "swap_short":           cot["swap_short"] if cot else None,
        "swap_net":             cot["swap_net"] if cot else None,
        "swap_pct_oi":          cot["swap_pct_oi"] if cot else None,
        "producer_long":        cot["producer_long"] if cot else None,
        "producer_short":       cot["producer_short"] if cot else None,
        "producer_net":         cot["producer_net"] if cot else None,
        "producer_pct_oi":      cot["producer_pct_oi"] if cot else None,
        "crude_imports_mbd":    flows.get("WCRIMUS2", {}).get("value_mbd"),
        "crude_exports_mbd":    flows.get("WCREXUS2", {}).get("value_mbd"),
        "net_import_mbd":       d.get("net_import_mbd"),
        "refinery_utilization": flows.get("WPULEUS3", {}).get("value"),
        "product_demand_mbd":   flows.get("WRPUPUS2", {}).get("value_mbd"),
        "physical_tightness":   d.get("physical_tightness"),
        "price_z":              d.get("price_z"),
        "divergence_score":     d.get("divergence_score"),
        "supply_signal":        d["supply_signal"],
        "financing_signal":     d["financing_signal"],
        "intervention_signal":  d["intervention_signal"],
        "dominant_mechanic":    d["dominant_mechanic"],
        "hypothesis_notes":     d.get("hypothesis_notes"),
    }
    ok = upsert_row(row)
    print(f"\n  {'✓' if ok else '⚠️  FAILED'} Supabase upsert: market_mechanics_daily")
    return row


if __name__ == "__main__":
    run_market_mechanics()
