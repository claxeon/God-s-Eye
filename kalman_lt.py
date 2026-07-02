#!/usr/bin/env python3
"""
God's Eye — Kalman-Filtered State Vector L(t) (P-034, deployed 2026-07-02)

Treats each leg score (l1..l9, l_cross) and the composite as a latent state
observed with noise: local-level state-space model

    x_t = x_{t-1} + w_t,   w ~ N(0, q)     [stress persists day to day]
    y_t = x_t + v_t,       v ~ N(0, r)     [daily computed score = noisy obs]

Filter runs on a DAILY grid from the first observation to today. Missing days
(machine off, trigger failed) are handled natively: predict-only steps widen
the variance instead of leaving a hole — fixes the 'Jul 1 permanently lost'
class of wound with honest uncertainty instead of a gap.

HYPERPARAMETERS ARE DOCUMENTED PRIORS, NOT ESTIMATES (n=4 observations cannot
discipline them): r = 0.03^2 (≈3pp measurement noise on a 0-1 leg score),
q = 0.02^2 (≈2pp/day state drift). Re-estimate by MLE once ≥60 daily rows exist.

Output: filtered mean ± sd per leg and composite for today; writes one row to
state_vector_filtered (falls back to printing JSON if REST write fails).

Run: python3 kalman_lt.py
"""
import json
import math
import subprocess
import sys
from datetime import date, timedelta

SUPA_URL = "https://snykuqyceqpplnzmyksp.supabase.co"
SUPA_KEY = "sb_publishable_TJg65x5w56CulOEdWFJNyQ_89loJtit"
LEGS = ["l1", "l2", "l3", "l4", "l5", "l6", "l7", "l8", "l9", "l_cross"]
Q = 0.02 ** 2   # process noise (documented prior)
R = 0.03 ** 2   # measurement noise (documented prior)
P0 = 0.15 ** 2  # diffuse-ish initial variance
TODAY = date.today()


def fetch_history():
    url = (SUPA_URL + "/rest/v1/state_vector_history"
           + "?select=obs_date," + ",".join(LEGS) + ",composite&order=obs_date.asc")
    r = subprocess.run(["curl", "-s", "--max-time", "20", url,
                        "-H", f"apikey: {SUPA_KEY}",
                        "-H", f"Authorization: Bearer {SUPA_KEY}"],
                       capture_output=True, text=True)
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return []


def kalman(series):
    """series: dict date->value (may be sparse). Returns (mean, var) for TODAY."""
    if not series:
        return None, None
    d0 = min(series)
    x, p = series[d0], P0
    d = d0
    while d < TODAY:
        d += timedelta(days=1)
        p += Q                              # predict
        if d in series:                     # update if observed
            k = p / (p + R)
            x += k * (series[d] - x)
            p *= (1 - k)
    return x, p


def main():
    rows = fetch_history()
    if not rows:
        print(json.dumps({"error": "no state_vector_history rows"}))
        return
    print(f"Kalman L(t) — {TODAY} — {len(rows)} observations "
          f"({rows[0]['obs_date']} … {rows[-1]['obs_date']})", file=sys.stderr)

    out = {"obs_date": TODAY.isoformat(), "n_obs": len(rows),
           "params": {"q": Q, "r": R, "p0": P0,
                      "note": "documented priors, not MLE — re-estimate at n>=60"},
           "filtered": {}}
    for key in LEGS + ["composite"]:
        series = {}
        for row in rows:
            v = row.get(key)
            if v is not None:
                series[date.fromisoformat(row["obs_date"])] = float(v)
        m, p = kalman(series)
        if m is not None:
            out["filtered"][key] = {"mean": round(m, 4), "sd": round(math.sqrt(p), 4)}
            print(f"  {key:9s} {m:.4f} ± {math.sqrt(p):.4f}", file=sys.stderr)

    comp = out["filtered"].get("composite", {})
    row = {"obs_date": out["obs_date"],
           "composite_filtered": comp.get("mean"),
           "composite_sd": comp.get("sd"),
           "per_leg": {k: v for k, v in out["filtered"].items() if k != "composite"},
           "params": out["params"],
           "n_obs_used": out["n_obs"],
           "notes": "local-level KF; missing days = predict-only (variance widens)"}
    r = subprocess.run(
        ["curl", "-s", "--max-time", "20", "-X", "POST",
         "-H", f"apikey: {SUPA_KEY}", "-H", f"Authorization: Bearer {SUPA_KEY}",
         "-H", "Content-Type: application/json",
         "-H", "Prefer: resolution=merge-duplicates,return=minimal",
         "-d", json.dumps(row), SUPA_URL + "/rest/v1/state_vector_filtered"],
        capture_output=True, text=True)
    out["supabase_write"] = "ok" if (r.returncode == 0 and not r.stdout.strip()) \
        else f"FAILED: {r.stdout[:150]}"
    print(f"  state_vector_filtered write: {out['supabase_write']}", file=sys.stderr)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
