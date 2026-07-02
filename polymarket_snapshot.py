#!/usr/bin/env python3
"""
God's Eye — Polymarket Daily Snapshot
Pulls current market probabilities for all open framework_predictions,
writes snapshots to market_prob_snapshots, and flags overdue predictions.

Run: python3 polymarket_snapshot.py
Schedule: daily (called by state_vector_daily.sh)

Does NOT auto-resolve predictions — flags them for manual review only.
"""

import json
import os
import subprocess
import sys
from datetime import date, datetime, timezone
from typing import Optional

# ── Config ──────────────────────────────────────────────────────────────────
SUPA_URL  = "https://snykuqyceqpplnzmyksp.supabase.co"
SUPA_KEY  = "sb_publishable_TJg65x5w56CulOEdWFJNyQ_89loJtit"
POLY_BASE = "https://gamma-api.polymarket.com/markets"
FRED_BASE = "https://fred.stlouisfed.org/graph/fredgraph.csv?id="
EIA_KEY   = os.environ.get("EIA_API_KEY", "")
TODAY     = date.today().isoformat()


# ── Helpers ──────────────────────────────────────────────────────────────────

def curl_get(url: str, headers: Optional[dict] = None) -> Optional[dict]:
    """GET request via curl subprocess. Returns parsed JSON or None."""
    cmd = ["curl", "-s", "--max-time", "20", url]
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


def curl_post(url: str, headers: dict, body: dict) -> Optional[dict]:
    """POST request via curl subprocess."""
    cmd = [
        "curl", "-s", "--max-time", "20", "-X", "POST",
        "-H", "Content-Type: application/json",
    ]
    for k, v in headers.items():
        cmd += ["-H", f"{k}: {v}"]
    cmd += ["-d", json.dumps(body), url]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
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


# ── Fetch open predictions ────────────────────────────────────────────────────

def fetch_open_predictions() -> list:
    url = (SUPA_URL
           + "/rest/v1/framework_predictions"
           + "?outcome=is.null"
           + "&select=id,claim_text,resolves_by,framework_prob,market_slug,gods_eye_leg"
           + "&order=resolves_by.asc")
    data = curl_get(url, supa_headers())
    if not isinstance(data, list):
        print("  ⚠️  Could not fetch framework_predictions", file=sys.stderr)
        return []
    return data


# ── Fetch Polymarket market data ──────────────────────────────────────────────

def fetch_polymarket(slug: str) -> Optional[dict]:
    url = f"{POLY_BASE}?slug={slug}"
    data = curl_get(url)
    if not isinstance(data, list) or not data:
        return None
    return data[0]


def extract_yes_prob(market: dict) -> Optional[float]:
    """Extract Yes probability from outcomePrices list."""
    outcomes_raw  = market.get("outcomes", "[]")
    prices_raw    = market.get("outcomePrices", "[]")
    try:
        outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else outcomes_raw
        prices   = json.loads(prices_raw)   if isinstance(prices_raw,   str) else prices_raw
    except (json.JSONDecodeError, TypeError):
        return None
    for i, o in enumerate(outcomes):
        if str(o).lower() == "yes" and i < len(prices):
            try:
                return round(float(prices[i]), 4)
            except (ValueError, TypeError):
                return None
    # Fallback: assume first price is Yes
    if prices:
        try:
            return round(float(prices[0]), 4)
        except (ValueError, TypeError):
            pass
    return None


# ── Write snapshot to Supabase ────────────────────────────────────────────────

def write_snapshot(slug: str, question: str, yes_prob: float,
                   end_date: Optional[str], raw: dict) -> bool:
    row = {
        "snapshot_at": datetime.now(timezone.utc).isoformat(),
        "source":      "polymarket",
        "market_slug": slug,
        "question":    question,
        "yes_prob":    yes_prob,
        "end_date":    end_date,
        "raw":         raw,
    }
    h = dict(supa_headers())
    h["Prefer"] = "return=minimal"
    url = SUPA_URL + "/rest/v1/market_prob_snapshots"
    r = subprocess.run(
        ["curl", "-s", "--max-time", "20", "-X", "POST",
         "-H", f"apikey: {SUPA_KEY}",
         "-H", f"Authorization: Bearer {SUPA_KEY}",
         "-H", "Content-Type: application/json",
         "-H", "Prefer: return=minimal",
         "-d", json.dumps(row), url],
        capture_output=True, text=True
    )
    return r.returncode == 0


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 64)
    print(f"  God's Eye — Polymarket Snapshot  ({TODAY})")
    print("=" * 64)

    predictions = fetch_open_predictions()
    if not predictions:
        print("  No open predictions found.")
        return

    print(f"\n  {len(predictions)} open prediction(s):\n")

    snapshots_written = 0
    needs_resolution  = []   # overdue predictions
    slug_not_found    = []   # stale/invalid slugs

    for pred in predictions:
        pid      = pred["id"]
        claim    = pred["claim_text"][:70]
        resolves = pred["resolves_by"]          # YYYY-MM-DD
        fp       = float(pred["framework_prob"] or 0)
        slug     = pred.get("market_slug")
        leg      = pred.get("gods_eye_leg", "?")

        overdue = resolves <= TODAY if resolves else False
        status_tag = " [OVERDUE]" if overdue else ""

        print(f"  {pid}  {resolves}{status_tag}  fp={fp:.3f}  leg={leg}")
        print(f"       {claim}")

        if not slug:
            print(f"       ⬛ no market_slug — manual resolution required")
            if overdue:
                needs_resolution.append({"id": pid, "claim": pred["claim_text"],
                                         "resolves_by": resolves, "framework_prob": fp,
                                         "reason": "overdue, no market slug"})
            print()
            continue

        market = fetch_polymarket(slug)
        if market is None:
            print(f"       ⚠️  slug not found on Polymarket: {slug[:60]}")
            slug_not_found.append(pid)
            if overdue:
                needs_resolution.append({"id": pid, "claim": pred["claim_text"],
                                         "resolves_by": resolves, "framework_prob": fp,
                                         "reason": "overdue, slug returned no market"})
            print()
            continue

        yes_prob = extract_yes_prob(market)
        is_closed = market.get("closed", False)
        question  = market.get("question", "")
        end_date_raw = market.get("endDate", "")
        end_date = end_date_raw[:10] if end_date_raw else None

        prob_str = f"{yes_prob:.1%}" if yes_prob is not None else "N/A"
        closed_tag = "  CLOSED" if is_closed else ""
        print(f"       ✓ Polymarket yes_prob={prob_str}{closed_tag}  [{question[:55]}]")

        if yes_prob is not None:
            ok = write_snapshot(slug, question, yes_prob, end_date, {
                "outcomePrices": market.get("outcomePrices"),
                "outcomes":      market.get("outcomes"),
                "closed":        is_closed,
            })
            if ok:
                snapshots_written += 1
                print(f"       → snapshot written")
            else:
                print(f"       ⚠️  failed to write snapshot")

        if overdue or is_closed:
            needs_resolution.append({
                "id": pid, "claim": pred["claim_text"],
                "resolves_by": resolves, "framework_prob": fp,
                "market_yes_prob": yes_prob,
                "market_closed": is_closed,
                "reason": ("market closed" if is_closed else "overdue"),
            })
        print()

    # ── Summary ───────────────────────────────────────────────────────────────
    print("─" * 64)
    print(f"  Snapshots written today: {snapshots_written}")
    print(f"  Stale/missing slugs:     {len(slug_not_found)}  {slug_not_found}")

    if needs_resolution:
        print(f"\n  ⚠️  MANUAL RESOLUTION REQUIRED ({len(needs_resolution)} predictions):\n")
        for item in needs_resolution:
            yp = item.get("market_yes_prob")
            yp_str = f"  Polymarket yes={yp:.1%}" if yp is not None else ""
            closed = "  [MARKET CLOSED]" if item.get("market_closed") else ""
            print(f"  {item['id']}  {item['resolves_by']}  fp={item['framework_prob']:.3f}{yp_str}{closed}")
            print(f"       {item['claim'][:80]}")
            print(f"       Reason: {item['reason']}")
            print()
    else:
        print("  No predictions require immediate resolution.")

    print("=" * 64)

    # Machine-readable output for daily trigger
    result = {
        "date":               TODAY,
        "snapshots_written":  snapshots_written,
        "needs_resolution":   needs_resolution,
        "slug_not_found":     slug_not_found,
    }
    print("\nJSON_RESULT:", json.dumps(result))


# ── Data-prediction auto-checkers ────────────────────────────────────────────

def _fred_latest(series_id: str) -> Optional[float]:
    """Latest non-missing FRED value via curl."""
    r = subprocess.run(["curl", "-s", "--max-time", "20", FRED_BASE + series_id],
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


def _eia_spr_mmbbl() -> Optional[float]:
    """Latest EIA SPR (WCSSTUS1) in mmbbl; source is thousands of barrels."""
    url = (f"https://api.eia.gov/v2/petroleum/sum/sndw/data/"
           f"?frequency=weekly&data%5B0%5D=value&facets%5Bseries%5D%5B%5D=WCSSTUS1"
           f"&sort%5B0%5D%5Bcolumn%5D=period&sort%5B0%5D%5Bdirection%5D=desc&length=4"
           f"&api_key={EIA_KEY}")
    r = subprocess.run(["curl", "-s", "--max-time", "20", "-g", url],
                       capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        return None
    try:
        rows = json.loads(r.stdout)["response"]["data"]
        return float(rows[0]["value"]) / 1000.0 if rows else None
    except Exception:
        return None


def _v(val: Optional[float], fmt: str = ".2f") -> str:
    return f"{val:{fmt}}" if val is not None else "N/A"


def check_data_predictions() -> list:
    """
    Check conditions for data-resolvable predictions: P17, P19, P20, P22, P24, P25, P29, P30.
    Does NOT write outcomes to Supabase — flags conditions for human confirmation only.
    Returns list of dicts with id, condition_met, value, threshold.
    """
    print("\n" + "=" * 64)
    print("  Data Prediction Condition Checks")
    print("=" * 64 + "\n")

    # Fetch FRED series
    brent  = _fred_latest("DCOILBRENTEU")    # P17, P25
    usdjpy = _fred_latest("DEXJPUS")          # P20  (FRED = JPY per USD, so high = weak yen)
    vix    = _fred_latest("VIXCLS")           # P22, P25
    gold   = _fred_latest("GOLDAMGBD228NLBM") # P24
    spr    = _eia_spr_mmbbl()                 # P19

    def _row(pred_id, label, met, val_str, threshold):
        tag = "✓ CONDITION MET  " if met else "✗ not yet        "
        print(f"  {pred_id}  {tag}  {val_str:>18}  [{threshold}]")
        return {"id": pred_id, "condition_met": met, "value": val_str, "threshold": threshold}

    conditions = []

    # P17: Brent > $90
    p17 = brent is not None and brent > 90.0
    conditions.append(_row("P17", "Brent>$90", p17, f"Brent=${_v(brent)}", "Brent > $90.00"))

    # P19: SPR < 320 mmbbl
    p19 = spr is not None and spr < 320.0
    conditions.append(_row("P19", "SPR<320", p19, f"SPR={_v(spr,'.1f')}mmbbl", "SPR < 320 mmbbl"))

    # P20: USD/JPY < 150 (FRED DEXJPUS = JPY per USD; lower = stronger yen)
    p20 = usdjpy is not None and usdjpy < 150.0
    conditions.append(_row("P20", "USD/JPY<150", p20, f"USD/JPY={_v(usdjpy)}", "USD/JPY < 150.00"))

    # P22: VIX > 30
    p22 = vix is not None and vix > 30.0
    conditions.append(_row("P22", "VIX>30", p22, f"VIX={_v(vix)}", "VIX > 30.00"))

    # P24: Gold > $3,500
    p24 = gold is not None and gold > 3500.0
    conditions.append(_row("P24", "Gold>$3500", p24, f"Gold=${_v(gold,'.0f')}", "Gold > $3,500"))

    # P25: Brent>$100 AND VIX>35 same day
    p25 = (brent is not None and brent > 100.0 and
           vix   is not None and vix   > 35.0)
    conditions.append(_row("P25", "Brent>100+VIX>35", p25,
                           f"Brent=${_v(brent)}, VIX={_v(vix)}", "Brent>$100 AND VIX>35"))

    # P29: STEO Q4 2026 average global balance < 0 (surplus)
    q4_url = (SUPA_URL + "/rest/v1/macro_oil_balance"
              + "?country=eq.WORLD&date=gte.2026-10-01&date=lte.2026-12-01"
              + "&select=date,net_imports_mbd&order=date.asc")
    q4_rows = curl_get(q4_url, supa_headers())
    p29_val = "no Q4 data"
    p29     = False
    if isinstance(q4_rows, list) and q4_rows:
        vals = [float(r["net_imports_mbd"]) for r in q4_rows if r.get("net_imports_mbd") is not None]
        if vals:
            avg = sum(vals) / len(vals)
            p29 = avg < 0.0
            p29_val = f"Q4 avg={avg:+.3f} mb/d"
    conditions.append(_row("P29", "STEO Q4 surplus", p29, p29_val, "Q4 avg < 0 mb/d"))

    # P30: L(t) < 0.50 in state_vector_history (any row after Jul 1)
    lt_url = (SUPA_URL + "/rest/v1/state_vector_history"
              + "?composite_score=lt.0.5&date=gte.2026-07-01"
              + "&select=date,composite_score&order=date.desc&limit=1")
    lt_rows = curl_get(lt_url, supa_headers())
    p30 = isinstance(lt_rows, list) and len(lt_rows) > 0
    if p30:
        p30_val = f"L(t)={lt_rows[0]['composite_score']:.4f} on {lt_rows[0]['date']}"
    else:
        p30_val = "L(t) never <0.50"
    conditions.append(_row("P30", "L(t)<0.50", p30, p30_val, "composite < 0.50"))

    # ── Inventory-level predictions (from inventory_levels table) ──────────────
    def _inv_row(series_id):
        """Fetch latest inventory_levels row for a series."""
        url = (SUPA_URL + "/rest/v1/inventory_levels"
               + f"?series_id=eq.{series_id}"
               + "&select=value_mbbl,z_vs_5yr,as_of_date"
               + "&order=as_of_date.desc&limit=1")
        r = curl_get(url, supa_headers())
        return r[0] if isinstance(r, list) and r else None

    # P31: WCESTUS1 < 400 mmbbl
    inv_crude = _inv_row("WCESTUS1")
    p31_val = f"crude={inv_crude['value_mbbl']:.1f}mmbbl" if inv_crude else "no data"
    p31 = inv_crude is not None and float(inv_crude["value_mbbl"]) < 400.0
    conditions.append(_row("P31", "Crude<400", p31, p31_val, "WCESTUS1 < 400 mmbbl"))

    # P32: Cushing < 15 mmbbl
    inv_cush = _inv_row("W_EPC0_SAX_YCUOK_MBBL")
    p32_val = f"Cushing={inv_cush['value_mbbl']:.1f}mmbbl" if inv_cush else "no data"
    p32 = inv_cush is not None and float(inv_cush["value_mbbl"]) < 15.0
    conditions.append(_row("P32", "Cushing<15", p32, p32_val, "Cushing < 15 mmbbl"))

    # P33: Gasoline z ≤ -2.0
    inv_gas = _inv_row("WGTSTUS1")
    p33_val = f"gas_z={inv_gas['z_vs_5yr']:.2f}" if inv_gas else "no data"
    p33 = inv_gas is not None and inv_gas.get("z_vs_5yr") is not None and float(inv_gas["z_vs_5yr"]) <= -2.0
    conditions.append(_row("P33", "Gas z≤-2.0", p33, p33_val, "WGTSTUS1 z_vs_5yr ≤ -2.0"))

    # P34: Total petroleum < 1450 mmbbl
    inv_tot = _inv_row("WTTSTUS1")
    p34_val = f"total={inv_tot['value_mbbl']:.0f}mmbbl" if inv_tot else "no data"
    p34 = inv_tot is not None and float(inv_tot["value_mbbl"]) < 1450.0
    conditions.append(_row("P34", "TotalPet<1450", p34, p34_val, "WTTSTUS1 < 1,450 mmbbl"))

    # Summary
    met = [c["id"] for c in conditions if c["condition_met"]]
    print()
    if met:
        print(f"  ⚠️  AUTO-RESOLVE CANDIDATES (manual confirmation needed): {met}")
    else:
        print("  ✅  No data conditions met today.")

    return conditions


# ── EIA STEO monthly refresh ──────────────────────────────────────────────────

def refresh_steo_if_stale() -> str:
    """
    Refresh macro_oil_balance from EIA STEO if the latest WORLD row is >28 days old.
    EIA publishes STEO on the 7th-10th of each month — check monthly is sufficient.
    Returns a status string for logging.
    """
    h = supa_headers()

    # Check age of latest WORLD row
    url = SUPA_URL + "/rest/v1/macro_oil_balance?country=eq.WORLD&select=date&order=date.desc&limit=1"
    latest = curl_get(url, h)
    if isinstance(latest, list) and latest:
        from datetime import timedelta
        latest_date = date.fromisoformat(latest[0]["date"])
        if (date.today() - latest_date).days < 28:
            return f"STEO current (latest row: {latest_date})"

    # Fetch PAPR_WORLD and PATC_WORLD from EIA STEO
    def steo_fetch(series):
        url = (f"https://api.eia.gov/v2/steo/data/?api_key={EIA_KEY}"
               f"&frequency=monthly&start=2024-01"
               f"&data%5B0%5D=value&facets%5BseriesId%5D%5B%5D={series}"
               f"&sort%5B0%5D%5Bcolumn%5D=period&sort%5B0%5D%5Bdirection%5D=asc")
        r = subprocess.run(["curl","--max-time","30","-s","-g",url],
                           capture_output=True, text=True)
        if not r.stdout.strip():
            return {}
        try:
            d = json.loads(r.stdout)
            return {row["period"]: float(row["value"])
                    for row in d["response"]["data"] if row.get("value") not in (None,"")}
        except Exception:
            return {}

    prod_w = steo_fetch("PAPR_WORLD")
    cons_w = steo_fetch("PATC_WORLD")
    prod_us = steo_fetch("PAPR_US")
    cons_us = steo_fetch("PATC_US")

    if not prod_w or not cons_w:
        return "STEO refresh FAILED — EIA API unreachable"

    rows = []
    for m in sorted(set(prod_w) & set(cons_w)):
        p, c = prod_w[m], cons_w[m]
        rows.append({"date": f"{m}-01", "country": "WORLD",
                     "prod_mbd": round(p, 4),
                     "net_imports_mbd": round(c - p, 4),
                     "apparent_demand_mbd": round(c, 4),
                     "source_tag": "EIA_STEO"})
    for m in sorted(set(prod_us) & set(cons_us)):
        p, c = prod_us[m], cons_us[m]
        rows.append({"date": f"{m}-01", "country": "US",
                     "prod_mbd": round(p, 4),
                     "net_imports_mbd": round(c - p, 4),
                     "apparent_demand_mbd": round(c, 4),
                     "source_tag": "EIA_STEO"})

    if not rows:
        return "STEO refresh — no rows computed"

    # Batch upsert via Supabase REST API (merge-duplicates = ON CONFLICT UPDATE)
    url = SUPA_URL + "/rest/v1/macro_oil_balance"
    r = subprocess.run(
        ["curl","-s","--max-time","60","-X","POST",
         "-H","Content-Type: application/json",
         "-H",f"apikey: {SUPA_KEY}",
         "-H",f"Authorization: Bearer {SUPA_KEY}",
         "-H","Prefer: resolution=merge-duplicates,return=minimal",
         "-d", json.dumps(rows),
         url],
        capture_output=True, text=True
    )
    if r.returncode == 0:
        return f"STEO refreshed: {len(rows)} rows upserted"
    return f"STEO refresh FAILED: {r.stderr[:80]}"


if __name__ == "__main__":
    # 1. Refresh STEO monthly data
    print("\n  Checking EIA STEO freshness...")
    steo_status = refresh_steo_if_stale()
    print(f"  {steo_status}\n")

    # 2. Polymarket snapshot for open predictions with slugs
    main()

    # 3. Data condition checks for auto-resolvable predictions
    data_conditions = check_data_predictions()
