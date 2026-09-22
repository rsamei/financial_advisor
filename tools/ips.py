#!/usr/bin/env python3
"""The investment policy statement: draft it, freeze it, and refuse to let it change quietly.

Usage (from the repository root):
    python tools/ips.py init     [--force]
    python tools/ips.py status
    python tools/ips.py check
    python tools/ips.py freeze   --approval "<the user's literal yes, quoted>"
    python tools/ips.py deviate  --section "Target allocation" --before "..." --after "..." \
                                 --reason "..." --approval "<the user's literal yes>"

`freeze` and `deviate` are deliberately **absent from the permission allowlist**. Every other tool
here can run unattended; these two cannot, because what they record is the user's own approval of
their own policy. A pre-approved freeze would mean the model approving a policy on the user's
behalf, which is precisely the thing an IPS exists to prevent.

What this tool enforces that prose cannot:

- **A frozen IPS is pinned to a SHA-256.** Any edit to `ips/IPS.md` - one character - makes `check`
  fail. The policy cannot drift by being quietly rewritten between decisions, which is how
  portfolios actually drift.
- **A limit changes only through a dated deviation.** `deviate` records what it was, what it
  became, why, and the user's literal words approving it, bumps the version and re-freezes. There
  is no other path.
- **The approval is quoted verbatim**, not paraphrased, and an empty or model-authored approval is
  refused. "The user agreed" is not a record of consent.
- **Nothing is ever deleted.** `ips/DEVIATIONS.md` only grows, and `/reset`'s `ips` scope moves
  files to the trash rather than removing them.

Stdlib only. Exit 0 ok; 1 state or schema error; 4 bad arguments; 7 freeze blocked.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fa_state import (StateError, atomic_write_text, file_sha256, now_utc, read_json,  # noqa: E402
                      safe_console, today, write_json)

IPS_DIR = Path('ips')
IPS_FILE = IPS_DIR / 'IPS.md'
LOCK_FILE = IPS_DIR / 'IPS.lock.json'
DEVIATIONS_FILE = IPS_DIR / 'DEVIATIONS.md'
FREEZE_BLOCKED = 7

# The sections an IPS must have (plan section 5.5). A missing section is a policy question the user
# has not answered, and it will be re-argued at every decision until they do.
SECTIONS = (
    'Purpose and goals',
    'Effective risk band and hard limits',
    'Target allocation and rebalance bands',
    'Instrument universe and exclusions',
    'Cash and emergency policy',
    'Debt policy',
    'Contribution schedule',
    'Review cadence',
    'What triggers a deviation',
)
DEVIATION_ID = re.compile(r'DV-\d{3}')
# An approval must be the user's own words. These are what a model writes when nobody said yes.
HOLLOW_APPROVALS = ('yes', 'ok', 'approved', 'confirmed', 'the user agreed', 'user approved',
                    'agreed', 'sure', 'lgtm', 'proceed')

TEMPLATE = """# Investment Policy Statement

Version {version} - drafted {date}. **Not frozen yet**: run `python tools/ips.py freeze` once the
sections below say what you actually intend. Until it is frozen, every decision carries
`cap:65 ips=missing`.

## Purpose and goals

_(unset)_ - list the goals from `profile/profile.json` by `goal_id`, with their dates and whether
each date is hard. A policy that does not name what the money is for cannot judge anything.

## Effective risk band and hard limits

_(unset)_ - copy the effective band and the limits computed by `tools/profile_check.py derive`.
Tighten anything you want. Loosening a limit after this is frozen requires a deviation.

## Target allocation and rebalance bands

_(unset)_ - target percentages by asset class, with the bands from
`06-portfolio-construction.md` (absolute +/-5 pp at or above a 10 % target, relative 25 % below).

## Instrument universe and exclusions

_(unset)_ - what you will hold (UCITS, accumulating, domicile) and what you refuse outright.

## Cash and emergency policy

_(unset)_ - months of essential expenses held, where that money sits, and what it is for.

## Debt policy

_(unset)_ - the rate above which debt is repaid before investing, and how prepayment is decided.

## Contribution schedule

_(unset)_ - monthly or annual contributions, pension and TFR destination, and what happens to a
bonus or a windfall.

## Review cadence

_(unset)_ - how often `/advise` runs, and what happens if a review is missed.

## What triggers a deviation

_(unset)_ - the specific circumstances in which this policy may be changed, so that "I changed my
mind in a drawdown" is visibly not one of them.

---
This is not licensed financial advice. Nothing here has been executed; you decide and you act.
"""


def paths(root: Path) -> tuple[Path, Path, Path]:
    root = Path(root)
    return root / IPS_FILE, root / LOCK_FILE, root / DEVIATIONS_FILE


def read_lock(root: Path) -> dict | None:
    _, lock_path, _ = paths(root)
    return read_json(lock_path, 'ips/IPS.lock.json') if lock_path.is_file() else None


def missing_sections(text: str) -> list[str]:
    return [section for section in SECTIONS if f'## {section}' not in text]


def unset_sections(text: str) -> list[str]:
    """Sections still carrying the placeholder. Freezing those would freeze nothing."""
    found = []
    for section in SECTIONS:
        marker = f'## {section}'
        if marker not in text:
            continue
        start = text.index(marker)
        end = text.find('\n## ', start + 1)
        if '_(unset)_' in text[start:end if end != -1 else len(text)]:
            found.append(section)
    return found


def init(root: Path, force: bool = False) -> Path:
    ips_path, lock_path, deviations_path = paths(root)
    if ips_path.is_file() and not force:
        raise StateError(f'{IPS_FILE} already exists; pass --force to overwrite the draft '
                         '(a frozen IPS should be changed with `deviate`, not overwritten)')
    if lock_path.is_file() and not force:
        raise StateError('this IPS is frozen. Change it with `deviate`, which records what '
                         'changed, why, and your approval - not by rewriting the file.')
    atomic_write_text(ips_path, TEMPLATE.format(version=1, date=today()))
    if not deviations_path.is_file():
        atomic_write_text(deviations_path,
                          '# Deviations\n\nEvery change to a frozen IPS, in order. This file only '
                          'grows.\n\n| id | date | section | before | after | reason | approval |\n'
                          '|---|---|---|---|---|---|---|\n')
    return ips_path


def status(root: Path) -> dict:
    ips_path, _, _ = paths(root)
    if not ips_path.is_file():
        return {'exists': False, 'frozen': False,
                'note': 'no IPS. Every decision carries cap:65 ips=missing until one is frozen.'}
    text = ips_path.read_text(encoding='utf-8')
    lock = read_lock(root)
    return {
        'exists': True,
        'frozen': bool(lock),
        'version': lock.get('version') if lock else None,
        'frozen_on': lock.get('frozen_on') if lock else None,
        'missing_sections': missing_sections(text),
        'unset_sections': unset_sections(text),
    }


def check(root: Path) -> dict:
    """Is the frozen text still the text on disk? Anything else is a silent policy change."""
    ips_path, lock_path, _ = paths(root)
    if not ips_path.is_file():
        return {'ok': False, 'state': 'absent',
                'problems': ['no ips/IPS.md; decisions carry cap:65 ips=missing']}
    lock = read_lock(root)
    if not lock:
        return {'ok': False, 'state': 'unfrozen',
                'problems': ['ips/IPS.md exists but is not frozen; run `ips.py freeze`']}
    current = file_sha256(ips_path)
    if current != lock.get('sha256'):
        return {
            'ok': False, 'state': 'modified', 'version': lock.get('version'),
            'expected_sha256': lock.get('sha256'), 'actual_sha256': current,
            'problems': [
                'ips/IPS.md has changed since it was frozen. A policy that can be edited between '
                'decisions is not a policy. Either restore the frozen text, or record the change '
                'with `ips.py deviate` so it carries a date, a reason and your approval.'],
        }
    return {'ok': True, 'state': 'frozen', 'version': lock.get('version'),
            'frozen_on': lock.get('frozen_on'), 'sha256': current, 'problems': []}


def _validate_approval(approval: str) -> str:
    text = (approval or '').strip()
    if len(text) < 12 or text.lower().strip('.!" ') in HOLLOW_APPROVALS:
        raise StateError(
            'the approval must be the user\'s own words, quoted - something only they would have '
            'written, in the conversation where they said it. "yes" or "the user agreed" is a '
            'summary written by the model, and this record exists precisely so that a summary is '
            'not enough.')
    return text


def freeze(root: Path, approval: str, *, allow_unset: bool = False) -> dict:
    ips_path, lock_path, _ = paths(root)
    if not ips_path.is_file():
        raise StateError(f'no {IPS_FILE}; run `ips.py init` first')
    text = ips_path.read_text(encoding='utf-8')
    missing = missing_sections(text)
    if missing:
        raise StateError('cannot freeze: missing section(s) ' + ', '.join(missing))
    unset = unset_sections(text)
    if unset and not allow_unset:
        raise StateError(
            'cannot freeze: still _(unset)_ - ' + ', '.join(unset) + '. Freezing a placeholder '
            'would pin a policy that says nothing, and every later decision would cite it.')
    approval = _validate_approval(approval)
    previous = read_lock(root)
    lock = {
        'version': (previous.get('version', 0) + 1) if previous else 1,
        'sha256': file_sha256(ips_path),
        'frozen_on': now_utc(),
        'approval': approval,
    }
    write_json(lock_path, lock)
    return lock


def next_deviation_id(text: str) -> str:
    numbers = [int(found[3:]) for found in DEVIATION_ID.findall(text)]
    return f'DV-{max(numbers, default=0) + 1:03d}'


def deviate(root: Path, *, section: str, before: str, after: str, reason: str,
            approval: str) -> dict:
    """Record a policy change, bump the version, re-freeze. The only way a frozen limit moves."""
    ips_path, lock_path, deviations_path = paths(root)
    lock = read_lock(root)
    if not lock:
        raise StateError('this IPS is not frozen, so there is nothing to deviate from. Edit the '
                         'draft and freeze it.')
    if section not in SECTIONS:
        raise StateError(f'unknown section {section!r}; expected one of: ' + ', '.join(SECTIONS))
    for field, value in (('before', before), ('after', after), ('reason', reason)):
        if not (value or '').strip():
            raise StateError(f'deviate: {field} is required')
    approval = _validate_approval(approval)

    existing = deviations_path.read_text(encoding='utf-8') if deviations_path.is_file() else (
        '# Deviations\n\nEvery change to a frozen IPS, in order. This file only grows.\n\n'
        '| id | date | section | before | after | reason | approval |\n|---|---|---|---|---|---|---|\n')
    deviation_id = next_deviation_id(existing)

    def cell(value: str) -> str:
        return value.replace('|', '\\|').replace('\n', ' ').strip()

    row = (f'| {deviation_id} | {today()} | {cell(section)} | {cell(before)} | {cell(after)} | '
           f'{cell(reason)} | {cell(approval)} |\n')
    # The deviation is recorded BEFORE the re-freeze, so an interruption leaves a recorded change
    # with a stale lock - which `check` reports - rather than a changed policy with no record.
    atomic_write_text(deviations_path, existing + row)
    new_lock = {
        'version': lock.get('version', 1) + 1,
        'sha256': file_sha256(ips_path),
        'frozen_on': now_utc(),
        'approval': approval,
        'deviation_id': deviation_id,
    }
    write_json(lock_path, new_lock)
    return {'deviation_id': deviation_id, 'lock': new_lock}


def drift(root: Path, targets: dict | None = None) -> dict:
    """Live allocation against the IPS targets, via portfolio_math. Reports; never trades."""
    import portfolio_math as pm
    balance = pm.load_balance(root)
    if targets is None:
        raise StateError('pass --targets with the IPS target allocation; this tool does not guess '
                         'what the policy says')
    return pm.drift(balance, targets)


def main(argv=None) -> int:
    safe_console()
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--root', type=Path, default=Path.cwd())
    parser.add_argument('--json', action='store_true')
    sub = parser.add_subparsers(dest='command', required=True)
    initer = sub.add_parser('init')
    initer.add_argument('--force', action='store_true')
    sub.add_parser('status')
    sub.add_parser('check')
    freezer = sub.add_parser('freeze')
    freezer.add_argument('--approval', required=True)
    freezer.add_argument('--allow-unset', action='store_true',
                         help='freeze with placeholders still present; for tests only')
    deviator = sub.add_parser('deviate')
    deviator.add_argument('--section', required=True)
    deviator.add_argument('--before', required=True)
    deviator.add_argument('--after', required=True)
    deviator.add_argument('--reason', required=True)
    deviator.add_argument('--approval', required=True)
    drifter = sub.add_parser('drift')
    drifter.add_argument('--targets', type=Path, required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == 'init':
            path = init(args.root, args.force)
            print(f'wrote {path.relative_to(Path(args.root))} (draft, not frozen)')
            print('fill the _(unset)_ sections, then freeze it with your own words as the approval')
            return 0

        if args.command == 'status':
            result = status(args.root)
            print(json.dumps(result, indent=2) if args.json else
                  ('no IPS' if not result['exists'] else
                   f"version {result['version']} | frozen: {result['frozen']} | "
                   f"unset: {', '.join(result['unset_sections']) or 'none'}"))
            return 0

        if args.command == 'check':
            result = check(args.root)
            if args.json:
                print(json.dumps(result, indent=2))
            else:
                print(f"ips: {result['state']}"
                      + (f" v{result.get('version')}" if result.get('version') else ''))
                for problem in result['problems']:
                    print(f'  - {problem}')
            return 0 if result['ok'] else 1

        if args.command == 'freeze':
            try:
                lock = freeze(args.root, args.approval, allow_unset=args.allow_unset)
            except StateError as exc:
                print(f'ips: {exc}', file=sys.stderr)
                return FREEZE_BLOCKED
            print(f"frozen: version {lock['version']}, sha256 {lock['sha256'][:16]}")
            print(f"approval recorded verbatim: {lock['approval'][:80]}")
            return 0

        if args.command == 'deviate':
            try:
                result = deviate(args.root, section=args.section, before=args.before,
                                 after=args.after, reason=args.reason, approval=args.approval)
            except StateError as exc:
                print(f'ips: {exc}', file=sys.stderr)
                return FREEZE_BLOCKED
            print(f"recorded {result['deviation_id']}; IPS re-frozen at version "
                  f"{result['lock']['version']}")
            return 0

        print(json.dumps(drift(args.root, read_json(args.targets, 'targets')), indent=2))
        return 0
    except StateError as exc:
        print(f'ips: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
