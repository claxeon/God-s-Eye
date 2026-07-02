#!/usr/bin/env python3
"""
God's Eye — SDE-Calibrated Simulation Priors (P-034, deployed 2026-07-02)

Fits stochastic-process parameters to FULL daily FRED history (data-rich layer)
and computes empirical base rates for framework-relevant events, replacing
hand-set simulation priors. Addresses State Vector Definition 'Prior Calibration'
section: "current scenario priors are qualitative judgments, not calibrated base rates."

Series: DCOILBRENTEU (Brent spot), DEXJPUS (USD/JPY), VIXCLS (VIX).
Estimates per series:
  - annualized drift (mu) and volatility (sigma) of daily log returns
  - jump intensity (|ret| > 3*sigma_daily, events/year) and jump size stats
  - 2-regime split (threshold on 21d rolling vol at 75th pctile — labeled
    'threshold regimes', NOT an MLE HMM; honest about method)
Event base rates (unconditional, full history):
  - EVENT_brent_up30pct_6m: fraction of 126-trading-day windows with +30% move
  - EVENT_brent_up50pct_6m: same, +50%
  - EVENT_vix_above35_episode: episodes/year VIX>35, mean duration (days)
  - EVENT_jpy_appreciation_8pct_1m: fraction of 21-day windows with DEXJPUS -8%
    (yen strengthening = carry unwind trigger direction)

Writes to Supabase calibration_params (series rows: mu=ann drift, sigma=ann vol;
EVENT_ rows: mu=probability or events/year, sigma=mean duration in days or 0,
n_obs=windows/days counted). Falls back to printing JSON if REST write fails.

Run: python3 sde_priors.py
"""
import json
import math
import subprocess
import sys
from datetime import date

SUPA_URL = "https://snykuqyceqpplnzmyksp.supabase.co"
SUPA_KEY = "sb_publishable_TJg65x5w56CulOEdWFJNyQ_89loJtit"
FRED = "https://fred.stlouisfed.org/graph/fredgraph.csv?id="
TODAY = date.today().isoformat()


def fred_series(series_id):
    """Full history via curl (urllib times out on FRED — known dead end)."""
    r = subprocess.run(["curl", "-s", "--max-time", "40", FRED + series_id],
                       capture_output=True, text=True)
    out = []
    for line in r.stdout.strip().split("\n")[1:]:
        parts = line.split(",")
        if len(parts) == 2 and parts[1].strip() not in ("", "."):
            try:
                out.append((parts[0], float(parts[1])))
            except ValueError:
                pass
    return out


def log_returns(vals):
    return [math.log(vals[i] / vals[i - 1]) for i in range(1, len(vals))
            if vals[i] > 0 and vals[i - 1] > 0]


def mean(xs): return sum(xs) / len(xs)


def std(xs):
    m = mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / max(len(xs) - 1, 1))


def rolling_vol(rets, w=21):
    out = [None] * len(rets)
    for i in range(w, len(rets)):
        out[i] = std(rets[i - w:i])
    return out


def fit_series(series_id, data):
    dates = [d for d, _ in data]
    vals = [v for _, v in data]
    rets = log_returns(vals)
    n = len(rets)
    mu_d, sig_d = mean(rets), std(rets)
    mu_ann, sig_ann = mu_d * 252, sig_d * math.sqrt(252)
    jumps = [r for r in rets if abs(r - mu_d) > 3 * sig_d]
    years = n / 252
    jump_intensity = len(jumps) / years
    jump_mean_abs = mean([abs(j) for j in jumps]) if jumps else 0.0
    rv = rolling_vol(rets)
    valid = [v for v in rv if v is not None]
    thresh = sorted(valid)[int(0.75 * len(valid))]
    hi = [i for i, v in enumerate(rv) if v is not None and v >= thresh]
    hi_rets = [rets[i] for i in hi]
    lo_rets = [rets[i] for i, v in enumerate(rv) if v is not None and v < thresh]
    # mean duration of high-vol episodes
    eps, run = [], 0
    hiset = set(hi)
    for i in range(len(rets)):
        if i in hiset:
            run += 1
        elif run:
            eps.append(run); run = 0
    if run: eps.append(run)
    return {
        "series_id": series_id, "start": dates[0], "end": dates[-1], "n_obs": n,
        "mu_ann": round(mu_ann, 5), "sigma_ann": round(sig_ann, 5),
        "jump_intensity_per_yr": round(jump_intensity, 3),
        "jump_mean_abs": round(jump_mean_abs, 5),
        "regime_hi_sigma_ann": round(std(hi_rets) * math.sqrt(252), 5) if hi_rets else None,
        "regime_lo_sigma_ann": round(std(lo_rets) * math.sqrt(252), 5) if lo_rets else None,
        "regime_hi_frac": round(len(hi) / len(valid), 4),
        "regime_hi_mean_duration_d": round(mean(eps), 1) if eps else None,
        "method": "threshold regimes (75th pct 21d vol), NOT MLE-HMM",
    }


def window_move_prob(vals, window, up_frac):
    """Fraction of rolling windows with max gain >= up_frac from window start."""
    hits = total = 0
    for i in range(len(vals) - window):
        total += 1
        base = vals[i]
        if base > 0 and max(vals[i:i + window + 1]) / base - 1 >= up_frac:
            hits += 1
    return hits / total if total else 0.0, total


def window_drop_prob(vals, window, down_frac):
    hits = total = 0
    for i in range(len(vals) - window):
        total += 1
        base = vals[i]
        if base > 0 and min(vals[i:i + window + 1]) / base - 1 <= -down_frac:
            hits += 1
    return hits / total if total else 0.0, total


def episodes_above(vals, level):
    eps, run = [], 0
    for v in vals:
        if v > level:
            run += 1
        elif run:
            eps.append(run); run = 0
    if run: eps.append(run)
    return eps


def upsert_calibration_params(rows):
    body = json.dumps(rows)
    r = subprocess.run(
        ["curl", "-s", "--max-time", "30", "-X", "POST",
         "-H", f"apikey: {SUPA_KEY}", "-H", f"Authorization: Bearer {SUPA_KEY}",
         "-H", "Content-Type: application/json",
         "-H", "Prefer: resolution=merge-duplicates,return=minimal",
         "-d", body, SUPA_URL + "/rest/v1/calibration_params"],
        capture_output=True, text=True)
    ok = r.returncode == 0 and not r.stdout.strip()
    return ok, r.stdout[:200]


def main():
    print(f"SDE prior calibration — {TODAY}", file=sys.stderr)
    results, params_rows = {}, []

    for sid in ("DCOILBRENTEU", "DEXJPUS", "VIXCLS"):
        data = fred_series(sid)
        if len(data) < 500:
            print(f"  {sid}: insufficient data ({len(data)})", file=sys.stderr)
            continue
        fit = fit_series(sid, data)
        results[sid] = fit
        params_rows.append({
            "series_id": f"SDE_{sid}", "estimation_window_start": fit["start"],
            "estimation_window_end": fit["end"], "mu": fit["mu_ann"],
            "sigma": fit["sigma_ann"], "n_obs": fit["n_obs"], "weight": 1.0})
        # _REGIME row (consumed by gods_eye_engine._update_macro, P-035):
        #   mu = stationary high-vol fraction, sigma = high-regime ann vol,
        #   weight = mean episode duration in DAYS (repurposed field, documented)
        params_rows.append({
            "series_id": f"SDE_{sid}_REGIME", "estimation_window_start": fit["start"],
            "estimation_window_end": fit["end"], "mu": fit["regime_hi_frac"],
            "sigma": fit["regime_hi_sigma_ann"], "n_obs": fit["n_obs"],
            "weight": fit["regime_hi_mean_duration_d"] or 16.0})
        # _JUMP row (stored for validation; NOT consumed by engine — event system
        # already produces jumps, adding SDE jumps would double-count):
        #   mu = jumps/year (|ret|>3σ), sigma = mean abs jump size
        params_rows.append({
            "series_id": f"SDE_{sid}_JUMP", "estimation_window_start": fit["start"],
            "estimation_window_end": fit["end"], "mu": fit["jump_intensity_per_yr"],
            "sigma": fit["jump_mean_abs"], "n_obs": fit["n_obs"], "weight": 1.0})
        print(f"  {sid}: n={fit['n_obs']} mu={fit['mu_ann']:+.3f} sig={fit['sigma_ann']:.3f} "
              f"jumps/yr={fit['jump_intensity_per_yr']} hi-regime sig={fit['regime_hi_sigma_ann']}",
              file=sys.stderr)

    brent = fred_series("DCOILBRENTEU"); bv = [v for _, v in brent]
    vix = fred_series("VIXCLS"); vv = [v for _, v in vix]
    jpy = fred_series("DEXJPUS"); jv = [v for _, v in jpy]

    p30, n30 = window_move_prob(bv, 126, 0.30)
    p50, n50 = window_move_prob(bv, 126, 0.50)
    pj8, nj8 = window_drop_prob(jv, 21, 0.08)
    eps35 = episodes_above(vv, 35.0)
    yrs_vix = len(vv) / 252
    events = [
        ("EVENT_brent_up30pct_6m", p30, 0.0, n30),
        ("EVENT_brent_up50pct_6m", p50, 0.0, n50),
        ("EVENT_jpy_appreciation_8pct_1m", pj8, 0.0, nj8),
        ("EVENT_vix_above35_episodes_per_yr", len(eps35) / yrs_vix,
         mean(eps35) if eps35 else 0.0, len(vv)),
    ]
    for sid, mu, sigma, n in events:
        params_rows.append({
            "series_id": sid, "estimation_window_start": brent[0][0],
            "estimation_window_end": TODAY, "mu": round(mu, 5),
            "sigma": round(sigma, 2), "n_obs": n, "weight": 1.0})
        print(f"  {sid}: {mu:.4f} (n={n})", file=sys.stderr)
    results["events"] = {sid: {"value": round(mu, 5), "sigma": sigma, "n": n}
                         for sid, mu, sigma, n in events}

    ok, err = upsert_calibration_params(params_rows)
    results["supabase_write"] = "ok" if ok else f"FAILED: {err}"
    print(f"  calibration_params upsert: {results['supabase_write']}", file=sys.stderr)
    print(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
