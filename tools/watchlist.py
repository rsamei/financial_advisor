#!/usr/bin/env python3
"""Triggers the user asked to be told about - and alerts that are never instructions to act.

Usage (from the repository root):
    python tools/watchlist.py add    --subject SYN --metric price --op below --value 90 \
                                     --then "revisit D-004" [--decision D-004] [--expires 2027-01-01]
    python tools/watchlist.py list   [--all]
    python tools/watchlist.py remove --id W-001
    python tools/watchlist.py evaluate [--as-of YYYY-MM-DD] [--write-alerts]

What this tool enforces that prose cannot:

- **A trigger is a condition, not a plan.** Every entry carries the user's own `then` text, written
  when they were calm, and an alert quotes it back. An alert that arrived with fresh advice
  attached would be the system deciding in the moment - which is exactly what a watchlist exists to
  avoid.
- **A fired trigger produces an alert and a `draft` row. Never an action.** The draft row must go
  through `/decide` like anything else.
- **Evaluation reads recorded observations only.** A trigger on a price nobody recorded is reported
  as *not evaluated*, never as *not fired*. Those are different, and conflating them would mean a
  watchlist that silently stops watching.
- **A trigger expires.** An entry with no expiry is asked for one: a condition nobody will ever
  revisit is not a plan, it is a haunting.

Stdlib only. Exit 0 ok; 1 state or schema error; 4 bad arguments.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fa_state import StateError, read_json, safe_console, today, write_json  # noqa: E402

WATCHLIST = Path('watch') / 'watchlist.json'
ALERTS = Path('watch') / 'alerts'
OBSERVATIONS = Path('market') / 'observations.json'

WATCH_ID = re.compile(r'W-\d{3}')
METRICS = ('price', 'value', 'return_pct', 'level')
OPS = {'below': lambda a, b: a < b, 'above': lambda a, b: a > b,
       'at_or_below': lambda a, b: a <= b, 'at_or_above': lambda a, b: a >= b}
# How stale a recorded observation may be and still count as evaluating the trigger today.
EVALUATION_TOLERANCE_DAYS = 7


def load(root: Path) -> list[dict]:
    path = Path(root) / WATCHLIST
    if not path.is_file():
        return []
    entries = read_json(path, 'watch/watchlist.json')
    if not isinstance(entries, list):
        raise StateError('watch/watchlist.json must be a JSON array')
    return entries


def save(root: Path, entries: list[dict]) -> None:
    write_json(Path(root) / WATCHLIST, entries)


def next_id(entries: list[dict]) -> str:
    numbers = [int(entry['watch_id'][2:]) for entry in entries
               if WATCH_ID.fullmatch(entry.get('watch_id', ''))]
    return f'W-{max(numbers, default=0) + 1:03d}'


def add(root: Path, *, subject: str, metric: str, op: str, value: float, then: str,
        decision_id: str | None = None, expires: str | None = None) -> dict:
    if metric not in METRICS:
        raise StateError(f'metric must be one of {", ".join(METRICS)}')
    if op not in OPS:
        raise StateError(f'op must be one of {", ".join(OPS)}')
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise StateError('value must be a number')
    if not (then or '').strip():
        raise StateError(
            'a trigger needs a "then": what you decided, while calm, that this condition should '
            'prompt you to reconsider. A trigger without one is an alarm with no instruction, and '
            'it will be answered in the moment by whoever is most frightened.')
    if expires:
        try:
            dt.date.fromisoformat(expires)
        except ValueError:
            raise StateError(f'expires: {expires!r} is not an ISO date') from None

    entries = load(root)
    entry = {'watch_id': next_id(entries), 'subject': subject, 'metric': metric, 'op': op,
             'value': float(value), 'then': then.strip(), 'decision_id': decision_id,
             'created_on': today(), 'expires': expires, 'fired_on': None}
    entries.append(entry)
    save(root, entries)
    return entry


def remove(root: Path, watch_id: str) -> dict:
    entries = load(root)
    for index, entry in enumerate(entries):
        if entry.get('watch_id') == watch_id:
            removed = entries.pop(index)
            save(root, entries)
            return removed
    raise StateError(f'no such trigger: {watch_id}')


def _latest_observation(corpus: dict, subject: str, as_of: dt.date):
    best = None
    for record in corpus.values():
        symbol = (record.get('symbol') or record.get('series_id') or '').upper()
        if symbol != subject.upper() or record.get('value') is None or not record.get('asof'):
            continue
        try:
            when = dt.date.fromisoformat(str(record['asof'])[:10])
        except ValueError:
            continue
        if when > as_of:
            continue
        if best is None or when > best[0]:
            best = (when, record)
    return best


def evaluate(root: Path, as_of: str | None = None) -> dict:
    """Check every live trigger against recorded observations.

    Three outcomes per trigger, deliberately distinct: `fired`, `not_fired`, and `not_evaluated`.
    The third is the one that matters - a trigger whose data never arrived has not been checked,
    and reporting it as "not fired" would be a watchlist that silently stopped watching.
    """
    when = dt.date.fromisoformat(as_of or today())
    path = Path(root) / OBSERVATIONS
    corpus = read_json(path, 'market/observations.json') if path.is_file() else {}
    fired, not_fired, not_evaluated, expired = [], [], [], []

    for entry in load(root):
        if entry.get('expires'):
            try:
                if dt.date.fromisoformat(entry['expires']) < when:
                    expired.append(entry | {'reason': 'past its expiry date'})
                    continue
            except ValueError:
                pass
        if entry.get('fired_on'):
            continue
        found = _latest_observation(corpus, entry['subject'], when)
        if not found:
            not_evaluated.append(entry | {'reason': f'no recorded observation for '
                                                    f'{entry["subject"]} on or before {when}'})
            continue
        observed_on, record = found
        age = (when - observed_on).days
        if age > EVALUATION_TOLERANCE_DAYS:
            not_evaluated.append(entry | {
                'reason': f'the newest recorded {entry["subject"]} observation is {age} days old '
                          f'({observed_on}); this trigger was not checked today'})
            continue
        observed = float(record['value'])
        result = entry | {'observed': observed, 'observed_on': observed_on.isoformat(),
                          'ev_key': record.get('key')}
        (fired if OPS[entry['op']](observed, entry['value']) else not_fired).append(result)

    return {'as_of': when.isoformat(), 'fired': fired, 'not_fired': not_fired,
            'not_evaluated': not_evaluated, 'expired': expired}


def alert_text(entry: dict, index: int, as_of: str) -> str:
    """The alert template from 09. It quotes the user back to themselves and stops there."""
    return f"""# ALERT-{as_of.replace('-', '')}-{index:02d}

**Trigger**: {entry['subject']} {entry['metric']} {entry['op'].replace('_', ' ')} {entry['value']}
**Observed**: {entry['observed']} as of {entry['observed_on']} ({entry.get('ev_key') or 'no key'})
**You said then**: {entry['then']}

A draft decision row has been created from {entry['watch_id']}. It has not been vetted: run
`/decide --re-vet <D-0NN>`.

This alert is not a recommendation. A condition occurring is not a reason to act.
"""


def write_alerts(root: Path, result: dict) -> list[Path]:
    written = []
    entries = load(root)
    for index, fired in enumerate(result['fired'], start=1):
        path = Path(root) / ALERTS / f"ALERT-{result['as_of'].replace('-', '')}-{index:02d}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(alert_text(fired, index, result['as_of']), encoding='utf-8')
        written.append(path)
        for entry in entries:
            if entry['watch_id'] == fired['watch_id']:
                entry['fired_on'] = result['as_of']
    if written:
        save(root, entries)
    return written


def main(argv=None) -> int:
    safe_console()
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--root', type=Path, default=Path.cwd())
    parser.add_argument('--json', action='store_true')
    sub = parser.add_subparsers(dest='command', required=True)
    adder = sub.add_parser('add')
    adder.add_argument('--subject', required=True)
    adder.add_argument('--metric', default='price', choices=METRICS)
    adder.add_argument('--op', required=True, choices=sorted(OPS))
    adder.add_argument('--value', type=float, required=True)
    adder.add_argument('--then', required=True)
    adder.add_argument('--decision')
    adder.add_argument('--expires')
    lister = sub.add_parser('list')
    lister.add_argument('--all', action='store_true')
    remover = sub.add_parser('remove')
    remover.add_argument('--id', required=True)
    evaluator = sub.add_parser('evaluate')
    evaluator.add_argument('--as-of', dest='as_of')
    evaluator.add_argument('--write-alerts', action='store_true')

    args = parser.parse_args(argv)
    try:
        if args.command == 'add':
            entry = add(args.root, subject=args.subject, metric=args.metric, op=args.op,
                        value=args.value, then=args.then, decision_id=args.decision,
                        expires=args.expires)
            print(f"{entry['watch_id']}: {entry['subject']} {entry['metric']} "
                  f"{entry['op'].replace('_', ' ')} {entry['value']}")
            if not entry['expires']:
                print('  no expiry set: a condition nobody will revisit is a haunting, not a plan')
            return 0

        if args.command == 'remove':
            entry = remove(args.root, args.id)
            print(f"removed {entry['watch_id']}")
            return 0

        if args.command == 'list':
            entries = load(args.root)
            if not args.all:
                entries = [entry for entry in entries if not entry.get('fired_on')]
            if not entries:
                print('no triggers')
                return 0
            for entry in entries:
                print(f"{entry['watch_id']}  {entry['subject']:10} {entry['metric']:10} "
                      f"{entry['op']:12} {entry['value']:>10}  expires "
                      f"{entry.get('expires') or 'never'}"
                      + (f"  FIRED {entry['fired_on']}" if entry.get('fired_on') else ''))
                print(f"    then: {entry['then']}")
            return 0

        result = evaluate(args.root, args.as_of)
        written = write_alerts(args.root, result) if args.write_alerts else []
        if args.json:
            print(json.dumps(result | {'alerts': [str(p) for p in written]}, indent=2))
        else:
            print(f"as of {result['as_of']}: {len(result['fired'])} fired, "
                  f"{len(result['not_fired'])} not fired, "
                  f"{len(result['not_evaluated'])} NOT EVALUATED, "
                  f"{len(result['expired'])} expired")
            for entry in result['fired']:
                print(f"  FIRED {entry['watch_id']}: {entry['subject']} = {entry['observed']} "
                      f"({entry['observed_on']})")
                print(f"    you said then: {entry['then']}")
            for entry in result['not_evaluated']:
                print(f"  not evaluated {entry['watch_id']}: {entry['reason']}")
            for entry in result['expired']:
                print(f"  expired {entry['watch_id']}: {entry['then']}")
            for path in written:
                print(f'  wrote {path}')
        return 0
    except StateError as exc:
        print(f'watchlist: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
