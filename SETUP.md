# Setup

## Requirements

- **Python 3.10 or newer** on PATH. That is the whole runtime: every tool in `tools/` and every
  provider in `providers/` uses the standard library only.
- **git**, for the tracked-file checks in `tools/security_guards.py` and for
  `tools/check_framework_version.py`.
- Development only: `pip install -r requirements-dev.txt` (pytest). The suite also runs under
  `python -m unittest`, which needs nothing installed.

## Environment variables

Secrets are read inside `providers/` from the environment. They never appear in a command line, in
`market/queries.json`, or in any tracked file. Set them in your shell profile or a local `.env`
that git ignores, never in `.claude/settings.json`.

| Variable | Needed for | Required? |
|---|---|---|
| `FA_CONTACT_EMAIL` | SEC EDGAR. The SEC requires a contact address in the User-Agent of every request; without it, EDGAR-backed providers refuse to run rather than send an anonymous request. | Required before any SEC provider is used |
| `FRED_API_KEY` | FRED series. Optional: without it the provider falls back to the public CSV endpoint, which covers the series this repo uses. | Optional |
| `BRIGHTDATA_API_KEY` | The optional discovery-only Bright Data provider. Absent means that provider is simply not registered. | Optional |

ECB, Eurostat, Stooq, CoinGecko, the congressional-disclosure mirrors, FINRA short interest and
the RSS feeds need no key.

## TLS certificates (Windows)

Providers fetch over HTTPS and **always** verify the certificate. On some Windows Python
installations the trust store is incomplete, and a perfectly healthy source fails with
`CERTIFICATE_VERIFY_FAILED` - `providers/base.py` reports that as a local configuration problem
rather than as a source failure, because it is one.

If you see it, install a CA bundle:

```
pip install certifi
```

Nothing else is needed: when `SSL_CERT_FILE` and `SSL_CERT_DIR` are unset, `providers/base.py`
loads the `certifi` bundle on top of the system store by itself. `certifi` stays optional - the
repository still runs on the standard library alone - and setting `SSL_CERT_FILE` yourself still
takes precedence. Alternatively, let Windows Update refresh its root store. Verification is never disabled, and no flag in this
repository turns it off: an unverified fetch is an unauthenticated one, and the whole claim of this
system is that its evidence is what the source actually published.

## First run

Before running a command, check the repository is sound on your machine:

```
python -m unittest discover -s tests -v
python tools/lint_skills.py
python tools/security_guards.py
python tools/check_framework_version.py
```

All four must pass before any phase is called done. Once `/setup` ships, the first real run is:

```
/setup          # profile, balance sheet, risk profile - answer one batch of questions
/market         # evidence, with a coverage table that names what failed
/advise         # the prioritized action list
```

## Where your data lives

Everything personal stays in this directory and is ignored by git: `profile/`, `documents/`,
`decision_tracker.csv`, `decisions/`, `advice/`, `ips/`, `track/`, `watch/`, `views/`,
`compliance/`. Only `market/queries.json`, which holds provider arguments and no personal data or secrets, is
tracked, because it is what makes a market claim replayable. See [SECURITY.md](SECURITY.md).
