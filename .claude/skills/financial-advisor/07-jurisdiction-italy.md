---
framework_version: 0.2.0
---

# Jurisdiction Overlay: Italy

Library file - reached only by an explicit Read from a command; maintained by the developer from
official sources. Selected when `profile/profile.json` has `identity.tax_residency: "IT"`.

**Nothing in this file is a fact about Italian law until its row carries an official source URL and
a `verified_on` date.** A row marked `_(unset)_` is unverified: Gate 3 FLAGs any decision that
depends on it, and the report says the rule is unverified rather than quoting a number. That is the
correct behaviour, not a gap to paper over.

## How to verify a row, and the trap that is waiting

While populating this file on 2026-09-20, two official Agenzia delle Entrate pages were fetched for
the same question. One - an **archive** page for an old return - states the substitute tax is
`12,50 per cento`. The other, current, states `26 per cento` for gains realised from 1 July 2014.
Both are genuine agenziaentrate.gov.it URLs. A transcription from the first would have put a rate
less than half the real one into a file that decisions cite.

So, for every row:

1. Fetch the page with `tools/evidence_memory.py add --url`, which snapshots it. A page you did not
   snapshot is a page you cannot quote later.
2. **Quote the sentence that states the rule**, in Italian, in the Notes column. Not a paraphrase.
3. Check the page is **current**, not an archive: look for a year in the URL or title, an
   "Archivio" breadcrumb, and a reference to the tax year the rule applies from.
4. Record `verified_on`. A row older than a year is treated as unverified - tax law changes
   annually, and the finance act each December is where it changes.
5. Where the rule comes from primary law, cite Normattiva (the consolidated text) rather than a
   summary page.

An LLM's recollection of a tax rate is not a source, however confident it sounds. If this file and
the model disagree, this file wins; if this file is silent, the answer is "unverified", not the
model's memory.

## Rules

| id | Rule | Value | Applies to | Official source | Verified on | Notes |
|---|---|---|---|---|---|---|
| `IT-CG-01` | Substitute tax on capital gains and other financial income (redditi diversi) | **26 %** | Gains under art. 67(1)(c-bis) to (c-quinquies) TUIR realised from 1 July 2014 | https://infoprecompilata.agenziaentrate.gov.it/portale/semplificata-mod-plusvalenze-natura-finanziaria | 2026-09-20 | Quoted: "realizzate a decorrere dal 1° luglio 2014, per le quali è dovuta l'imposta sostitutiva nella misura del 26 per cento". Qualified holdings (lett. c) are treated separately; the same page lists a 20 % bucket for older gains. **This rate does not apply to white-list government bonds** - see `IT-CG-02`, which is unverified |
| `IT-CG-02` | Reduced rate on Italian and white-list government bonds | _(unset)_ | Titoli di Stato and equivalent | _(unset)_ | - | Widely cited as 12.5 %, and the archive page trap above is exactly this figure appearing for the wrong reason. Verify against the current AE page and the white list itself before use |
| `IT-CG-03` | Asymmetry between fund gains (redditi di capitale) and losses (redditi diversi), and the offset window | _(unset)_ | UCITS/ETF | _(unset)_ | - | The rule that makes ETF losses hard to offset against ETF gains. It changes what "tax-efficient" means in `06`, so it must be verified before any decision leans on it |
| `IT-BOLLO-01` | Imposta di bollo on securities accounts | _(unset)_ | Deposito titoli | _(unset)_ | - | Check both the rate and the minimum/maximum, which differ for individuals and legal entities |
| `IT-IVAFE-01` | IVAFE on financial assets held abroad | _(unset)_ | Foreign brokers and accounts | _(unset)_ | - | Interacts with the choice of broker; a foreign broker also implies `IT-RW-01` |
| `IT-IVIE-01` | IVIE on real estate held abroad | _(unset)_ | Foreign property | _(unset)_ | - | |
| `IT-REG-01` | Regime amministrato vs dichiarativo, and which brokers imply which | _(unset)_ | Custody choice | _(unset)_ | - | In regime amministrato the broker withholds; in dichiarativo the user files. Gate 3 FLAGs an unresolved custody regime because it changes who does the paperwork and when the tax falls due |
| `IT-RW-01` | Quadro RW / RT obligations for foreign brokers and crypto | _(unset)_ | Monitoraggio fiscale | _(unset)_ | - | An obligation, not a rate: getting it wrong is a penalty, not a cost |
| `IT-CRYPTO-01` | Taxation of crypto-assets, and any threshold | _(unset)_ | Cripto-attività | _(unset)_ | - | The rules changed with the 2023 finance act and again since; **do not rely on any remembered figure here**. Verify against the current AE guidance and the finance act in force |
| `IT-PENS-01` | Fondo pensione contribution deductibility ceiling | _(unset)_ | Previdenza complementare | _(unset)_ | - | Commonly cited as EUR 5,164.57. Verify: it is the single most decision-relevant number in this file for a working household, and the pension pass in `/advise` will lean on it |
| `IT-TFR-01` | TFR destination options and their treatment | _(unset)_ | Employees | _(unset)_ | - | |
| `IT-PIR-01` | PIR conditions and the holding period | _(unset)_ | PIR-compliant products | _(unset)_ | - | |
| `IT-PRIIPS-01` | PRIIPs/KID requirement for EU retail investors | _(unset)_ | US-domiciled ETFs | _(unset)_ | - | The basis of the Gate 3 FAIL on a US-domiciled ETF. The rule is EU-level (PRIIPs Regulation); cite EUR-Lex, not a broker's summary |
| `IT-COST-01` | Cost-basis method for partial disposals | _(unset)_ | All securities | _(unset)_ | - | `tools/portfolio_math.py` computes average cost (costo medio ponderato) because that is what brokers apply in regime amministrato. Verify, because the gain figure a report shows must match the user's statement |
| `IT-DEAD-01` | Annual deadlines: 730 / Redditi, acconti, bollo | _(unset)_ | Filing calendar | _(unset)_ | - | Gate 3 FLAGs an action interacting with a deadline within 30 days, so these dates need to be right and current |
| `IT-INPS-01` | Where the public pension estimate comes from | _(unset)_ | Retirement planning | _(unset)_ | - | INPS "La mia pensione futura". It is an estimate under stated assumptions, and the report must say so rather than treating it as a promise |

## How this overlay is used

- **Gate 3** cites rule ids **with their `verified_on` dates**. An `_(unset)_` row makes the gate
  FLAG (`cap:70 tax=FLAG`), never FAIL - an unverified rule is missing information, not a breach.
- **`06`'s instrument selection** (accumulating in regime amministrato, Irish domicile for US
  equity) states its *reasoning* but takes its *rates* from here. Until the relevant row is
  verified, those preferences are stated as conventions, not tax facts.
- **The compliance reviewer** lists every unverified row it had to rely on, under "Unverified rules
  I had to rely on". That list is the work queue for this file.
- **No tool applies a tax rate.** `portfolio_math.py lots` computes a gain and explicitly does not
  apply a rate, precisely because the rate lives here and here is mostly unverified.

## Adding a second jurisdiction

Copy `../financial-advisor-jurisdiction-template/`, rename it, and populate the same table shape.
An overlay adds specifics and **never loosens a base threshold**: if the base rubric caps crypto at
5 %, an overlay may be stricter and may never be looser.
`tests/test_overlay_contract.py` asserts both that rule and the source-and-date requirement.

## Status

**One row verified of sixteen** (2026-09-20). This file is the largest known gap in the system, and
it is the gap with the most direct consequences: Italian tax treatment is the difference between a
good instrument choice and an expensive one. Populating it is careful, slow work against primary
sources, and it is deliberately not being guessed at.
