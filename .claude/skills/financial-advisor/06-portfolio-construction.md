---
framework_version: 0.2.0
---

# Portfolio Construction

Library file - reached only by an explicit Read from a command; used by `/advise`, `/decide` and
`/ips`.

How a portfolio is put together once a decision has passed its gates. Every number here is a
**starting point calibrated to long-run evidence, not a prediction**, and every one of them can be
tightened by the user. `tools/portfolio_math.py` implements the arithmetic; this file states the
policy it serves.

## Allocation frameworks by effective band

Ranges, not points. A portfolio sitting anywhere inside its range needs no action.

| Effective band | Equity | Bonds | Cash | Optional satellites |
|---|---|---|---|---|
| `low` | 20-30 % | 40-55 % | 20-35 % | none |
| `moderate` | 50-65 % | 25-40 % | 5-15 % | up to 5 % crypto, only if the user asks |
| `high` | 70-85 % | 10-25 % | 3-10 % | up to 10 % crypto, only if the user asks |

The band comes from `03-risk-profile.md` and is `min(tolerance, capacity)`. **The emergency fund
sits outside this table**: it is not an allocation decision, it is the precondition for having one.

Crypto is never proposed by this system. It appears only when the user asks for it, sized inside
the band's limit, and 08's chasing rule applies to it with particular force.

## Rebalancing

| Rule | Value | Why |
|---|---|---|
| Absolute band, classes at or above 10 % target | ±5 pp | Below that, the trade costs more than the drift |
| Relative band, classes below 10 % target | ±25 % of target | 5 pp on a 2 % sleeve is a 250 % move |
| Review cadence | At `/advise`, and on a watch trigger | Calendar rebalancing on a schedule nobody keeps is not a rule |
| Cash-flow first | Direct new contributions to the underweight class before selling anything | A sale is a taxable event in Italy; a contribution is not |

`tools/portfolio_math.py rebalance` proposes nothing when every class is inside its band, and says
so. Trading because a number moved is a cost, not a strategy.

## Position sizing

- A single position is capped by the issuer limit in `03` (10 % of invested assets at every band).
- A new position below **2 % of invested assets** is usually not worth holding: it cannot move the
  portfolio, and it will still need watching, rebalancing and tax paperwork for years.
- Anything above **10 % of investable assets in one decision** triggers the cooling-off rule in
  `08` and is banded at most `park` for 48 hours.

## Instrument selection for an Italian retail investor

| Rule | Reason |
|---|---|
| **UCITS only** | A US-domiciled ETF has no PRIIPs KID, so an EU retail investor cannot buy it. Gate 3 FAILs on it |
| **Accumulating preferred in regime amministrato** | A distributing fund forces a taxable event on each distribution with no reinvestment choice. Confirm against 07 before relying on the treatment |
| **Irish domicile for US equity exposure** | The Ireland-US treaty reduces withholding on US dividends relative to other common domiciles. The rate belongs in 07 with its source, not here |
| **TER ceilings** (starting points) | broad developed equity 0.25 %, broad euro government bonds 0.20 %, money market 0.20 %, single-country or thematic 0.45 % |
| **Physical replication preferred** | Synthetic replication introduces a counterparty; acceptable where it is materially cheaper and the swap counterparty is named |
| **Fund size and age** | Below roughly 100 M EUR or under 3 years old, closure and merger risk is real - not fatal, but a reason to prefer the alternative |

None of these is a tax fact until the corresponding row in `07-jurisdiction-italy.md` carries an
official source and a `verified_on` date. Until then Gate 3 FLAGs and the report says the rule is
unverified.

## Cash placement ladder

Money is placed by **when it is needed**, not by what it could earn.

| Horizon | Instrument | Note |
|---|---|---|
| Now, must not move | Current account up to the deposit guarantee per bank | Convenience beats yield for the first tier |
| 0-12 months | Conto deposito (svincolabile) or a money-market UCITS | Check the bollo and any lock-up before comparing to a headline rate |
| 1-3 years | BOT or short BTP held to maturity; money-market UCITS | Held to maturity, price volatility does not matter; sold early, it does |
| 3 years+ | This is no longer cash. It belongs in the allocation above | |

## Debt versus investing

Compare like with like, after tax:

```
after-tax debt rate  =  nominal rate x (1 - deductible share)
after-tax expected return  =  evidence-based expected return x (1 - capital gains rate from 07)
```

Repay the debt when the after-tax debt rate is at or above the after-tax expected return.
**High-interest debt (proposal: above 6 % nominal) is repaid before any investing**, and Gate 1
FLAGs a decision that invests while it is outstanding.

Two things this arithmetic does not capture, and which are stated in every report that uses it:
repaying debt is a **certain** return while an expected return is not, and a mortgage on a home the
user lives in is not just a liability.

## Staging: lump sum or spread

The evidence is that lump-sum investing beats spreading, on average, because markets rise more
often than they fall. The evidence is also that a person who invests a lump sum the month before a
30 % drawdown may never invest again.

- Default to **lump sum** for amounts under 10 % of invested assets.
- Above that, offer both, with the trade-off stated in one sentence: spreading costs expected
  return and buys behavioural insurance.
- `do_scoped` (58-71) **requires** staging over at least three tranches, per `04`.
- A staging plan names its dates in advance. "When it feels right" is not a plan.

## Expected returns

Every expected-return input must come from a **recorded observation** with the series named - a
current yield, a valuation ratio, a long-run realised series and its window. Never from model
memory, and never a round number someone remembers.

A bond's expected return starts from its yield to maturity. An equity expected return is quoted as
a range with its method named. If no recorded series supports the number, the decision's evidence
dimension takes the hit and Gate 4 caps it at 55 - which is the system working, not failing.
