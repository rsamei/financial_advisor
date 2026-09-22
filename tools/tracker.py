#!/usr/bin/env python3
"""Append to and update `decision_tracker.csv`, whose 19-column header never changes.

Usage (from the repository root):
    python tools/tracker.py add    --row <file> [--allow-existing]
    python tools/tracker.py update --id D-001 --status executed [--note "cap:55 coverage=..."]
    python tools/tracker.py list   [--status vetted] [--band do_now] [--open]
    python tools/tracker.py get    --id D-001
    python tools/tracker.py next-id
    python tools/tracker.py archive add --concept "..." --why "..." --revive-when "..."
    python tools/tracker.py archive list

What this tool enforces that prose cannot:

- **The header is frozen.** Nineteen columns, forever. Anything extra goes into `notes` as a token
  (`cap:55 coverage=UNDETERMINED`, `reframe_of:D-002`, `advice:AR-20260920-01`, `view:VW-…`).
  A schema that grows a column whenever something new appears is a schema nobody can read back.
- **Gate-before-score, again.** A row whose gate failed must carry `band: gated` and an empty
  score; `fa_state.validate_decision_row` refuses anything else. The rule lives in two places
  because this is the copy that survives the report being deleted.
- **Status transitions are checked.** A decision cannot go from `draft` to `executed` without being
  vetted, and a terminal row cannot be revived. The user acting is recorded by
  `tools/track_actions.py`; this file records only what the system decided.
- **An id is never reused.** `next-id` reads the whole file; `add` refuses a duplicate.

Stdlib only. Exit 0 ok; 1 state or schema error; 4 bad arguments; 6 id collision.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fa_state import (DECISION_FIELDS, STATUSES, TERMINAL_STATUSES, StateError,  # noqa: E402
                      canonical_status, read_json, read_tracker, safe_console, today,
                      validate_decision_row, write_json, write_tracker)

TRACKER = Path('decision_tracker.csv')
ARCHIVE = Path('decisions') / 'archive.json'
TEMPLATE = Path('decision_tracker.template.csv')
ID_COLLISION = 6

# What each status may become. `parked` stays visible in --list on purpose: a parked decision that
# disappears from the list is a decision that was forgotten, not one that was parked.
TRANSITIONS = {
    'draft': ('draft', 'vetted', 'declined', 'expired', 'superseded', 'parked'),
    'vetted': ('vetted', 'accepted', 'declined', 'expired', 'superseded', 'parked'),
    'accepted': ('accepted', 'executed', 'partial', 'declined', 'expired', 'superseded'),
    'partial': ('partial', 'executed', 'declined', 'expired', 'superseded'),
    'executed': ('executed', 'superseded'),
    'parked': ('parked', 'vetted', 'declined', 'expired', 'superseded'),
    'declined': (), 'expired': (), 'superseded': (),
}
OPEN_STATUSES = tuple(s for s in STATUSES if s not in TERMINAL_STATUSES)


def archive_add(root: Path, concept: str, why: str, revive_when: str) -> dict:
    """Record a concept dropped before vetting, with what would bring it back.

    The archive is **shown, never ranked on**. It exists so a dropped idea can be reconsidered when
    the world changes, not so the system can learn what the user likes - a preference store that
    feeds ranking is how an advisor starts telling someone what they want to hear.
    """
    for field, value in (('concept', concept), ('why_dropped', why), ('revive_when', revive_when)):
        if not value or not str(value).strip():
            raise StateError(f'archive: {field} is required. A concept with no revival condition '
                             'is not archived, it is forgotten.')
    path = Path(root) / ARCHIVE
    entries = read_json(path, 'decisions/archive.json') if path.is_file() else []
    if not isinstance(entries, list):
        raise StateError('decisions/archive.json must be a JSON array')
    entry = {'concept': concept.strip(), 'why_dropped': why.strip(),
             'revive_when': revive_when.strip(), 'archived_on': today()}
    entries.append(entry)
    write_json(path, entries)
    return entry


def archive_list(root: Path) -> list[dict]:
    path = Path(root) / ARCHIVE
    return read_json(path, 'decisions/archive.json') if path.is_file() else []


def tracker_path(root: Path) -> Path:
    return Path(root) / TRACKER


def load(root: Path) -> list[dict]:
    path = tracker_path(root)
    if not path.is_file():
        return []
    return read_tracker(path)


def ensure_tracker(root: Path) -> None:
    """Create the tracker from the tracked template, so the header can only ever come from there."""
    path = tracker_path(root)
    if path.is_file():
        return
    template = Path(root) / TEMPLATE
    header = (template.read_text(encoding='utf-8').strip() if template.is_file()
              else ','.join(DECISION_FIELDS))
    if header.split(',') != DECISION_FIELDS:
        raise StateError(f'{TEMPLATE} does not carry the frozen header')
    write_tracker(path, [])


def next_id(rows: list[dict]) -> str:
    numbers = [int(row['decision_id'].split('-')[1]) for row in rows
               if row.get('decision_id', '').startswith('D-')]
    return f'D-{max(numbers, default=0) + 1:03d}'


def add(root: Path, row: dict) -> dict:
    rows = load(root)
    complete = {field: '' for field in DECISION_FIELDS} | {
        k: ('' if v is None else str(v)) for k, v in row.items()}
    unknown = set(row) - set(DECISION_FIELDS)
    if unknown:
        raise StateError(
            f'unknown column(s) {sorted(unknown)}. The header is frozen at 19 columns; extra '
            'material belongs in notes as a token (cap:…, reframe_of:…, advice:…, view:…).')
    if not complete['decision_id']:
        complete['decision_id'] = next_id(rows)
    if not complete['created_on']:
        complete['created_on'] = today()
    if any(existing['decision_id'] == complete['decision_id'] for existing in rows):
        raise StateError(f'{complete["decision_id"]} already exists', )
    complete['status'] = canonical_status(complete['status'] or 'draft')
    validate_decision_row(complete)
    ensure_tracker(root)
    rows.append(complete)
    write_tracker(tracker_path(root), rows)
    return complete


def update(root: Path, decision_id: str, *, status: str | None = None, note: str | None = None,
           **fields) -> dict:
    rows = load(root)
    for row in rows:
        if row['decision_id'] != decision_id:
            continue
        if status:
            current = canonical_status(row['status'] or 'draft')
            target = canonical_status(status)
            allowed = TRANSITIONS.get(current, ())
            if target not in allowed:
                raise StateError(
                    f'{decision_id}: {current} -> {target} is not a valid transition'
                    + (f' (allowed: {", ".join(allowed)})' if allowed
                       else f'; {current} is terminal and a terminal row is never revived - '
                            'supersede it with a new decision instead'))
            row['status'] = target
        for field, value in fields.items():
            if value is None:
                continue
            if field not in DECISION_FIELDS:
                raise StateError(f'unknown column {field!r}; the header is frozen')
            row[field] = str(value)
        if note:
            row['notes'] = ' '.join(filter(None, [row['notes'], note]))
        validate_decision_row(row)
        write_tracker(tracker_path(root), rows)
        return row
    raise StateError(f'no such decision: {decision_id}')


def listing(rows: list[dict], *, status: str | None = None, band: str | None = None,
            open_only: bool = False) -> list[dict]:
    out = rows
    if status:
        out = [row for row in out if row['status'] == canonical_status(status)]
    if band:
        out = [row for row in out if row['band'] == band]
    if open_only:
        out = [row for row in out if row['status'] in OPEN_STATUSES]
    return out


def from_gate_record(record: dict, *, question: str, decision_type: str, origin: str = 'advise',
                     amount_eur=None, instrument: str = '', market_query_id: str = '',
                     brief: str = '', extra_notes: str = '') -> dict:
    """Turn a decision_score.py result into a tracker row, so the two can never disagree."""
    gates = record.get('gates') or {}
    notes = ' '.join(filter(None, [' '.join(record.get('caps') or []), extra_notes]))
    return {
        'decision_id': record.get('decision_id') or '',
        'origin': origin,
        'question': question,
        'decision_type': decision_type,
        'amount_eur': '' if amount_eur is None else f'{float(amount_eur):.2f}',
        'instrument': instrument,
        'gate1': gates.get('gate1_affordability', ''),
        'gate2': gates.get('gate2_suitability', ''),
        'gate3': gates.get('gate3_legal_tax', ''),
        'gate4': gates.get('gate4_evidence_coverage', ''),
        'verdict': record.get('band', ''),
        'score': '' if record.get('score') is None else str(record['score']),
        'band': record.get('band', ''),
        'status': 'vetted' if record.get('band') != 'gated' else 'draft',
        'market_query_id': market_query_id,
        'ips_version': record.get('ips_version', '') or '',
        'brief': brief,
        'notes': notes,
    }


def main(argv=None) -> int:
    safe_console()
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--root', type=Path, default=Path.cwd())
    sub = parser.add_subparsers(dest='command', required=True)
    adder = sub.add_parser('add')
    adder.add_argument('--row', type=Path, required=True)
    updater = sub.add_parser('update')
    updater.add_argument('--id', required=True)
    updater.add_argument('--status')
    updater.add_argument('--note')
    updater.add_argument('--brief')
    lister = sub.add_parser('list')
    lister.add_argument('--status')
    lister.add_argument('--band')
    lister.add_argument('--open', action='store_true')
    getter = sub.add_parser('get')
    getter.add_argument('--id', required=True)
    sub.add_parser('next-id')
    archive = sub.add_parser('archive')
    archive_sub = archive.add_subparsers(dest='archive_command', required=True)
    archive_adder = archive_sub.add_parser('add')
    archive_adder.add_argument('--concept', required=True)
    archive_adder.add_argument('--why', required=True)
    archive_adder.add_argument('--revive-when', dest='revive_when', required=True)
    archive_sub.add_parser('list')

    args = parser.parse_args(argv)
    try:
        if args.command == 'archive':
            if args.archive_command == 'add':
                entry = archive_add(args.root, args.concept, args.why, args.revive_when)
                print(f"archived: {entry['concept']} (revive when: {entry['revive_when']})")
                return 0
            entries = archive_list(args.root)
            if not entries:
                print('archive is empty')
                return 0
            for entry in entries:
                print(f"{entry['archived_on']}  {entry['concept']}")
                print(f"    dropped because: {entry['why_dropped']}")
                print(f"    revive when: {entry['revive_when']}")
            return 0
        if args.command == 'next-id':
            print(next_id(load(args.root)))
            return 0
        if args.command == 'add':
            row = add(args.root, read_json(args.row, 'tracker row'))
            print(f"{row['decision_id']}: {row['band'] or 'no band'} / {row['status']}")
            return 0
        if args.command == 'update':
            row = update(args.root, args.id, status=args.status, note=args.note,
                         brief=args.brief)
            print(f"{row['decision_id']}: {row['status']}")
            return 0
        if args.command == 'get':
            for row in load(args.root):
                if row['decision_id'] == args.id:
                    print(json.dumps(row, indent=2, ensure_ascii=False))
                    return 0
            raise StateError(f'no such decision: {args.id}')

        rows = listing(load(args.root), status=args.status, band=args.band, open_only=args.open)
        if not rows:
            print('no rows')
            return 0
        print(f'{"id":8} {"band":10} {"status":10} {"score":>5}  question')
        for row in rows:
            print(f'{row["decision_id"]:8} {row["band"]:10} {row["status"]:10} '
                  f'{row["score"] or "-":>5}  {row["question"][:60]}')
        return 0
    except StateError as exc:
        message = str(exc)
        print(f'tracker: {message}', file=sys.stderr)
        return ID_COLLISION if 'already exists' in message else 1


if __name__ == '__main__':
    sys.exit(main())
