# God's Eye

**Full-Convergence Strategic Intelligence System**  
9-Leg Geopolitical + Financial Risk Framework · June 2026

---

## Overview

God's Eye is an agent-based Monte Carlo simulation and intelligence aggregation system built to model the probability distribution of geopolitical scenarios arising from the 2026 Persian Gulf conflict, with a specific focus on:

- **Hormuz / Bab al-Mandab chokepoint closure probability and duration**
- **BoJ rate hike sequencing and carry trade unwind mechanics**
- **USD/JPY and JGB yield co-movement under reserve drawdown**
- **Japan SPR depletion and repatriation pressure on US Treasuries**
- **GCC petrodollar strain and Saudi peg stress**

---

## Repository Structure

```
god-s-eye/
│
├── engine/                         # Core simulation logic
│   ├── gods_eye_engine.py          # Agent-based Monte Carlo simulation engine (9-leg, 13 actors)
│   ├── gods_eye_rss.py             # RSS intelligence brief generator (Obsidian vault output)
│   ├── state_vector_compute.py     # State vector and leg score computation
│   ├── eia_spr_pull.py             # EIA SPR data pull and analysis
│   └── spr_term_structure_model.py # SPR term structure and crude futures model
│
├── frontend/                       # Interactive dashboards
│   ├── gods_eye_dashboard.html     # Main God's Eye scenario dashboard (static, open in browser)
│   └── japan-monte-carlo-dashboard.html  # Japan BoJ / USD-JPY / JGB Monte Carlo overlay
│
├── data/                           # Monte Carlo output CSVs
│   ├── hormuz_monte_carlo_monthly.csv      # Base Hormuz closure model (monthly medians)
│   ├── hormuz_reopening_distribution.csv   # Reopening probability distribution
│   ├── japan_hormuz_mc_refined.csv         # Refined Japan-only model (monthly medians)
│   ├── japan_hormuz_reopen_refined.csv     # Refined model reopening distribution
│   ├── japan_hormuz_mc_capped1.csv         # BoJ-capped-at-1.00% scenario (monthly medians)
│   └── japan_hormuz_reopen_capped1.csv     # Capped model reopening distribution
│
└── docs/                           # Framework documentation
    ├── hormuz_monte_carlo_report.md         # Base model assumptions and initial conditions
    ├── japan_hormuz_refined_notes.md        # Refined model parameter notes
    ├── japan_hormuz_capped1_notes.md        # Capped BoJ scenario notes
    ├── Actors-Full-Registry.md              # Full 13-actor registry with utility functions
    ├── Scenarios-Five-Primary-Branch-Tree.md # A/B/C/D/E scenario branch tree
    └── Qatar.md                             # Qatar actor deep-dive (Ras Laffan, LNG, mediation role)
```

---

## Five Primary Scenarios

| ID | Name | Starting Probability |
|---|---|---|
| **A** | Strike / Zero Restraint | 12% |
| **B** | Back Down / Duration (base case) | 38% |
| **C** | Back Channel Deal | 28% |
| **D** | IRGC Mines / Physical Hormuz Closure | 15% |
| **E** | Dual Chokepoint (Hormuz + Yanbu) | 7% |

---

## Key Initial Conditions (June 8, 2026)

| Variable | Value |
|---|---|
| USD/JPY | 160.14 |
| Japan 10Y yield | 2.72% |
| US 10Y yield | 4.60% |
| US–Japan 10Y spread | 1.87% |
| Brent crude | ~$118/bbl |
| BoJ policy rate | 0.75% |
| Japan total oil reserves | 214 days |
| Japan national SPR | 131 days |
| US SPR | ~357 MMbbl |
| Hormuz status | TOLL (PGSA selective access) |
| Bab al-Mandab | Declared Israeli-ship ban (Jun 8 confirmed) |

---

## Usage

### Run the simulation engine
```bash
pip install numpy   # feedparser also needed for RSS module
python3 engine/gods_eye_engine.py
python3 engine/gods_eye_engine.py --simulations 2000 --output results.json
python3 engine/gods_eye_engine.py --fire BOJ_HIKE --fire BAB_AL_MANDAB
```

### Run the RSS intelligence brief generator
```bash
python3 engine/gods_eye_rss.py
# Outputs: Intelligence Briefs/Intelligence Brief - YYYY-MM-DD.md
```

### Open dashboards
Open `frontend/gods_eye_dashboard.html` or `frontend/japan-monte-carlo-dashboard.html` directly in your browser. No build step required.

---

## 9 Convergence Legs

1. **War / Energy Chokepoints** — Hormuz, Bab al-Mandab, South Pars, Ras Laffan
2. **GCC / Petrodollar Strain** — Saudi peg, TIC data, de-dollarization
3. **Private Credit / NBFI** — Fund gates, NAV discounts, credit cascade
4. **Rails / XRP / Stablecoin** — GENIUS Act, RLUSD, Tether systemic risk
5. **Food / Fertilizer** — QAFCO, urea, Phase 5 famine threshold
6. **Munitions / MIC** — Stockpile depletion, defense production rates
7. **Semiconductor / Taiwan** — TSMC contingency, PLA naval posture
8. **Maritime / Insurance** — War risk premiums, Lloyd's triggers, AIS data
9. **AI / Labor** — Sahm Rule, hyperscaler capex, data center power demand

---

*Last updated: June 8, 2026*
