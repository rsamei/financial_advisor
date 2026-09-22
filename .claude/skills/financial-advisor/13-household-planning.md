---
framework_version: 0.3.1
---

# Household what-if planning

Library file - read from `/advise` and `/decide`. The orchestrator runs `tools/household_plan.py`; users supply
ordinary answers, not JSON. No new user command, provider or dependency is required.

## Boundaries

These are conditional cash-flow calculations, not market forecasts, evidence of future returns,
probabilities, or independent recommendations. A recorded goal and budget can motivate a
candidate; a projection illustrates its consequences. Every candidate still follows `/decide`.
Market scenario opinions remain quarantined under reference 12. No score weights change.

The calculator reads the profile and balance sheet without changing them. The owning workflow
saves its inputs and results under its ignored `advice/AR-.../` or `decisions/briefs/` directory.
Never commit these inputs or outputs. No execution, brokerage access or external account linking.

## Inputs and questions

Run profile validation first. Reuse income, essential and discretionary expenses, goals, dated
available assets and the emergency reserve target. Missing numbers are unknown, not zero.
If debt exists, ask whether its payments are already included in expenses, unless confirmed
earlier in the session. This prevents double counting. Ask about irregular bills or income only
if not already recorded, and reconcile profile liquidity events explicitly; never assume a
likely bonus is guaranteed. Reuse answers on subsequent runs and ask what changed.

Build an assumptions object with these fields:

| Field | Meaning |
|---|---|
| `debt_payments_in_expenses` | Confirmed boolean when there is debt |
| `events_complete` | Whether the user confirmed the dated events list is complete |
| `goal_kinds` | Map goal ids to `cash_outlay` only for actual future purchases/payments; wealth and retirement targets remain explicitly unmodelled |
| `events` | Objects with `on` (ISO date), signed `amount_eur`, and `source`; incremental to recurring expenses, never a goal payment already in the goal list |
| `annual_expense_growth_pct` | Explicit what-if assumption; default zero means unchanged prices, not an inflation prediction |
| `stress` | `income_loss_pct`, whole `income_loss_months`, `expense_increase_pct`, `investment_fall_pct` |

Use a twelve-month cash horizon by default. Extend to a near-term goal when helpful, up to the
tool's ten-year limit. Do not label goals outside the computed period as on track. A long-term
retirement model is not implemented. All amounts are nominal EUR; no FX is silently converted.
Income and expenses accrue daily using the calendar month's length. This is a budget projection,
not a forecast of payday or daily overdrafts; explain this if timing within a month matters.

For an illustrative stress use the existing six-month income interruption, an explicitly
labelled expense increase and broad investment decline. These magnitudes are assumptions, not
estimated odds. Investment losses are reported separately and never treated as spendable cash.
That investment loss covers current recorded holdings only; use the existing
`portfolio_math.py stress --action` for post-action holdings. A cash-only option does not
silently update a portfolio's exposures.
Run the same stress assumptions for every option. If no stress assumptions are supplied, say
that no adverse shock was tested; do not call unchanged inputs a passed stress test.

```
python tools/household_plan.py project --assumptions <local assumptions.json> --months 12
python tools/household_plan.py compare --assumptions <local assumptions.json> --options <local options.json> --months 12
```

## Comparisons

Do nothing is added automatically. Each option has a unique `id`, plain-language `label`, and
explicit cash changes: `cash_change_eur`, `monthly_income_change_eur`,
`monthly_expense_change_eur`, `fees_eur`, `tax_status`, `tax_cost_eur`, `source`.
Negative cash change means money leaves available cash; it is not necessarily a loss in wealth.
Recurring changes apply throughout this horizon.

`pausable_monthly_expense_eur` is optional and defaults to zero, which changes nothing. It declares
how much of *this option's own* `monthly_expense_change_eur` is a voluntary contribution the user
can switch off, and it is subtracted only for the months of a stressed run's income loss. It may
never exceed the option's own positive `monthly_expense_change_eur`: recorded essential spending
and debt payments are not pausable, and the tool refuses to model them as if they were. Use it only
when the user has actually said they would stop that contribution, cite them as the option's
`source`, and keep the resulting warning visible in the report. A paused contribution flatters an
option against one that cannot be paused, and the reader must be able to see that it was assumed. Use a shorter horizon or dated events if a
change stops or starts partway through. This calculator does not model loan amortisation,
investment returns, sales, or changed debt payments automatically. Do not manufacture interest
savings or future investment profits to fill these fields. Only include effects supported by
the proposed terms, or label them as what-if assumptions. Do not use it to compare total wealth.

`tax_status` is `verified`, `unverified` or `not_applicable`. Verified costs require an amount
and source reference; the workflow checks that reference against current official rules in 07.
The tool validates the presence of the reference, not the truth of a legal claim. Unknown tax
costs remain flagged, and no after-tax winner may be declared. Fees must be supplied for each
candidate, including an explicit confirmed zero. Unsupported effects remain unmodelled.

Goals consume cash in deadline and priority order, protecting the emergency reserve. Two goals
cannot reuse the same money. Shortfalls do not create imaginary borrowing. An overdue goal or
one missing its target needs clarification. This version treats the target as the full future
cash outlay; reconcile already-paid goals and amounts held outside available cash first.

Show: goal coverage/shortfall, available cash, first reserve shortfall, combined stress and
unknown costs. Provide the smallest practical next check. Do not rank options by a scenario
return, smooth over stale inputs, or turn a numerical sensitivity into an automatic action.

## Wealth-goal reality check

A goal that is a level of wealth ("double net worth", "reach X by a date") is not a cash payment,
so the cash planner marks it `not_modelled`. `tools/goal_check.py` answers the question the user
actually has: **what would reaching it on time take?**

```
python tools/goal_check.py check --inputs <local inputs.json>
```

| Input | Meaning |
|---|---|
| `goal_id` | One recorded goal with a target amount and date |
| `start_basis` | `net_worth`, `invested_assets`, `liquid_assets` (read from the derived balance sheet) or `explicit` with `start_amount_eur`. Never guessed: it must match what the goal's wording counts |
| `monthly_saving_eur` | Optional. Default is the derived monthly savings capacity, flagged as assuming every spare euro is saved |
| `what_if_annual_returns_pct` | Optional, at most five explicit assumptions shown as sensitivity. None is a prediction |
| `history` | Optional `{symbol, provider}`: counts how many recorded past periods of the same length rose enough. Price changes only |

The tool computes months left, where saving alone ends up, the gap, the yearly growth that would
close the gap, the monthly saving that would close it with no growth, and the date the target is
reached at the current pace. `status` is `saving_alone_is_enough`, `needs_growth`,
`already_reached`, `past_due` or `unknown`. Its `plain` list is ready-made wording for the
report: quote it, do not paraphrase a number.

Boundaries, same as the rest of this file. The result is a what-if: it never enters a gate
record, never moves a score dimension and never originates an action. A required growth rate is
not an expected one, and a count of past periods is not a probability - say "happened in k of n
recorded periods" with the period covered, never "a k/n chance". When the required rate is far
above anything in the recorded history, or no history is recorded, say so plainly and show the
two levers the user controls: the monthly saving and the date. Changing a goal is `/setup
--refresh`; any action prompted by the gap still goes through `/decide`.

## The cash answer (`tools/cash_plan.py`)

Answers "what should I do with my cash?" in two steps, both read-only arithmetic.

`plan` takes usable cash (L0/L1 accounts, plus a stated windfall, minus cash that is restricted
- not the user's to use, or already committed elsewhere) and takes from it, once and in order:
money already set aside (earmarks; an earmark naming a `goal_id` reduces that goal's need), the
emergency reserve (02), known bills due within twelve months, and cash-outlay goals due within
three years (06 ladder: money needed within three years stays cash). What is left is free. The
sum always reconciles. Stage `stabilise` (an overdue payment, or spending above income) keeps all
cash available; an unknown reserve target keeps it available and asks for the target.

For the free amount it lists: keep it as cash (always), expensive debt above the 06 threshold
(first, under the existing rule), cheaper debt (needs comparison), pension (needs a verified 07
rule), long-term investing (blocked while expensive debt is outstanding or the IPS is not
frozen). It introduces no new threshold, horizon or reserve exception. Which product holds the
kept cash is a separate comparison; an unresolved product question never erases the split.

`check` takes the components vetted through `/decide`, recomputes each band, returns blocked,
rejected and waiting components to the unallocated line, refuses amounts that exceed the free
cash or invest ahead of expensive debt, and writes the opening sentence. That sentence never
recommends a blocked step.
