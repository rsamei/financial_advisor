---
framework_version: 0.2.0
---

# Outcome Review

Library file - reached only by an explicit Read from a command; used by `/track review`.

This is where the system finds out whether it was any good, and the only defence against a tool
that sounds increasingly confident while getting steadily worse. Two rules make it worth reading:
**process and outcome are scored separately**, and **a review never rewrites the advice it
reviews**.

## Process and outcome are different questions

| | The question | Judged on |
|---|---|---|
| **Process** | Was this decided well, given what was knowable at the time? | The gate record, the coverage verdict, the sizing against the limits - all as they stood on the day |
| **Outcome** | What happened? | Recorded prices only, in EUR, after costs, against a named benchmark and against doing nothing |

Scoring them together is how a run of luck becomes a reputation. Every reviewed decision gets one
of four verdicts, and the pairing is the point:

| Verdict | What it means | What to do |
|---|---|---|
| `good_process/good_outcome` | The method worked and so did the world | Keep the method. Do not conclude more than that |
| `good_process/bad_outcome` | Sound decision, unkind world | **Change nothing.** This is the outcome a sound process produces some of the time, and reacting to it is how a good process gets abandoned |
| `bad_process/good_outcome` | Luck | The dangerous one. Say so explicitly: an unexamined win teaches the wrong lesson and will be repeated |
| `bad_process/bad_outcome` | The method failed | Find the gate that should have caught it and fix the rubric, not the narrative |

## The process checklist

Answered from the recorded gate record and the tracker row, not from memory:

1. Were all four gates run, in order, with every FAIL quoting evidence?
2. Was the coverage verdict `SUPPORTED`, or was the decision knowingly taken at `UNDETERMINED`?
3. Was the size inside the limits in force **at the time** (the frozen IPS version, or the risk
   profile if none was frozen)?
4. Was a cap applied, and does the recorded band match what `decision_score.py` computes today from
   the same findings?
5. For a `do_scoped` band, was the staging actually followed? For a `park`, was a watch trigger
   written?

A decision that was never gated cannot get a process verdict. Say that, rather than inventing one.

## The outcome arithmetic

`tools/track_actions.py review` computes it, and **only from recorded observations**:

- **Action result**: what the position is worth now, minus what was put in, including fees.
- **Benchmark**: a named instrument or index, with its `EV-` keys for both the start and the end
  price. "The market" is not a benchmark; a symbol is.
- **Do nothing**: the counterfactual where the money stayed where it was and the fees were never
  spent. It is always computed, and it wins more often than people expect.
- **In EUR, after costs.** A percentage without the fee drag is a flattering number.

**A missing price stops the comparison** (exit 8). The review says which price is missing and what
would supply it. It never interpolates, never assumes flat, never substitutes a similar
instrument - a benchmark the system invented would make its own scorecard fiction, which is worse
than having no scorecard.

Time matters too: a three-month window says almost nothing about a decision made for a 2031 goal.
State the window, and state what it can support.

## Lessons

A lesson is one sentence about **what would be decided differently, and at which gate**. It is
recorded as data for future skeptic prompts.

It is never a ranking input. The system must not learn that the user dislikes bonds, or that a
particular kind of recommendation is unwelcome; that is how an advisor starts telling someone what
they want to hear. What a lesson may change is a threshold, a limit or a question the rubric asks -
and changing one of those is a deliberate edit to a versioned reference file, not a quiet drift.

"No lesson" is a valid and common outcome. Most single decisions teach nothing.

## What a review never does

- It never edits the original action list, brief or view. A review is a **new file**
  (`track/reviews/RV-YYYYMMDD-NN.md`).
- It never re-scores an old decision with today's information to make it look better or worse.
- It never claims a track record the data does not support. Until several reviews exist across a
  full market cycle, the honest summary is "too early to say", and the report says exactly that.

## Calibration over time

Once reviews accumulate, the running numbers worth reporting are: the proportion of decisions with
a complete gate record, the proportion where coverage was `SUPPORTED`, outcome against benchmark
and against doing nothing, and the luck/skill split. Scenario views get their own calibration -
Brier scores by asset class and horizon - in `12-scenario-view.md`.

Report them with their sample size. A win rate over four decisions is not a win rate.
