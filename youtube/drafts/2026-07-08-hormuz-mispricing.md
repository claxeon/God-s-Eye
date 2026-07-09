# DRAFT — "The Market Just Moved 24 Points Toward a Model It's Never Heard Of"
<!-- G-017 weekly analysis draft #1 · 2026-07-08 · NOT FOR AUTO-PUBLISH (G-005: publishing is user-approved) -->
<!-- Target: finance/geopolitics niche, ~4-5 min narration, pipeline.py compatible sections -->

## HOOK (0:00-0:20)
On July 2nd, prediction markets said there was an 82% chance the Strait of
Hormuz returns to normal by New Year's Eve. Six days later they say 58%.
A quantitative geopolitical model called that move before it happened — and
it says the market is *still* 38 points too high. Here's the reasoning, with
the receipts.

## SECTION 1 — What the market sees (0:20-1:10)
Tankers are moving. Insurance quotes are softening. Headlines say "de-escalation."
If you only watch ship traffic, "back to normal" looks like a coin flip you'd
happily take at 58 cents.

## SECTION 2 — What the model sees (1:10-2:40)
The framework tracks nine structural legs, not headlines. The one that matters
here: Iran didn't just close a strait — it built a *toll booth*. A formalized
vetting-and-fee architecture over passage. That's an institution, and
institutions with revenue don't dissolve because tensions cool; they get
renegotiated, slowly, for a price.
"Normal" — uncontrolled passage, pre-war insurance rates, zero vetting revenue —
requires Iran to demolish its own income stream in the next ~6 months.
The model prices that at roughly 20%.

## SECTION 3 — The receipts (2:40-3:40)
This model's resolved-prediction Brier score is 0.09 across 12 graded calls
(0 is perfect, 0.25 is coin-flipping). It beat the market on the June "peace
deal" question by pricing *substantive* peace low while the market chased the
signing ceremony. And the current oil term structure — front-month barely
$1.50 over the 6-month — is exactly what institutionalized, revenue-generating
suppression looks like, not what a genuine normalization rally looks like.
[VISUAL: calibration table; Jul 2→8 market prob chart 82.5→58.5 vs model flat 20]

## SECTION 4 — The honest caveat (3:40-4:20)
The market could "resolve normal" on nominal signals — a headline, a photo-op —
while passage stays vetted and tolled. The model would be substantively right
and technically graded wrong. That risk is why this is analysis, not advice,
and why the model's own tracker sizes this view at a fraction of what the raw
edge implies. Watch three things: uncontested transit counts, war-risk
insurance premia, and whether the toll revenue actually stops.

## OUTRO (4:20-4:40)
Every prediction this model makes is logged before resolution and graded in
public after. Subscribe to follow the track record — especially the misses.

---
### Production notes (pipeline.py)
- Voice: edge-tts default news register; charts: matplotlib from state_vector_history + market_prob_snapshots (both queryable at render time)
- Thumbnail concept: split frame — tanker fleet vs. toll booth, "82% → 58%" overlay
- Description links: none external needed; NO financial-advice framing anywhere
- Compliance: zero real positions held; paper ledger disclosure line in description
