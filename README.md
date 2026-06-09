# God's Eye — Strategic Intelligence Engine

> Full Convergence Strategic Intelligence — 9-Leg Convergence Model  
> Horizon: June 8 – December 31, 2026

## Overview

God's Eye is an agent-based Monte Carlo simulation framework for modelling the 2026 Persian Gulf conflict scenario and its second-order effects on energy markets, FX (USD/JPY carry trade unwind), Japanese Government Bonds, U.S. Treasuries, and global credit.

The engine runs 13 primary actor nodes + 7 subgroups through a stochastic weekly time-step simulation, outputting scenario probability distributions (A/B/C/D/E) with confidence bands.

## Architecture

```
god-s-eye/
├── src/
│   ├── gods_eye_engine.py          # Core agent-based simulation engine (Monte Carlo)
│   ├── gods_eye_rss.py             # Live intelligence feed aggregator (RSS/web)
│   ├── eia_spr_pull.py             # EIA SPR + crude inventory data pull
│   ├── spr_term_structure_model.py # SPR depletion + term structure model
│   └── state_vector_compute.py     # World state vector computation layer
├── frontend/
│   └── gods_eye_dashboard.html     # Interactive browser dashboard (Chart.js)
├── docs/
│   ├── Actors-Full-Registry.md         # All 13 primary actors + 7 subgroups
│   ├── Scenarios-Five-Primary-Branch-Tree.md  # A/B/C/D/E scenario definitions
│   └── Qatar.md                        # Qatar node deep-dive
└── requirements.txt
```

## Key Scenarios

| Scenario | Label | Description |
|---|---|---|
| A | Full Escalation | Strait closure 90d+, BoJ forced to 1.25–1.50%, reverse carry unwind |
| B | Managed Conflict | Partial disruption, BoJ hikes to 1.00%, Treasury drip sell |
| C | Ceasefire + Reopen | Negotiated resolution, BoJ pauses, yen stabilises |
| D | Israel Unilateral | Escalation without US backing, oil spike then collapse |
| E | Black Swan | Tether de-peg + credit cascade + FX intervention failure |

## FX / BoJ / Treasury Overlay

The simulation tracks USD/JPY, Japan 10Y yield, and US-JP spread weekly.  
Key trigger levels:
- **USD/JPY 160**: BoJ verbal intervention threshold
- **USD/JPY 165**: Direct FX defence + Treasury repatriation begins
- **JGB 10Y > 2.75%**: BoJ YCC ceiling stress
- **US-JP spread < 150bp**: Carry trade significantly reduced

## Running the Engine

```bash
pip install -r requirements.txt
python src/gods_eye_engine.py
```

Open `frontend/gods_eye_dashboard.html` in any browser — no server needed.

## Intelligence Feed

```bash
python src/gods_eye_rss.py   # Pulls live RSS feeds and scores them against scenario legs
```

## Data Feeds

```bash
python src/eia_spr_pull.py   # EIA API → SPR levels, crude draws, inventory
```
