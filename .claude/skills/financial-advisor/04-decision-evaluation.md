---
framework_version: 0.2.0
---

# Decision Evaluation

Library file - reached only by an explicit Read from a command; executed by `/decide` and `/advise`.

This is the rubric. `tools/decision_score.py` computes every verdict in it mechanically from a
findings JSON, and `tests/test_decision_contract.py` asserts that this file and that tool still
say the same thing. Prose never overrides the tool, and the tool never invents a number this file
does not define.

## Gate-before-score

Four gates run **in order**, before any score exists.

- A **FAIL** on any gate gives `band: gated` and an **empty score**. Not a low score - an empty
  one. A gated decision that carried a number would be quoted later as though it had survived.
- A **FLAG** proceeds, and attaches a cap.
- Every FAIL **quotes its evidence**: the balance-sheet row and its `as_of`, the IPS limit, the 07
  rule id, or the `EV-` key. A gate that fails without quoting something is not a gate, it is an
  opinion.

| Gate | Question | FAIL | FLAG |
|---|---|---|---|
| **1 Affordability & liquidity** | After this action, does the household still work? | Emergency fund below its target months; monthly cash-flow negative; debt-service ratio above 40 %; the action is funded by debt whose rate exceeds the action's evidence-based expected return; money needed for a hard goal within 24 months is put at risk | Emergency fund at 80-100 % of target; high-interest debt (proposal: >6 % nominal) outstanding while investing; a known 12-month outflow only partly funded; **any balance-sheet figure used is `stale`** (`as_of` more than 90 days old) |
| **2 Suitability** | Does it fit the horizon, the effective risk band and the limits? | Horizon under 2 years for equity or crypto; breaches a hard limit in the frozen IPS (or in `risk_profile.json` when no IPS is frozen); an instrument the user has excluded; a leveraged or derivative product with no stated hedge purpose | Pushes any limit past 80 % of its value; increases the largest concentration; adds exposure to the user's own employer sector (human-capital correlation); the stress run shows the nearest hard goal failing under any single scenario; **no frozen IPS** (`cap:65 ips=missing`) |
| **3 Legal, tax & compliance** | Is it legal, purchasable, tax-sane and clean? | Not purchasable by an EU retail investor (no KID/PRIIPs); the decision rests on information the guard classified as MNPI; an unregulated or sanctioned venue; violates a stated jurisdictional constraint | Tax-inefficient where a clean equivalent exists (distributing where accumulating exists; non-Irish domicile for US equity exposure); a tax deadline within 30 days that the action interacts with; custody regime unresolved; **the relevant 07 rule row is `_(unset)_`** - unverified |
| **4 Evidence coverage & freshness** | Are the market claims this rests on actually supported? | **never** - absence of evidence is `UNDETERMINED`, which is a FLAG with `cap:55`, so the best available band is `park` | Fewer than 3 independent successful sources, or an observation outside its freshness window, or no primary source, or any load-bearing claim that is a forecast rather than an observation |

**Gate 4 has no FAIL, on purpose.** "We could not establish this" is not the same as "this is
false", and a gate that could fail on missing evidence would quietly convert ignorance into a
verdict. It caps instead: the decision can still be parked, watched and revisited.

**Gate 4, profile mode.** A non-market decision - emergency fund, debt paydown, insurance, a
purchase - satisfies Gate 4 when every balance-sheet figure it uses was confirmed within 90 days.
No market pull is required to decide whether someone can afford their own rent.

## Scenario stress (part of Gate 2)

Computed by `tools/portfolio_math.py stress` on the **post-action** balance sheet:

| Scenario | Shock |
|---|---|
| Equity drawdown | equities -30 % |
| Rate shock | rates +2 pp, applied to bond value by duration |
| Currency move | EUR/USD ±10 % on unhedged non-EUR exposure |
| Crypto drawdown | crypto -70 % |
| Income loss | no income for 6 months |

Each scenario reports emergency months covered and nearest-hard-goal coverage afterwards. These
magnitudes are **proposals calibrated to history, not predictions**: the point is not that equities
will fall 30 %, it is that a plan which only works if they do not is not a plan.

## Weighted score (survivors only)

Weights sum to exactly 100; `tests/test_decision_contract.py` asserts it against
`tools/decision_score.py`.

| Dimension | Weight | What a high score means |
|---|---|---|
| Contribution to stated goals | 25 | Moves a named goal materially closer, on its date |
| Risk-adjusted robustness | 25 | Survives every stress scenario with margin; low regret across outcomes |
| Cost and tax efficiency | 15 | Low TER and fees, uses the wrappers and offsets available in 07, no avoidable tax event |
| Evidence strength | 15 | Claims are observations from 3+ fresh independent sources, not forecasts or sentiment |
| Simplicity and maintainability | 10 | Fewer positions, fewer moving parts, fits the time the user will actually spend |
| Reversibility and optionality | 10 | Cheap to undo; keeps future choices open |

Each dimension is scored 0-100 by the orchestrator against the evidence, then weighted. Nothing
here is scored on how appealing the idea sounds.

## Caps

Caps apply **after** the weighted sum. The lowest cap wins, and every cap is recorded as a token in
the tracker's `notes` field.

| Cap token | Set when |
|---|---|
| `cap:55 coverage=UNDETERMINED` | Gate 4 FLAG - the evidence does not meet c1-c4 |
| `cap:60 liquidity=FLAG` | Gate 1 FLAG |
| `cap:65 concentration=FLAG` | The action increases the largest concentration |
| `cap:65 ips=missing` | No frozen IPS exists |
| `cap:70 tax=FLAG` | Gate 3 FLAG |
| `cap:60 behaviour=<chasing\|recency\|fomo>` | Set by the skeptic, and **only** with a recorded evidence card as support - never on a hunch about the user |

Reported as "84, capped to 55" - both numbers, always. A capped score that hid its raw value would
lose the information that the idea was good and the evidence was not.

## Bands

| Score | Band | What it means |
|---|---|---|
| 72-100 | `do_now` | Act on it this week |
| 58-71 | `do_scoped` | Act, but halved or staged over at least 3 tranches |
| 45-57 | `park` | Do not act. **A watch trigger must be written** (`/watch add`), or the park is just forgetting |
| 0-44 | `reject` | Do not act |
| - | `gated` | A gate failed; there is no score |

## Skeptic BLOCKER to gate mapping

A skeptic objection is not a verdict. A `BLOCKER` re-opens a gate **only** when it maps to a rule
below **and** is backed by a recorded evidence card. A BLOCKER backed only by reasoning is shown to
the user and applied as a cap, never as a FAIL - otherwise the loudest argument would win.

| Skeptic BLOCKER category | Maps to | Effect |
|---|---|---|
| `cannot_afford` - the action breaks the household's cash position | Gate 1 | Re-run Gate 1 with the cited figures |
| `breaches_limit` - the action exceeds a stated limit | Gate 2 | Re-run Gate 2 against the IPS or `risk_profile.json` |
| `not_purchasable` - the instrument is unavailable to an EU retail investor | Gate 3 | Re-run Gate 3 |
| `mnpi` - the thesis rests on non-public information | Gate 3 | FAIL immediately; log the refusal |
| `evidence_absent` - the load-bearing claim has no support | Gate 4 | `cap:55` |
| `behavioural` - chasing, recency, fomo | no gate | `cap:60 behaviour=<name>`, with a card |
| anything else | no gate | Shown in the report; no mechanical effect |

## Insider-signal evidence

Form 4 clusters, 13F changes, congressional disclosures and short interest may move the **Evidence
strength** dimension and nothing else. They never lift a cap, never override an IPS limit, and
never satisfy the primary-source condition for a claim about fundamentals (see 05).

## The do-nothing option

**"Do nothing" is evaluated as a candidate every time**, through the same gates and the same score.
Not as a rhetorical gesture - as a row. A recommendation that never competed against inaction has
not been tested against the most common correct answer in personal finance.

## Decision brief template (`decisions/briefs/D-0NN.md`)

```
# D-0NN <title>

## Thesis
<one paragraph: what this does and why now>

## The strongest alternative
<the best competing use of the same money, including doing nothing, and why it lost>

## What would show this was wrong
<observable, dated, falsifiable - not "if markets fall">

## The numbers it moves
| Figure | Before | After | Source |
(from 02, with as_of dates; computed by portfolio_math.py, never typed)

## What it rests on
| Claim | EV- key | Coverage | Fresh? |

## Tax treatment
<per 07, citing rule ids and their verified_on dates; `_(unset)_` rows named as unverified>

## Decisive first check (<= 1 hour)
<the single cheapest thing that could invalidate this - "confirm the broker lists this UCITS ETF",
"confirm the loan has no prepayment penalty">

## Sizing and staging
<amount, tranches, dates>

## Exit or review trigger
<what would end or revisit this position>

---
Gates: G1 <verdict> · G2 <verdict> · G3 <verdict> · G4 <verdict>
Score: <raw>, capped to <final> (<cap tokens>) · Band: <band>
This is not licensed financial advice. Nothing here has been executed.
```
