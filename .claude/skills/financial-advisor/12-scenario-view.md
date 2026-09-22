---
framework_version: 0.3.0
---

# Scenario View

Library file - reached only by an explicit Read from a command; written by `/market --view` and
`/decide`; scored by `/track review --views`.

This is the one place in the repository where the system is allowed to have an opinion. Everything
about how it is stored, labelled and scored exists to keep that opinion from leaking into the
machinery that decides things.

## What a view is, and what it is not

A view answers "what do you think X does over horizon H?" as **three scenarios with probabilities**,
never as a point forecast. It is model opinion, labelled as such everywhere it appears.

**The quarantine, in full.** A view:

- never satisfies a coverage condition (c1-c4 in `05`);
- never enters a gate record - `tools/decision_score.py` refuses any `VW-` key in gate evidence;
- never moves a score dimension in `04`;
- never originates an action;
- is linked from a tracker row's `notes` as `view:VW-…`, and from nowhere else;
- appears in a report only after the verdict, in a block labelled model opinion.

Its **only** downstream use is being scored later against what actually happened.

Why this is strict: a forecast dressed as analysis is the most persuasive thing a system like this
can produce, and the least reliable. Keeping it in a box is what makes it safe to produce at all.

## Construction

### 1. Base rate first

`tools/portfolio_math.py base-rate --symbol X --horizon H --as-of YYYY-MM-DD` computes the historical distribution of
H-length returns from **recorded observations**: 10th, 50th and 90th percentiles, the worst and the
best window, and how many windows it had. The view starts there.

H is positive calendar days, or a duration such as `6m` (converted using the average calendar
month). Use `--provider` when multiple providers exist; never splice currencies or price bases.
The tool uses non-overlapping windows and names every source key. It computes price changes,
not total returns: dividends, fees, tax and FX are excluded. A historical percentile is not a
calibrated future probability. If history is missing, state that and do not invent a baseline.

Run `portfolio_math.py forecast-check` with the same arguments to compare the historical median
with a no-price-change prediction on identical later periods. Training uses only observations
whose recorded fetch and publication dates are no later than the forecast origin. Missing
availability dates or too few training windows produce `insufficient_history`, not a track
record. A history downloaded today cannot prove what was knowable years ago. Report errors,
range coverage and sample sizes; only claim improvement within the measured sample. Do not
automatically select or promote a new model or change confidence from a small winning sample.

A view that departs from the base rate must say why, citing `EV-` keys. "This time is different"
is a claim requiring evidence, not a preamble.

### 2. Three scenarios

Exactly three - `bear`, `base`, `bull` - each with:

| Field | Rule |
|---|---|
| `range` | a return range at `horizon_end`, not a point |
| `probability` | an integer; **the three sum to exactly 100** |
| `assumptions` | 2-4 of them, each an observable claim |
| `evidence_keys` | the `EV-` keys those assumptions rest on; an assumption with none is marked `unsupported` |
| `signposts` | observable things that would indicate this scenario is unfolding |

Signposts are what make a view falsifiable before its horizon ends. A scenario with no signpost is
a story.

### 3. What would change the view

One sentence, concrete and observable. "If X falls below Y" or "if the next print comes in above
Z" - not "if conditions deteriorate".

### 4. Confidence

`low`, `medium` or `high`. **`low` is the default**, and it is mandatory for anything beyond six
months and for anything in crypto. The system has no track record until `/track review --views`
produces one; until then, high confidence is unearned by construction.

### 5. Insider-signal and sentiment inputs

May inform an assumption, and must be named as such, carrying the base rate stated in `05` (a Form
4 cluster is a weak positive signal; congressional disclosures are 30-45 days stale; sentiment
extremes are weakly contrarian at best).

### 6. Disclosure

Every view ends with, verbatim:

> This is a model opinion, not evidence and not a forecast you should act on; see the decision
> verdict for what to do.

`tools/views.py validate` rejects a view without it.

## Immutability and supersession

Views are `VW-YYYYMMDD-NN` files, written once. A changed mind is a **new view** with
`supersedes: VW-…`, never an edit. The point is that a view recorded in March can be scored in
September against what it actually said, rather than against what its author wishes it had said.

## Scoring

When `horizon_end` passes, `/track review --views` runs `tools/views.py score`:

- **Which scenario materialised**, from the realised return, computed from recorded observations
  only. A missing price exits 8; nothing is estimated.
- The endpoint is `horizon_end`, even when reviewed later. Repeated scoring does not add another
  sample. Ranges are ordered bear/base/bull, non-overlapping; shared boundaries belong to the
  upper range, with the last endpoint included. An outcome outside all ranges is a visible miss.
- **The Brier score** of the stated probabilities: `mean((p_i - o_i)^2)` over the three scenarios,
  where `o_i` is 1 for the scenario that materialised and 0 otherwise. Lower is better; 0 is
  perfect, and a uniform 33/33/34 guess scores about 0.22 on this definition.
- **A running calibration table** by asset class and horizon, with its sample size.
- Range coverage includes out-of-range misses. Brier means are conditional on a covered outcome;
  show the miss count beside them. Compare against the uniform probabilities on the same covered
  outcomes. Legacy rows without horizon metadata stay in an explicit unknown-horizon group.

Those scores are shown to the user - including, especially, when they are bad - and are given to
the skeptic as data ("the system's last four crypto views were overconfident"). They are **never**
used to rank anything.

A baseline exists only after the first horizon has been scored. Before that, the honest statement
about this system's forecasting ability is that there is no evidence either way.
