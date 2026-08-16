# DRAFT — "My Model Graded Itself And Found The Opposite Mistake"
<!-- G-017 weekly analysis draft #4 · 2026-08-16 · NOT FOR AUTO-PUBLISH (G-005: publishing is user-approved) -->
<!-- Target: finance/AI niche, ~4-5 min narration, pipeline.py compatible sections -->
<!-- Follows draft #1 (07-08 live-call framing), #2 (07-15 track-record honesty), -->
<!-- #3 (08-09 data-integrity/outage). Different angle: a SELF-GRADING MISTAKE — -->
<!-- carrying a stale accuracy number for 33 days, then finding the correction ran -->
<!-- in the opposite direction from what was expected. -->
<!-- Production note: do NOT reference any specific held position, ticker, price, -->
<!-- or open trade as a recommendation. Nothing here is a signal; it is a -->
<!-- methodology post-mortem about scoring your own forecasts honestly. -->

## HOOK (0:00-0:25)
I built a system that grades its own predictions against what actually
happened — the closest thing to an honest report card a forecasting model can
have. Then I didn't run the grader for 33 days. When I finally did, the score
had gotten 77% worse. That's not the interesting part. The interesting part
is *which way* the correction went, because it was the opposite of what I
expected.

## SECTION 1 — A completed task is not a schedule (0:25-1:35)
The report card is a Brier score: the standard way to measure whether a
forecaster's stated probabilities match reality. Lower is better. Mine sat at
0.0945 across 12 resolved predictions — genuinely good — and I quoted that
number for over a month.

Here's the bug, and it has nothing to do with forecasting: the task "run the
grader" had been marked *done*. Not *recurring* — done. So nothing in the
system was ever going to trigger it again on its own. Six new predictions
resolved during that month and the number sat there, unrefreshed, quoted in
five separate reports as if it were current. [VISUAL: a single number,
timestamped once, cited five times]

Re-running it dragged the score to 0.1671 across 18 predictions — worse by
more than three-quarters. Nothing about my forecasting got worse in that
window. My *measurement* just stopped happening, and a stopped measurement
doesn't announce itself. It looks exactly like a stable one.

## SECTION 2 — The correction, and why it surprised me (1:35-2:50)
Before re-running the grader, my working theory — based on the last audit —
was that I was *overconfident*: stating high probabilities on things that
then didn't happen. That's the classic forecasting sin, and I'd built my
mental model around correcting for it.

The fresh data said the opposite. Every single prediction I'd stated at 30%
confidence or higher in this batch — all seven of them — resolved TRUE.
Seven for seven. [VISUAL: seven dots, all landing on "true"] That's not weak
evidence of underconfidence, it's about as strong as seven data points can
be: when I was *most* sure, I was actually understating how sure I should
have been.

That reversal matters more than the raw score. A model that's overconfident
should size down. A model that's underconfident on its strong calls should
lean in harder. Those are opposite instructions, and for 33 days I would have
followed the wrong one if I'd needed to act on this number.

## SECTION 3 — The part I'm proudest of, and it's a refusal (2:50-3:55)
Finding a correction is the easy half. The harder discipline is knowing where
it's allowed to apply — and here's where I nearly made a second mistake
correcting the first one.

All seven of those confident, correct calls came from the *same* underlying
claim class — call it the physical-market track, oil and shipping-adjacent
questions I have a real, tested history on. A separate class of prediction —
macro calls, recession-adjacent — has *zero* graded resolutions of its own.
None. It would have been easy to say "I'm underconfident, apply the boost
everywhere," and quietly lend the oil model's hard-won track record to a
class of question that has never once been scored.

I didn't do that. The correction stayed scoped to the class that earned it.
An unproven claim stays unproven no matter how good a *different* part of the
system looks — borrowing credibility across domains is exactly how a good
finding turns into a bad decision.

## CLOSE (3:55-4:30)
Three things, if you're keeping score on any model of your own:

One — "completed" and "recurring" are different states, and confusing them
is how a good number quietly rots. Ask what specifically will trigger the
*next* measurement, not just whether this one was taken.

Two — when you find a miscalibration, check the direction before you correct
for it. I was set up to fix the wrong problem.

Three — a correction earns its scope from the data that produced it, and
nothing wider. Being right about one thing is not evidence about a different
thing, even when they're graded by the same system.

The report card gets pulled again the moment new results come in. So far,
still flat.

---
**Fact-check anchors (for production, not narration):**
- Brier score: 0.0945 (N=12, last measured 2026-07-02) → 0.1671 (N=18,
  re-derived 2026-08-04). Re-derivation: sum of squared errors 3.008025 / 18
  = 0.1671 exact. Skill vs. climatology baseline: +0.575 → +0.323 (still
  positive — the model still beats a naive baseline, just by less).
- Staleness cause: the recurring task was recorded as a one-time "completed"
  goal (2026-07-15) with no mechanism to re-trigger it; named as owed in
  internal notes eight separate times across the 33 days without a fix
  attaching to any of them.
- Miscalibration flip: of predictions stated at ≥30% confidence in the
  resolved batch, 7 of 7 resolved TRUE — evidence of underconfidence at the
  high-confidence end, contrasted with a prior, now-superseded finding of
  overconfidence at the low end.
- Scope discipline: all 7 of those resolutions belong to one claim class
  (physical/oil-market questions); a separate macro/recession claim class has
  0 graded resolutions and was explicitly excluded from the correction.
- Do NOT state or imply any current position, entry price, or trade
  recommendation — this draft is about measurement methodology only.
