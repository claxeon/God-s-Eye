# God's Eye — Source Notes

The front-end dashboard lives at `docs/dashboard.html`.
It is a single static HTML file with zero build dependencies.

## Running locally

```bash
# from repo root — any static server works
npx serve .
# or
python3 -m http.server 8080
```

Then open: http://localhost:8080/docs/dashboard.html

## GitHub Pages

Enable Pages → Source: `main` / `docs/` to serve the dashboard at:
`https://claxeon.github.io/God-s-Eye/dashboard.html`

## Data files

All scenario CSVs live in `data/`. The dashboard auto-detects the path
based on whether it is served from `docs/` or the repo root.

| File | Scenario |
|---|---|
| `japan_hormuz_mc_capped1.csv` | BoJ capped at 1.00% |
| `japan_hormuz_mc_refined.csv` | Refined / stronger defense |

## Next steps

- `src/monte-carlo.js` — planned: in-browser live simulation engine
- `src/overlay-data.js` — planned: live USD/JPY + JGB fetcher via public APIs
