---
framework_version: 0.2.0
---

# Balance Sheet and Cash Flow

Library file - reached only by an explicit Read from a command; written by `/setup`;
the `assets` and `holdings` arrays are also refreshed by `/track snapshot`, and by nothing else.

This is the data the gates score against. It is a **schema**: the instance is
`profile/balance_sheet.json`, ignored by git.

## Standing rules

1. **Bare numbers, EUR, with an `as_of` date.** No ranges, no "about", no formatted strings.
   A non-EUR figure keeps its own currency and is converted by `tools/fx.py` using a *recorded*
   ECB reference rate that carries an `EV-` key. Nothing converts at a remembered rate.
2. **Staleness is mechanical.** A figure whose `as_of` is more than **90 days** old is `stale`.
   A decision that uses a stale figure gets a Gate 1 FLAG until it is refreshed. This is why
   `as_of` is per row, not per file: one forgotten pension statement should not make the whole
   balance sheet suspect, and it should not be invisible either.
3. **Derived numbers are computed, never typed.** Everything in the Derived section below is
   produced by `tools/profile_check.py` from the rows. A derived number written by hand into the
   instance is an error the tool reports.
4. **No identifiers.** Institution and account *type*, never an account number or IBAN.
5. **Every material number carries a source-ledger row** (`covers` paths, per
   `01-financial-profile.md`).

## Liquidity tiers

| Tier | Meaning | Counts toward the emergency fund |
|---|---|---|
| `L0` | Same day, no loss of principal | yes |
| `L1` | Within a week, no material loss | yes |
| `L2` | Weeks to a year, or a loss on early exit | no |
| `L3` | Over a year, or effectively illiquid (property, locked pension) | no |

The emergency fund is L0 + L1 only. A stock portfolio is liquid in the market-microstructure sense
and is *not* an emergency fund: the month you need it is disproportionately likely to be a month it
is down.

## `profile/balance_sheet.json` schema

```json
{
  "schema_version": 1,
  "as_of": "YYYY-MM-DD",
  "cash_flow": {
    "monthly_net_income": null,
    "monthly_essential_expenses": null,
    "monthly_discretionary": null,
    "income_stability": null,
    "emergency_fund_target_months": null,
    "as_of": "YYYY-MM-DD"
  },
  "assets": [
    {
      "account": null, "institution": null, "type": null, "liquidity_tier": null,
      "value_eur": null, "currency": "EUR", "custody_regime": null,
      "as_of": "YYYY-MM-DD", "notes": null
    }
  ],
  "holdings": [
    {
      "isin_or_symbol": null, "name": null, "asset_class": null, "units": null,
      "avg_cost_eur": null, "value_eur": null, "account": null, "issuer": null,
      "sector": null, "domicile": null, "currency": "EUR", "ter_pct": null,
      "distribution_policy": null, "as_of": "YYYY-MM-DD"
    }
  ],
  "liabilities": [
    {
      "type": null, "lender": null, "balance_eur": null, "rate_pct": null,
      "remaining_term_months": null, "monthly_payment_eur": null,
      "prepayment_penalty": null, "tax_deductible": null, "as_of": "YYYY-MM-DD"
    }
  ],
  "pension": {
    "public_pension_estimate_eur_year": null, "public_pension_source": null,
    "fondo_pensione_balance_eur": null, "fondo_pensione_annual_contribution_eur": null,
    "employer_match_pct": null, "tfr_destination": null, "as_of": "YYYY-MM-DD"
  },
  "insurance": {
    "health": "unknown", "life": "unknown", "disability": "unknown",
    "home": "unknown", "liability": "unknown"
  },
  "source_ledger": []
}
```

### Vocabularies

| Field | Allowed values |
|---|---|
| `assets[].type` | `cash`, `deposit`, `brokerage`, `pension`, `crypto`, `real_estate`, `other` |
| `assets[].liquidity_tier` | `L0`, `L1`, `L2`, `L3` |
| `assets[].custody_regime` | `amministrato`, `dichiarativo`, `not_applicable`, `null` (unknown - Gate 3 FLAGs) |
| `holdings[].asset_class` | `equity`, `bond`, `cash`, `crypto`, `commodity`, `real_estate`, `multi_asset` |
| `holdings[].distribution_policy` | `accumulating`, `distributing` |
| `liabilities[].type` | `mortgage`, `personal_loan`, `car_loan`, `student_loan`, `credit_card`, `family_loan`, `other` |
| `pension.tfr_destination` | `company`, `fondo_pensione`, `mixed`, `not_applicable` |
| `insurance.*` | `present`, `absent`, `unknown` |

`insurance` defaults to `unknown`, never to `absent`. "We did not ask" and "they have no disability
cover" are different facts, and only one of them should make a report recommend buying insurance.

## Derived numbers (computed by `tools/profile_check.py`)

| Name | Definition |
|---|---|
| `net_worth_eur` | sum of `assets[].value_eur` - sum of `liabilities[].balance_eur` |
| `liquid_assets_eur` | sum of `assets[].value_eur` where tier is `L0` or `L1` |
| `emergency_months_covered` | `liquid_assets_eur` / `monthly_essential_expenses` |
| `emergency_fund_gap_months` | `emergency_fund_target_months` - `emergency_months_covered`, floored at 0 |
| `monthly_savings_capacity_eur` | `monthly_net_income` - `monthly_essential_expenses` - `monthly_discretionary` |
| `savings_rate_pct` | `monthly_savings_capacity_eur` / `monthly_net_income` x 100 |
| `debt_service_ratio_pct` | sum of `liabilities[].monthly_payment_eur` / `monthly_net_income` x 100 |
| `debt_to_income_pct` | sum of `liabilities[].balance_eur` / (`monthly_net_income` x 12) x 100 |
| `allocation_pct` | share of invested value by `asset_class`; cash and deposit assets count as `cash` |
| `currency_exposure_pct` | share of invested value by `currency` |
| `largest_issuer_pct`, `largest_sector_pct`, `crypto_share_pct` | concentration measures, as a share of invested value |
| `invested_assets_eur` | sum of `holdings[].value_eur` plus asset rows of type `cash`/`deposit` |
| `stale_fields` | every path whose `as_of` is more than 90 days before the run date |

Two deliberate choices. **Concentration is measured against invested assets, not net worth** -
a mortgaged home would otherwise mask every concentration in the portfolio. **The emergency fund
is measured against essential expenses, not total spending** - the discretionary half of a budget
is exactly what stops in the month an emergency fund is needed.

`emergency_fund_target_months` is set by the user. The rubric's *suggestion* is 3-6 months for
stable income and 6-12 for variable or precarious; a suggestion is shown, never auto-filled, and
`profile_check.py` leaves the field `null` if the user has not answered.

## Net-worth band labels

The only form in which net worth may appear in the tracked `CLAUDE.md` summary. A band expressed
as a currency range is still an amount, so the summary carries the label alone.

| Label | Net worth (EUR) |
|---|---|
| `B1` | below 25 000 |
| `B2` | 25 000 - 99 999 |
| `B3` | 100 000 - 249 999 |
| `B4` | 250 000 - 499 999 |
| `B5` | 500 000 - 999 999 |
| `B6` | 1 000 000 and above |

A negative net worth is `B1`, and `profile_check.py` reports it explicitly rather than hiding it
inside the lowest band: it changes which gate matters first.

## The `/setup` question batch (group F, G, H)

**F. Cash flow** - monthly net income; monthly essential expenses (housing, utilities, food,
transport, insurance, minimum debt payments); monthly discretionary; how stable the income is;
how many months of essential expenses they want in reserve.

**G. Assets, holdings and liabilities** - accounts by institution and type; for each, value and
the date that value is from; holdings (best read from a broker export in `documents/`); every debt
with balance, rate, remaining term, monthly payment, whether early repayment carries a penalty, and
whether the interest is deductible.

**H. Pension and insurance** - INPS estimate if known and where it came from; fondo pensione
balance, annual contribution, employer match, TFR destination; which of the five insurance types
are present, absent or unknown.

## What `/track snapshot` may touch

`assets[].value_eur`, `assets[].as_of`, `holdings[]` rows and their `as_of` - nothing else. It may
not edit cash flow, liabilities, pension, insurance or the ledger. Those change when the user's
life changes, which is a `/setup --refresh` event, not a market-value refresh.
