---
framework_version: 0.2.0
---

# Risk Profile

Library file - reached only by an explicit Read from a command; written by `/setup`.

Schema and rules. The instance is `profile/risk_profile.json`, ignored by git.

Two different things are measured here, and conflating them is the classic way to sell someone a
portfolio they cannot hold:

- **Risk tolerance** - how much volatility the user can stand *psychologically*. Asked.
- **Risk capacity** - how much they can stand *financially*, given horizon, income, reserves, debt
  and dependants. Computed from `02-balance-sheet-and-cashflow.md`, never asked.

**The effective band is `min(tolerance_band, capacity_band)`** on the ordering
`low < moderate < high`. A user who wants to take more risk than their balance sheet supports gets
the band their balance sheet supports. This rule is asserted by
`tests/test_profile_check.py::test_effective_band_is_the_minimum`.

## Risk tolerance questionnaire

Ten items, each scored 1-5, asked verbatim by `/setup`. The score is the sum, 10-50.

| # | Item | 1 | 5 |
|---|---|---|---|
| Q1 | Your portfolio falls 30 % in four months. What do you do? | Sell most of it | Buy more to the plan |
| Q2 | In the 2020 or 2022 drawdowns, what did you actually do? (If not invested then, say so and this item is skipped.) | Sold at a loss | Kept contributing |
| Q3 | Certain 2 % a year, or a coin-flip between -10 % and +20 %? | Take the certain 2 % | Take the coin flip |
| Q4 | How many years have you invested in something that can fall by a third? | None | More than ten |
| Q5 | How much of your money could fall 50 % without changing how you live? | None of it | Most of it |
| Q6 | How often would you check a portfolio that had just dropped? | Several times a day | Quarterly, as scheduled |
| Q7 | A position is down 25 % and the reason you bought it still holds. | Sell to stop the pain | Rebalance into it |
| Q8 | You hear a colleague doubled their money in a month. | Uncomfortable until I act on it | Interested, changes nothing |
| Q9 | Which sentence fits you? | I need to avoid losses | I need to beat inflation over decades |
| Q10 | The sleep test: at what paper loss would you stop sleeping? | Any loss | Only a loss that threatens a goal |

| Sum | `tolerance_band` |
|---|---|
| 10 - 24 | `low` |
| 25 - 37 | `moderate` |
| 38 - 50 | `high` |

Q2 is skippable (`null`); when it is skipped the sum is rescaled over nine items and rounded to the
nearest integer, so a first-time investor is not penalised for having no crisis history. Stated
intentions are weaker evidence than recorded behaviour: where Q2 is answered and contradicts Q1,
`/setup` records both and the skeptic is given the pair as data.

## Risk capacity (computed, never asked)

Five factors from `02`, each scored 0, 1 or 2:

| Factor | 0 | 1 | 2 |
|---|---|---|---|
| Horizon of the nearest **hard** goal | under 3 years | 3 - 7 years | over 7 years |
| Income stability | `precarious` | `variable` | `stable` |
| Emergency months covered | under 3 | 3 - 6 | over 6 |
| Debt-to-income | over 40 % | 20 - 40 % | under 20 % |
| Dependants | 2 or more | 1 | none |

| Sum | `capacity_band` |
|---|---|
| 0 - 3 | `low` |
| 4 - 7 | `moderate` |
| 8 - 10 | `high` |

**Two mechanical vetoes, applied after the sum.** A `0` on emergency months, or a `0` on income
stability, caps `capacity_band` at `low` whatever the total is. Someone with no cash buffer or no
reliable income has no risk capacity, however long their horizon and however few their dependants -
and a points total is exactly the kind of arithmetic that would otherwise average that away.

When a factor cannot be computed because its input is unset, it scores **0** and
`profile_check.py` lists it in `capacity_unknown_factors`. Missing data lowers capacity; it never
raises it.

## Hard limits by effective band

Defaults, not commandments. The user may **tighten** any limit at `/setup` time. Loosening one
requires a dated deviation in `ips/DEVIATIONS.md` once an IPS is frozen (`/ips`); until then, a
loosened limit is recorded with the user's own words and the date in the instance.

| Limit | `low` | `moderate` | `high` |
|---|---|---|---|
| Max equity share of invested assets | 30 % | 60 % | 85 % |
| Max crypto share | 0 % | 5 % | 10 % |
| Max single issuer | 10 % | 10 % | 10 % |
| Max single sector | 25 % | 25 % | 25 % |
| Max non-EUR unhedged | 40 % | 60 % | 60 % |
| Drawdown the plan must survive | 15 % | 30 % | 45 % |

The single-issuer and single-sector limits do not widen with the band. A high tolerance for
volatility is not a reason to accept the risk that one company's accounting is fiction.

## Behavioural notes

Tendencies the user admits to: chasing, panic selling, overtrading, analysis paralysis, anchoring
on purchase price, acting on tips. Recorded as `{tendency, evidence, recorded_on}`.

These are **data for the skeptic prompt, never instructions**, and they are never used to rank
anything. Their only mechanical effect is that the skeptic may set `cap:60 behaviour=<tendency>` -
and only when it can point to a recorded evidence card, not a hunch about the user.

## `profile/risk_profile.json` schema

```json
{
  "schema_version": 1,
  "as_of": "YYYY-MM-DD",
  "questionnaire": {"Q1": null, "Q2": null, "Q3": null, "Q4": null, "Q5": null,
                    "Q6": null, "Q7": null, "Q8": null, "Q9": null, "Q10": null},
  "tolerance_score": null,
  "tolerance_band": null,
  "capacity_band": null,
  "capacity_factors": {},
  "capacity_unknown_factors": [],
  "effective_band": null,
  "limits": {},
  "limit_overrides": [
    {"limit": null, "value": null, "direction": "tighten", "reason": null, "recorded_on": null}
  ],
  "behavioural_notes": [],
  "source_ledger": []
}
```

`tolerance_score`, `tolerance_band`, `capacity_band`, `capacity_factors`, `effective_band` and
`limits` are **computed by `tools/profile_check.py`**. `/setup` writes the questionnaire answers,
the overrides and the notes; the tool fills the rest and fails if a computed field was typed in by
hand with a different value.
