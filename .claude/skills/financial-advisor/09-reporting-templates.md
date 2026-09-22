---
framework_version: 0.4.0
---

# Reporting Templates

Library file - reached only by an explicit Read from a command; used by `/advise`, `/market`,
`/decide`, `/track` and `/watch`.

The detailed templates below remain the audit record. Prepend the short reader summary described
here; preserve all required detail and its order below that summary. `tools/report_check.py` enforces the parts that
can be checked mechanically: every number resolves to an `EV-` key, a `Q-` id or a profile ledger
path, every action carries a gate line, and the disclosure line is present.

Two rules that apply to every template below:

- **A number with no citation is a bug**, not a rounding. If the figure comes from the balance
  sheet, cite the path and its `as_of`; if from the market, cite the `EV-` key; if computed, name
  the tool that computed it.
- **What failed is reported beside what worked.** A report that lists only what it found reads
  exactly like a report that found everything.

## Reader summary: the default first page

Lead with **Your situation**, **What changed**, **Most useful next step**, **Why it matters**,
**Restrictions and missing information**, and **Next review**. Show at most three priorities;
keep every material restriction visible even if its action is outside those three. Include what
happens if the user changes nothing. If nothing needs changing, say so. Avoid finance vocabulary
or explain it in a short clause. No source/provider dump, internal caps or raw score in the lead.
Numbers still need citations; use a short linked decision id or named calculation tool.

Translate bands exactly: `gated` = "Do not proceed; a required check failed"; `reject` = "Do not
proceed"; `park` = "Wait; do not act yet"; `do_scoped` = "Consider only a smaller or staged step";
`do_now` = "Reasonable to consider". Never call any band safe, guaranteed, or a success probability.
Surface tax, evidence, affordability, missing policy and staging restrictions in plain language.

`tools/plain_report.py --input <local bundle.json>` provides a deterministic reader summary and
collapsible supporting checks. The bundle contains `situation`, `changed`, `review_when`, and
`decisions`: each with `title`, `why`, `next_check`, optional `uncertainty`, `is_do_nothing`, and
the original `findings` accepted by decision_score.py. It recomputes each verdict; never supply
a hand-written replacement verdict. Input order is the existing workflow priority order, not
a new ranking. Its output is a companion `summary.md`; retain the full action list or decision
brief and link to it. Run report_check.py --strict on both.
An optional `cash: {plan, check}` holds saved `cash_plan.py` outputs; the summary then opens with
**Your answer**: the answer, why, next step, what would change it, what could not be checked,
and what happens if nothing changes. Decision ids stay in the supporting checks; a lead line
carries one only when it quotes a number. A restriction shared by several options is stated
once, naming each option it affects. In plain Markdown clients where
details cannot collapse, put supporting checks after the short answer anyway.

Household calculations are labelled **What-if: under these assumptions**, citing
`household_plan.py` and the saved inputs/output. Show unknowns, data age, projected shortfalls,
and the next fact that could change the result. Do not invent likelihood percentages. Market
scenario views remain after the verdict and labelled model opinion. Market-only summaries may
state what changed and what is unknown, but may not originate an action.

## Action list (`/advise` -> `advice/AR-<date>-NN/action-list.md`)

```
# Action list AR-YYYYMMDD-NN

## Market snapshot
<3-6 bullets, each with an EV- key. Nothing here that is not in the brief.>
Coverage: <n> SUPPORTED, <n> UNDETERMINED. Failed sources: <names, or "none">.

## Your situation
| Figure | Value | As of | Source |
|---|---|---|---|
| Net worth | <computed> | <date> | profile_check.py from ledger:assets[*],liabilities[*] |
| Emergency months | <computed> | <date> | profile_check.py |
| Savings rate | <computed> | <date> | profile_check.py |
| Effective risk band | <band> | <date> | risk_profile.json (min of tolerance and capacity) |
<every row computed, none typed; stale figures marked STALE with their age>

## Actions

### HIGH - this week
- **<D-0NN> <title>** - <size> from <funding source>
  G1 <verdict> | G2 <verdict> | G3 <verdict> | G4 <verdict> | score <raw>, capped to <final> (<caps>)
  Why: <one sentence, citing EV- keys>
  First check (<= 1 hour): <the cheapest thing that could invalidate this>

### MEDIUM - this month
<same shape>

### LONG-TERM
<same shape>

## Do nothing
<the do-nothing candidate's own gate line and score - always present, even when it lost>

## Watch list
| Trigger | Condition | Then |
<every `park` band from this run appears here, or the park was just forgetting>

## What to avoid
<things considered and rejected, with the gate that rejected them>

## Non-investment flags
- Insurance gaps: <from 02, or "none recorded">
- Tax deadlines within 90 days: <from 07 rows with sources, or "07 unverified">
- Large purchases or liquidity events expected: <from 01>

## Deviations from the IPS
<drift against ips/IPS.md, or "no frozen IPS - writing one is the first HIGH action">

---
This is not licensed financial advice. Nothing here has been executed; you decide and you act.
```

## Decision verdict (`/decide`)

```
# D-0NN <title>

**Decision tuple**: <type> | <amount> | <instrument or target> | funded from <source> |
horizon <period> | goal <goal_id or none>

## Gates
| Gate | Verdict | Evidence |
|---|---|---|
| 1 Affordability & liquidity | <verdict> | <quoted figure with as_of> |
| 2 Suitability | <verdict> | <limit and its source> |
| 3 Legal, tax & compliance | <verdict> | <07 rule id + verified_on, or "unverified"> |
| 4 Evidence coverage | <SUPPORTED\|UNDETERMINED> | <Q- id, c1-c4 line> |

<every FAIL quotes its evidence verbatim; a gated decision stops here with no score>

## Verdict
Score <raw>, capped to <final> (<cap tokens>) - band **<band>**
<for do_scoped: the staging plan, with dates. For park: the watch trigger that was written.>

## The case for
<proponent, 3-5 sentences>

## The case against
<skeptic: strongest objection, disconfirming evidence, the single reframe>
<BLOCKERs that did not map to a gate are listed here and said to have no mechanical effect>

## Stress
| Scenario | Effect | Emergency months after |
<from portfolio_math.py stress on the post-action balance sheet>

## What would show this was wrong
<observable, dated>

## View (model opinion - not evidence)
<only for instrument decisions; the VW- block, after the verdict, never before>
<linked from notes as view:VW-…, never from a gate record>

---
This is not licensed financial advice. Nothing here has been executed; you decide and you act.
```

## Market brief (`/market` -> `market/exports/MB-<date>-NN.md`)

```
# Market brief MB-YYYYMMDD-NN

## Coverage
| Claim | Verdict | c1 | c2 | c3 | c4 |
|---|---|---|---|---|---|
<every claim, verdict first - this table opens the brief, before any narrative>

## Sources that failed
| Provider | Status | Reason |
<or "none" - never omitted>

## What changed
## What is contested
## What is missing
<the four fixed headings from the lens agents; present even when the answer is "nothing">

## By tier
### M Macro & rates / ### A Asset / ### S Sentiment / ### I Insider & institutional / ### P Political
<findings with EV- keys; forecasts explicitly marked and never used as support>

---
This is not licensed financial advice.
```

## Outcome review (`/track review` -> `track/reviews/RV-<date>-NN.md`)

```
# Outcome review RV-YYYYMMDD-NN

Scope: <AR-… | D-0NN | since <date>>

## Process
| Decision | Gates run | Coverage met | Sizing within limits | Verdict |
<process is judged on what was knowable at the time, never on what happened after>

## Outcome
| Decision | Action | Benchmark | Do-nothing | Result vs each, after costs, in EUR |
<every figure from recorded observations only; a missing price is stated, never estimated>

## Luck or skill
<explicit per decision: a good outcome from a bad process is luck and is labelled as such>

## Lessons
<what changes in the rubric, the limits or the behaviour - or "none", which is a valid answer>

---
This review does not rewrite the original advice. It is a new file.
```

## Alert (`/watch` -> `watch/alerts/ALERT-<date>-NN.md`)

```
# ALERT-YYYYMMDD-NN

**Trigger**: <the condition the user asked to be told about>
**Observed**: <value, as_of, EV- key>
**You said then**: <the "then" text recorded when the trigger was set>

A draft decision row <D-0NN> has been created. It has not been vetted: run `/decide --re-vet D-0NN`.

This alert is not a recommendation. A condition occurring is not a reason to act.
```
