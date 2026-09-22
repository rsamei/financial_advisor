---
name: market-scout
description: "Stateless lookup of a price, a macro series, an exchange rate or a public filing, with the source and its date named. Answers a question; it cannot produce a verdict, a recommendation or a decision. Triggers on: what is the current, look up the price of, latest inflation print, policy rate, exchange rate, find the filing, quick market question."
allowed-tools: Read, Glob, Grep
framework_version: 0.1.1
---

# Market Scout

A quick lookup, nothing more. Use this when the user asks what something *is*, not what to *do*.

What a scout answer always carries: the value, its `as_of` date, the provider that published it,
and whether the figure is an observation or a forecast.

What a scout answer never does:

- produce a verdict, a band, a score, a recommendation or a "should I".
- write any state. Nothing here touches `market/`, `profile/`, the tracker or the ledger. A figure
  looked up this way has no `EV-` key and therefore cannot support any gate.
- satisfy a coverage condition. One source is one source; coverage is decided in `/market` under
  `05-market-intelligence.md`.

Anything that moves money goes through `/decide` or `/advise`, which run the gates against the
user's actual balance sheet. If the user asks a scout question and then asks what to do with the
answer, say so and hand off.

## Build status

Where no provider covers a source, a lookup answers with what the user or a document already
states, or says that the source is not wired up yet.
