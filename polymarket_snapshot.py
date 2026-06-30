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
import subprocess
import sys
from datetime import date, datetime, timezone
from typing import Optional

# ── Config ──────────────────────────────────────────────────────────────────
SUPA_URL  = "https://snykuqyceqpplnzmyksp.supabase.co"
SUPA_KEY  = "sb_publishable_TJg65x5w56CulOEdWFJNyQ_89loJtit"
POLY_BASE = "https://gamma-api.polymarket.com/markets"
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


# ── EIA STEO monthly refresh ──────────────────────────────────────────────────

def refresh_steo_if_stale() -> str:
    """
    Refresh macro_oil_balance from EIA STEO if the latest WORLD row is >28 days old.
    EIA publishes STEO on the 7th-10th of each month — check monthly is sufficient.
    Returns a status string for logging.
    """
    EIA_KEY = "6JlB2qAQoHxNGL6kEiiZ6fIRt8cU5FlqR8ReVWYE"
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
    # Refresh STEO monthly data before Polymarket snapshot
    print("\n  Checking EIA STEO freshness...")
    steo_status = refresh_steo_if_stale()
    print(f"  {steo_status}\n")
    main()
