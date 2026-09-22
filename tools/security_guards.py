#!/usr/bin/env python3
"""Supply-chain and personal-data guards for this repo's riskiest surfaces.

Run from anywhere: python tools/security_guards.py

This repo ships pre-approved Claude Code permissions, and it holds one person's whole financial
position. These guards make the dangerous changes LOUD, not impossible: a change that genuinely
needs one of them must update the allowlists in this file in the same diff, so the widening is
explicit and reviewable rather than buried.

Checks:
1. .claude/settings.json - every permissions.allow entry must be in the exact allowlist below, and
   no entry may match a forbidden shape (a blanket Bash glob, curl, a bare `python -c`, a scraping
   CLI). Catches permission widening that would auto-approve commands without prompting. The same
   file's `hooks` key is held to its own allowlist, which is EMPTY by design.
2. .gitignore - the personal-data ignore rules must all still be present, and no un-allowlisted
   negation may re-include them.
3. CLAUDE.md's profile summary - a pointer, never a statement. It must contain no currency amount:
   net worth appears there as a band LABEL (B1..B6, defined in 02-balance-sheet-and-cashflow.md),
   never as a figure. CLAUDE.md is tracked; profile/ is not, and the summary is the one place where
   the two could leak into each other.
4. Tracked files - no key-shaped string (an API token pasted into a command file, a query log, a
   test fixture). Secrets are read from environment variables inside providers/ and never appear in
   argv, in market/queries.json, or anywhere git can see them.

Stdlib only, plus git on PATH for check 4. Exit 0 on success, 1 with a failure list otherwise.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
errors: list[str] = []

# The exact permission entries this repo ships. Adding an entry here and in settings.json in the
# same change is the review step - that is the point. The set grows as tools land; an entry for a
# tool that does not exist yet is not shipped.
#
# NOTE what is deliberately absent, and must stay absent:
#   tools/ips.py freeze|deviate   - freezing records the user's approval of their own policy; it
#                                   must reach the permission prompt, never be pre-approved.
#   tools/track_actions.py record - writes the hash-chained record of what the user really did.
#   tools/reset_repo.py apply     - moves state out of the working tree.
#   tools/market_cache.py         - deletes cached responses.
# Also absent: anything that fetches. Provider calls go through tools/market_retrieve.py, which
# wraps a hard-coded provider registry; a bare curl or a scraping CLI would bypass the provenance
# record that makes a market claim replayable.
ALLOWED_PERMISSIONS = {
    "Skill(financial-advisor)",
    # Validates profile/ and computes the derived numbers. Its only writes are inside profile/,
    # which /setup owns, and they are computed values - it cannot invent or alter a stated fact.
    "Bash(python tools/profile_check.py:*)",
    "Bash(python3 tools/profile_check.py:*)",
    # Converts using a rate /market already recorded. It never fetches and never invents a rate.
    "Bash(python tools/fx.py:*)",
    "Bash(python3 tools/fx.py:*)",
    # The controlled retrieval path: a hard-coded provider registry, a cache that cannot look
    # fresh, and a pull record in market/queries.json for every attempt including the failures.
    # Pre-approving this is what makes an unrecorded fetch (curl, a scraper) unnecessary.
    "Bash(python tools/market_retrieve.py:*)",
    "Bash(python3 tools/market_retrieve.py:*)",
    # Deduplicates the corpus and reports every fuzzy merge; it never retrieves.
    "Bash(python tools/market_merge.py:*)",
    "Bash(python3 tools/market_merge.py:*)",
    # Validates and stores immutable evidence cards. `add --url` re-fetches one URL to snapshot it.
    "Bash(python tools/evidence_memory.py:*)",
    "Bash(python3 tools/evidence_memory.py:*)",
    # Computes the verdict from a findings JSON. It decides nothing a human did not supply as
    # findings; it only refuses to let those findings mean something they do not.
    "Bash(python tools/decision_score.py:*)",
    "Bash(python3 tools/decision_score.py:*)",
    # Pure arithmetic over profile/balance_sheet.json. It writes nothing.
    "Bash(python tools/portfolio_math.py:*)",
    "Bash(python3 tools/portfolio_math.py:*)",
    # Appends and updates decision rows against a frozen header, with checked status transitions.
    "Bash(python tools/tracker.py:*)",
    "Bash(python3 tools/tracker.py:*)",
    # Read-only check of a finished report. It never edits the report it judges.
    "Bash(python tools/report_check.py:*)",
    "Bash(python3 tools/report_check.py:*)",
    # Screens a claim for MNPI and appends to the refusal log. Pre-approved on purpose: a guard
    # that needs a permission prompt is a guard that gets skipped, and skipping it is the failure.
    "Bash(python tools/compliance_guard.py:*)",
    "Bash(python3 tools/compliance_guard.py:*)",
    # Records triggers and evaluates them against recorded observations. An alert it writes is a
    # notification, never an instruction; the decision still goes through /decide.
    "Bash(python tools/watchlist.py:*)",
    "Bash(python3 tools/watchlist.py:*)",
    # Validates, records and scores scenario views. A view it writes can never enter a gate.
    "Bash(python tools/views.py:*)",
    "Bash(python3 tools/views.py:*)",
    # Writes operational checkpoints only; it never executes stored commands or grants approval.
    "Bash(python tools/workflow_state.py:*)",
    "Bash(python3 tools/workflow_state.py:*)",
    # Read-only repo checks.
    "Bash(python tools/lint_skills.py:*)",
    "Bash(python3 tools/lint_skills.py:*)",
    "Bash(python tools/security_guards.py:*)",
    "Bash(python3 tools/security_guards.py:*)",
    "Bash(python tools/check_framework_version.py:*)",
    "Bash(python3 tools/check_framework_version.py:*)",
    # The offline test suite. It touches synthetic fixtures only.
    "Bash(python -m unittest:*)",
    "Bash(python3 -m unittest:*)",
    # Read-only git inspection. No commit, no push, no checkout.
    "Bash(git status:*)",
    "Bash(git diff:*)",
    "Bash(git log:*)",
}

# Shapes that must never be pre-approved, whatever else the allowlist says. Checked separately from
# the exact allowlist so the failure message names the actual danger.
FORBIDDEN_PERMISSION_PATTERNS = [
    (re.compile(r"^Bash\(\*\)$|^Bash\([^)]*\*[^)]*\)$"), "a blanket Bash glob pre-approves arbitrary commands"),
    (re.compile(r"curl|wget|Invoke-WebRequest", re.I), "a raw fetcher bypasses the provider registry and its provenance record"),
    (re.compile(r"python3?\s+-c"), "`python -c` executes arbitrary code that no file records"),
    (re.compile(r"\bbdata\b|bright[-_]?data", re.I), "a scraping CLI bypasses the provider registry"),
    (re.compile(r"\bpip\b|npm|bun|uv\s+pip", re.I), "a package installer executes third-party code on install"),
]
# Entries in ALLOWED_PERMISSIONS that the glob rule would otherwise catch. Every shipped entry ends
# in `:*`, which is Claude Code's argument wildcard, not a path glob.
GLOB_EXEMPT = ALLOWED_PERMISSIONS

# Ignore rules that must never disappear from .gitignore (plan section 9).
REQUIRED_IGNORE_RULES = [
    # The user's financial position. The single most important block in this file: a committed
    # balance sheet is a data breach, not a mistake to fix in the next commit.
    "profile/**",
    "documents/**",
    "!documents/README.md",
    # Market corpus: large, and regenerable from market/queries.json (which stays tracked).
    "market/observations.json",
    "market/evidence/**",
    "market/snapshots/**",
    "market/exports/**",
    # Decisions, advice, policy, actions, alerts and opinions all quote real amounts.
    "decision_tracker.csv",
    "decisions/**",
    "advice/**",
    "ips/**",
    "track/**",
    "watch/**",
    "views/**",
    # Refusal log: it quotes the text that was refused, which is the tip itself.
    "compliance/**",
    # Disposable and operational.
    ".cache/**",
    ".state/**",
    ".reset-trash/",
    # Secrets.
    ".env",
    ".env.*",
]

# Negations this repo legitimately ships. .gitignore is order-sensitive: a later negation
# re-includes a path an earlier rule excluded, so a required rule can be physically present yet no
# longer take effect. Set membership on the required rules cannot see that, which is why negations
# get their own allowlist.
ALLOWED_IGNORE_NEGATIONS = {
    "!documents/README.md",
}

# Hook commands this repo legitimately ships, as "<Event>:<command>" strings.
# EMPTY BY DESIGN - this repo ships no hooks at all.
#
# A hook is strictly more dangerous than a permissions.allow entry. A permission pre-approves
# something Claude may choose to do; a hook runs unconditionally when its event fires, with no
# prompt and no model decision in between. Cloning a repo and opening it is enough.
ALLOWED_HOOKS: set[str] = set()

# A currency amount anywhere in the CLAUDE.md profile summary. Bands are labels (B1..B6), not
# figures, precisely so this regex can be strict.
AMOUNT_RE = re.compile(
    r"(?:[€$£]\s?\d)"                                   # euro 1.000
    r"|(?:\d[\d.,]*\s?(?:EUR|USD|GBP|eur|euro|k\b|K\b|mln\b))"   # 1.000 EUR, 184k
    r"|(?:\b(?:EUR|USD|GBP)\s?\d)",                     # EUR 1.000
    re.IGNORECASE)
SUMMARY_HEADING = "## Profile summary"

# Key-shaped strings: long random-looking tokens and the common provider prefixes.
SECRET_RES = [
    (re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}"), "an API key prefix"),
    (re.compile(r"\bghp_[A-Za-z0-9]{20,}"), "a GitHub token"),
    (re.compile(r"\b[A-Za-z0-9]{32}\b(?=.*(?i:fred|api[_-]?key|token))"), "a 32-character key next to a key word"),
    (re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*[\"']?[A-Za-z0-9_\-]{16,}"), "a key assignment"),
]
# Files whose job is to describe these patterns.
SECRET_SCAN_SKIP = {"tools/security_guards.py", "tests/test_security_guards.py", "SECURITY.md"}


def _hook_commands(event: str, entries: object):
    """Yield "<Event>:<command>" for every command a hook event would run.

    Fails closed: any shape this does not recognise yields a marker that cannot be in the
    allowlist, so an unfamiliar hook layout is rejected rather than silently skipped.
    """
    unrecognised = f"{event}:<unrecognised hook shape>"
    if not isinstance(entries, list):
        yield unrecognised
        return
    for entry in entries:
        if not isinstance(entry, dict):
            yield unrecognised
            continue
        inner = entry.get("hooks")
        if not isinstance(inner, list):
            yield unrecognised
            continue
        for hook in inner:
            command = hook.get("command") if isinstance(hook, dict) else None
            yield f"{event}:{command}" if isinstance(command, str) else unrecognised


def check_permissions() -> None:
    path = ROOT / ".claude" / "settings.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f".claude/settings.json: unreadable or invalid JSON: {exc}")
        return
    if not isinstance(data, dict):
        errors.append(".claude/settings.json: top-level JSON value must be an object")
        return

    # Checked before the permissions shape guards below, so a file that pairs a malformed
    # permissions block with a hook cannot return early and skip this.
    hooks = data.get("hooks", {})
    if hooks:
        if not isinstance(hooks, dict):
            errors.append(".claude/settings.json: hooks must be an object")
        else:
            for event, entries in hooks.items():
                for command in _hook_commands(str(event), entries):
                    if command not in ALLOWED_HOOKS:
                        errors.append(
                            f".claude/settings.json: hook not in the reviewed allowlist: "
                            f"{command!r}. A hook runs automatically when its event fires - it is "
                            "never gated by the permissions prompt, so it executes on every clone "
                            "without the user agreeing to anything. If this hook is intentional, "
                            "add it to ALLOWED_HOOKS in tools/security_guards.py in the same "
                            "change so the addition is explicit and reviewable."
                        )

    permissions = data.get("permissions", {})
    if not isinstance(permissions, dict):
        errors.append(".claude/settings.json: permissions must be an object")
        return
    allow = permissions.get("allow", [])
    if not isinstance(allow, list) or not all(isinstance(entry, str) for entry in allow):
        errors.append(".claude/settings.json: permissions.allow must be a list of strings")
        return
    for entry in allow:
        if entry not in ALLOWED_PERMISSIONS:
            errors.append(
                f".claude/settings.json: permission not in the reviewed allowlist: {entry!r}. "
                "Pre-approved permissions run without prompting. If this entry is intentional, add "
                "it to ALLOWED_PERMISSIONS in tools/security_guards.py in the same change so the "
                "widening is explicit and reviewable."
            )
        for pattern, why in FORBIDDEN_PERMISSION_PATTERNS:
            if pattern.search(entry) and entry not in GLOB_EXEMPT:
                errors.append(
                    f".claude/settings.json: forbidden permission shape {entry!r}: {why}."
                )
    for entry in ALLOWED_PERMISSIONS - set(allow):
        # Not an error: settings may legitimately drop an entry. But an allowlist entry that no
        # longer exists should be pruned.
        print(f"note: allowlisted permission not present in settings.json: {entry!r}")


def check_gitignore() -> None:
    path = ROOT / ".gitignore"
    try:
        lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    except OSError as exc:
        errors.append(f".gitignore: unreadable: {exc}")
        return
    rules = set(lines)
    for rule in REQUIRED_IGNORE_RULES:
        if rule not in rules:
            errors.append(
                f".gitignore: required personal-data rule missing: {rule!r}. These rules keep the "
                "user's balance sheet, decisions, policy and actions out of version control. If "
                "the rule moved or was renamed intentionally, update REQUIRED_IGNORE_RULES in "
                "tools/security_guards.py in the same change."
            )
    for line in lines:
        if line.startswith("!") and line not in ALLOWED_IGNORE_NEGATIONS:
            errors.append(
                f".gitignore: negation rule not in the reviewed allowlist: {line!r}. A negation "
                "re-includes a path an earlier rule excluded and can silently re-expose financial "
                "data (a required ignore rule stays present but stops taking effect). If this "
                "negation is intentional, add it to ALLOWED_IGNORE_NEGATIONS in "
                "tools/security_guards.py in the same change."
            )


def check_profile_summary() -> None:
    """The tracked profile summary in CLAUDE.md must name no amount.

    profile/ is ignored; CLAUDE.md is committed. The summary exists so a session knows a profile
    was populated and where the canonical facts live - not so it can quote them.
    """
    path = ROOT / "CLAUDE.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        errors.append(f"CLAUDE.md: unreadable: {exc}")
        return
    start = text.find(SUMMARY_HEADING)
    if start == -1:
        errors.append(
            f"CLAUDE.md: no {SUMMARY_HEADING!r} section. The section must exist (empty until "
            "/setup runs) so this guard has a defined region to check."
        )
        return
    end = text.find("\n## ", start + len(SUMMARY_HEADING))
    section = text[start:end if end != -1 else len(text)]
    for line_number, line in enumerate(section.splitlines(), start=1):
        match = AMOUNT_RE.search(line)
        if match:
            errors.append(
                f"CLAUDE.md profile summary, line {line_number}: currency amount {match.group(0)!r}. "
                "This section is tracked by git; profile/ is not. Net worth belongs here as a band "
                "label (B1..B6), and every other figure belongs in profile/balance_sheet.json."
            )


def _tracked_files() -> list[str]:
    if not (ROOT / ".git").exists():
        return []
    result = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True)
    if result.returncode != 0:
        errors.append("could not ask git which files are tracked; the secret scan did not run")
        return []
    return [name for name in result.stdout.decode("utf-8").split("\0") if name]


def check_tracked_secrets() -> None:
    for name in _tracked_files():
        if name in SECRET_SCAN_SKIP or not (ROOT / name).is_file():
            continue
        try:
            text = (ROOT / name).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for pattern, what in SECRET_RES:
            match = pattern.search(text)
            if match:
                errors.append(
                    f"{name}: looks like {what} ({match.group(0)[:12]}...). Secrets are read from "
                    "environment variables inside providers/ and never reach argv, "
                    "market/queries.json, or a tracked file. Remove it and rotate the key."
                )
                break


def main() -> int:
    check_permissions()
    check_gitignore()
    check_profile_summary()
    check_tracked_secrets()
    if errors:
        print(f"security_guards: {len(errors)} failure(s)")
        for err in errors:
            print(f"  - {err}")
        return 1
    print(
        "security_guards: OK (permissions allowlist, hooks allowlist, gitignore rules, "
        "profile summary, tracked-file secret scan)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
