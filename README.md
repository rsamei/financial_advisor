# Financial Advisor

A local, file-based decision system for one household's money. It builds a financial profile,
pulls dated market and macro evidence from free public sources, and vets money decisions against
mechanical gates, the household's own balance sheet, and a written investment policy. Allocation,
debt paydown, cash placement, pension, insurance, large purchases.

It runs as [Claude Code](https://claude.com/claude-code) slash commands over plain JSON, CSV and
Markdown files. No server, no database, no account anywhere. Everything that quotes an amount
stays on your machine and is gitignored.

## Honest limits

These are the design, so read them first.

- **This is not licensed financial advice.** It is a structured second opinion that shows its
  working. Every report says so.
- **It executes nothing.** No order is placed, no broker is logged into, no money moves, nothing
  is filed. It drafts; you act.
- **It is not a forecaster.** Where it states an opinion, which it calls a scenario view, that
  opinion is labelled, quarantined from every gate, and scored against reality later.
- **It refuses material non-public information.** A tip that looks like MNPI is refused, logged,
  and never enters a decision. Legally published insider-signal data (SEC Form 4, 13F,
  congressional disclosures, short interest) is fair game and moves only the evidence dimension.
- **Missing evidence stays missing.** A claim without three independent, fresh, successful sources
  is `UNDETERMINED`, which caps a decision. It never rounds up to a yes.
- **Jurisdiction: Italy, EUR** for v1. Other jurisdictions are added as overlays, and no tax rule
  is used until it carries an official source URL and a verification date.

## Build status

The core workflows and deterministic tools are implemented and covered by the test suite.
Live-source reliability and Italian tax verification are the two areas still incomplete. The
jurisdiction overlay mechanism is a stub, so only Italy is populated.

## Requirements

- **Python 3.10 or newer.** That is the whole runtime. Every tool and provider uses the standard
  library only, on purpose, so that no third-party package sits between a provider response and a
  verdict.
- **git**, for the tracked-file and ignore-rule checks.
- **Claude Code**, to run the slash commands. The tools under `tools/` are ordinary CLI programs
  and work without it, but the workflows that tie them together are the commands.
- Development only: `pip install -r requirements-dev.txt` (pytest). The suite also runs under
  `python -m unittest`, which needs nothing installed.

## Quick start

```bash
git clone https://github.com/rsamei/financial_advisor
cd financial_advisor

# Check the repository is sound on your machine.
python -m unittest discover -s tests
python tools/lint_skills.py
python tools/security_guards.py
python tools/check_framework_version.py
```

All four should pass on a fresh clone. Watch `security_guards.py` in particular. It verifies that
the ignore rules protecting your financial data are still in force and that no personal file has
been staged.

Then open the repository in Claude Code and run `/setup`. It reads any statements you drop into
`documents/` and asks one batch of questions. Unknowns stay `_(unset)_` rather than being guessed.
After that, `/checkin`, or just asking "how am I doing?", is the everyday entry point.

See [SETUP.md](SETUP.md) for environment variables and the Windows TLS note, and
[SECURITY.md](SECURITY.md) for exactly what leaves your machine.

## Commands

| Command | What it does |
|---|---|
| `/checkin` (or just ask: "how am I doing?") | The everyday front door. Works out what is out of date, refreshes only that, runs the smallest workflow that answers your question and replies on one plain-language page, including whether each wealth goal is realistic. |
| `/setup` | Builds the financial profile, balance sheet and risk profile from your documents and a single batch of questions. Unknowns stay `_(unset)_`; nothing is invented. |
| `/market [scope]` | Refreshes market, macro, flow and insider-signal evidence with replayable provenance and a coverage table that names what failed. |
| `/advise` | The periodic full review: a prioritised action list where every action carries a gate record and source keys. |
| `/decide <question>` | Vets one money decision end to end, with a brief, the strongest alternative, and the decisive first check. |
| `/ips` | Writes, reviews and freezes the investment policy statement. Changing a limit requires a dated deviation. |
| `/track` | Records what you actually did, snapshots the portfolio, and scores past advice against a benchmark and against doing nothing. |
| `/watch` | Sets triggers. A trigger produces an alert and a draft row, never an action. |
| `/status`, `/resume`, `/reset` | Inspect or resume an interrupted workflow; reset local state through a previewed, approved, reversible move. |

## How it is laid out

```
.claude/commands/   The slash commands: one workflow file each.
.claude/skills/     Reference library. Numbered files are the rules a workflow applies.
providers/          One module per data source. Stdlib only, no shared state.
tools/              Deterministic CLI programs: scoring, portfolio maths, state, guards.
tests/              Unit and contract tests. Contract tests pin the rules, not the wording.
market/queries.json Tracked on purpose: provider arguments, so any market claim is replayable.
documents/          Where you drop statements. Gitignored except the README.
```

`profile/`, `decisions/`, `advice/`, `ips/`, `track/`, `watch/`, `views/`, `compliance/` and
`decision_tracker.csv` all quote amounts, so they are gitignored and created on first run. A fresh
clone has none of them.

## Extending it

**Add a data source.** Write a module in `providers/` following the contract at the top of
[`providers/base.py`](providers/base.py). Five rules matter there. A failed fetch raises rather
than returning an empty list. `null` means unknown while `""` means known-empty. No provider
follows a URL found inside fetched content. `operator` identifies who actually runs the service,
so two mirrors of one feed count once toward independence. Secrets come from the environment and
never appear in argv or in any record. Then add a case to `tests/test_providers.py`.

**Add a jurisdiction.** Copy `.claude/skills/financial-advisor-jurisdiction-template/` and
populate it the way `07-jurisdiction-italy.md` is populated. No tax rule is accepted without an
official source URL and a verification date, and the linter enforces the frontmatter.

**Change a rule.** The numbered files under `.claude/skills/financial-advisor/` are the rulebook.
They carry a `framework_version`, so bump it when you change one and
`tools/check_framework_version.py` will tell you if you forgot. Contract tests under `tests/` pin
behaviour rather than phrasing, so a reworded rule should not break them and a changed rule
should.

## Contributing

Issues and pull requests are welcome. Before opening a PR:

```bash
python -m unittest discover -s tests
python tools/lint_skills.py
python tools/security_guards.py
python tools/check_framework_version.py
```

All four must pass. Two rules override convenience, and a PR that breaks either will not be
merged:

1. **Nothing that quotes a real amount is ever committed.** If you add a directory that can hold
   one, add it to `.gitignore` and to `REQUIRED_IGNORE_RULES` in `tools/security_guards.py` in the
   same commit.
2. **Nothing here executes a money decision.** It drafts, the user acts.
   `tests/test_no_execution.py` exists to keep that true.

Keep the runtime standard-library only. If you genuinely need a dependency, say why in the issue
first.

## Licence

[MIT](LICENSE).

## Disclaimer

This software is provided for informational and educational purposes only. It is not financial,
investment, tax or legal advice, and its author is not a licensed financial adviser. Nothing it
produces is a recommendation to buy or sell any security. Verify anything it tells you against a
qualified professional before acting on it, and see the warranty disclaimer in [LICENSE](LICENSE).
