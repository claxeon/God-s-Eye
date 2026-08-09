# DRAFT — "My Model Lost Three Days And Didn't Notice"
<!-- G-017 weekly analysis draft #3 · 2026-08-09 · NOT FOR AUTO-PUBLISH (G-005: publishing is user-approved) -->
<!-- Target: finance/AI niche, ~4-5 min narration, pipeline.py compatible sections -->
<!-- Follows draft #1 (2026-07-08 live-call framing) and #2 (2026-07-15 track-record honesty). -->
<!-- Different angle: DATA INTEGRITY as the unglamorous risk that eats quant systems. -->
<!-- Production note: do NOT reference any specific held position, ticker or price as a -->
<!-- recommendation. Nothing here is a signal; it is a post-mortem. -->

## HOOK (0:00-0:25)
Everyone building a quantitative model worries about being wrong. Almost
nobody builds for the failure that actually happens: your model isn't wrong,
it just never got the data — and it has no way to tell you. This week my own
system lost three consecutive days of market data, and the thing that makes
that worth four minutes of your time is *how* it lost them. Not a crash. Not
an error message. The process was still running the whole time.

## SECTION 1 — The bug that isn't a crash (0:25-1:30)
The pipeline fires once a day. Its first step waits up to fifteen minutes for
the network to come up, because the machine wakes from sleep before the wifi
does. That's a sensible guard, and it worked for a month.

Here's what nobody wrote down: a `sleep` timer doesn't advance while the
computer is asleep. So the fifteen-minute budget is fifteen minutes of *awake
time*. A run that woke into a dead network didn't fail — it froze inside that
wait and stayed frozen for two days. [VISUAL: timeline, three fires stacking
up]

Then the machine woke properly, and all the backlogged runs resumed at the
same instant. Two of them wrote the same day's market snapshot simultaneously
— fourteen database rows for seven markets, duplicated to the second — and
launched two copies of the analysis process into the same repository at once.

Nothing logged an error. Every check I had said the system was healthy.

## SECTION 2 — Why the data is gone for good (1:30-2:30)
The three missed days aren't recoverable, and the reason is worth
understanding if you run anything like this.

Previous outages on this system were survivable because the *data-collection*
step kept working even when the *analysis* step failed. Prices got written;
I could reconstruct the missing analysis later from the database. That's
happened six times, and six times the numbers were rebuildable.

This time the freeze happened *before* the collection step. So there is simply
no row. Market prices are a snapshot of a moment — you can't go back and ask
what a prediction market was pricing at 1pm on a Thursday three days ago.
Those three days are a hole in the series permanently. [VISUAL: the gap in
the table — 08-05 then 08-09, nothing between]

The general lesson: rank your pipeline's steps by whether their output is
*reproducible* or *perishable*. Perishable steps deserve their own alarms,
because everything downstream inherits a hole you can never patch.

## SECTION 3 — The deeper version of the same problem (2:30-3:40)
Now the part that should genuinely bother you, because it's not a bug — it's
a design gap most models share.

One component of my composite indicator tracks currency-futures positioning,
updated weekly. Three times in the last fortnight it has dropped ten to
sixteen percent in a single day and then recovered completely. [VISUAL: the
three collapses, 07-26 / 07-31 / 08-05]

Is that a real move in positioning, or did the upstream data source fail to
answer and the code quietly substituted a degraded number? **I cannot tell
from the stored record.** The database has a column for the value and no
column for "this input was stale." A missing input and a dramatic market
move produce a byte-identical row.

And that number feeds a composite that I then use as evidence. So a data
outage can propagate into a stored reading, get quoted in a later analysis,
and there is nothing in the artifact that would ever flag it. Once I noticed
the pattern I could see the spacing — the collapses fall exactly five days
apart, twice, and the upstream source publishes weekly. That's suggestive of
a failing fetch, not a market event. Suggestive. I still can't prove it from
the data I stored, which is the whole point.

## SECTION 4 — The live call, unchanged (3:40-4:20)
Meanwhile the model's headline open position: it says there's roughly a 20%
chance Strait of Hormuz shipping traffic returns to normal by year-end. The
prediction market spent last week moving as far away from that as it ever
has — a 41.5 point gap, the widest since I started tracking — and then came
back five points this week.

I'm not claiming that as vindication. Four days of a market moving against
you is exactly when "the market is wrong" is the most expensive sentence in
finance. I'm recording it because the model's position hasn't moved and the
market's has, and next time I'll be able to say which one was right.

## CLOSE (4:20-4:45)
Three things I'd take from this week if you're building anything that runs
unattended:

One — a process that is still running is not the same as a process that is
working. Check for *output*, not for liveness.

Two — know which of your steps produce perishable data, and alarm those
loudest.

Three — if your model can't distinguish "I have no data" from "the data says
zero," it will eventually tell you something confident and false, and you
will have no way to catch it.

The fixes went in the same day. Whether they hold is next month's video.

---
**Fact-check anchors (for production, not narration):**
- Three lost days: 2026-08-06, 08-07, 08-08. No rows in `state_vector_history`
  (goes 08-05 → 08-09) or `market_prob_snapshots` (id231 → id232).
- Duplicate batch: ids 232–245, 14 rows / 7 slugs, all at 08:56:11Z 08-09.
- Composite-component collapses: 0.9434→0.8555 (07-26), 0.9527→0.7937 (07-31),
  0.8939→0.7913 (08-05). Spacing 07-26 → 07-31 → 08-05.
- Divergence: model 20%, market 61.5% on 08-05 (41.5pp, widest recorded),
  56.5% on 08-09 (36.5pp).
- Do NOT state or imply any position, entry price or trade recommendation.
