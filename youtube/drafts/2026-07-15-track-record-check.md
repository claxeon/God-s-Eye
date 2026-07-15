# DRAFT — "I Graded My Own Prediction Model. Here's Its Worst Call."
<!-- G-017 weekly analysis draft #2 · 2026-07-15 · NOT FOR AUTO-PUBLISH (G-005: publishing is user-approved) -->
<!-- Target: finance/geopolitics niche, ~4-5 min narration, pipeline.py compatible sections -->
<!-- Follows draft #1 (2026-07-08-hormuz-mispricing.md); different angle — track-record honesty rather than a single live call -->

## HOOK (0:00-0:20)
Twelve resolved predictions. One quantitative model. A score of 0.09 out of a
possible 1.0 — genuinely good. But one single call is responsible for 81% of
all the error in that score. This is the video where I show you the model's
best call and its worst one, and why the worst one matters more.

## SECTION 1 — The scorecard (0:20-1:10)
A Brier score measures how honest your stated probabilities are, not just
whether you were "right." Lower is better; 0 is a perfect forecaster, 0.25 is
what you get from flipping a coin on everything. This model's rolling score
across 12 graded predictions: 0.0945. On the seven of those where a prediction
market also had a price, the model beat the market roughly 5-to-1 — 0.005
versus 0.027. [VISUAL: calibration table, 6-bucket chart]

## SECTION 2 — The one that went wrong (1:10-2:30)
In late June the model said there was only a 4% chance Brent crude would close
below $85. It closed below $85. That single miss — priced at 4%, resolved
true — accounts for 81% of the model's entire cumulative error across all 12
resolutions. Everything else it's called has been close to noise by
comparison. The lesson isn't "the model is bad" — it's that the model had no
explicit way to reason about how long *price suppression itself* can persist
against fundamentals. Jawboning, strategic reserve releases, OPEC+ signaling —
these can hold a price down for longer than a naive fundamentals model
expects, and this model didn't have a dial for that. It does now, as a
research question — see the description for the follow-up.

## SECTION 3 — What's still holding (2:30-3:40)
The active live call — Strait of Hormuz shipping traffic returning to "normal"
by year-end — hasn't broken either way. Prediction markets have oscillated
82.5% → 58.5% → 62.5% → 56.5% → 58.5% over three weeks without committing to a
direction. The model has held flat at 20% the entire time: its thesis is that
Iran built a formalized toll-and-vetting architecture over the strait, not a
temporary closure, and institutions with revenue don't dissolve because
headlines cool. The market's indecision is, itself, informative — three weeks
of oscillation with no clear trend is what you'd expect if traders don't have
a strong model of *why* traffic is still depressed, only that it currently is.
[VISUAL: Jul 2 → Jul 15 market probability line vs model's flat 20% line]

## SECTION 4 — The honest caveat (3:40-4:20)
This model has made zero graded calls on a recession — the single biggest
macro question it has an opinion on has never been tested against reality.
Most of its individual components have exactly one resolved data point each,
which is not enough to certify anything. A single win or loss on any of them
right now would swing the score by more than it should. Treat every number in
this video as "provisional, small sample" — that's not a caveat, that's the
finding.

## OUTRO (4:20-4:40)
The full track record, including every miss, is logged and graded in public
before anyone knows the outcome. Subscribe to watch the sample size grow.

---
### Production notes (pipeline.py)
- Voice: edge-tts default news register; charts: matplotlib from calibration_history (row 3, 2026-07-15) + market_prob_snapshots
- Thumbnail concept: split frame — a green checkmark row of 11 small dots and one large red X, "81% of the error is ONE call"
- Description links: none external needed; NO financial-advice framing anywhere; note the model's real-money exposure is currently a single $1.89-cost Kalshi position, disclosed in the paper-ledger description line
- Compliance: paper ledger disclosure line in description; do not name the specific held Kalshi position or its current price (avoid anything read as a live trading signal)
- Data provenance: Brier 0.0945 N=12 from calibration_history row 3 (2026-07-15); P16 Brier 0.9216 = worst miss; framework-vs-market 0.0051 vs 0.0273 on n=7 covered subset; P11 snapshot sequence 82.5(07-02)/58.5(07-08)/62.5(07-12)/56.5(07-14)/58.5(07-15)
