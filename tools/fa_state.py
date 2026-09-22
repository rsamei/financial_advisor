"""Shared state primitives for this repo's tools: tracker schema, atomic writes, locks, ledgers.

Stdlib only. Imported by the other tools in this directory, never run directly.

Design rules that every caller relies on:
- Every file replacement is atomic (write a temporary sibling, fsync, os.replace). A crash leaves
  either the old file or the new one, never a truncated mix.
- A multi-file operation is NOT a transaction. Callers order their writes so an interruption leaves
  a state the same command can detect and finish on the next run, and say so in their docstrings.
- Locks are exclusive-create files carrying the owner's PID and start time. An interrupted run
  leaves its lock behind on purpose: the next run reports it and the user removes it only after
  checking that the recorded process is gone. Nothing here deletes another process's lock.
- decision_tracker.csv is read leniently and written canonically, per the vocabularies in
  .claude/commands/decide.md. Its header is frozen: it never gains a column. Extra material goes
  into the `notes` field as tokens.
- This module stores what other tools computed; it never computes portfolio arithmetic
  (tools/portfolio_math.py owns that) and never decides a verdict (tools/decision_score.py does).

Ported from research-assistant's tools/research_state.py; the primitives are unchanged, the
domain vocabulary is this repo's.
"""
from __future__ import annotations

import contextlib
import csv
import datetime as dt
import hashlib
import io
import json
import os
import re
import sys
import tempfile
from pathlib import Path

# Frozen header (plan section 5.4). Never add a column; extra material becomes a token in `notes`.
DECISION_FIELDS = ('decision_id,created_on,origin,question,decision_type,amount_eur,instrument,'
                   'gate1,gate2,gate3,gate4,verdict,score,band,status,market_query_id,ips_version,'
                   'brief,notes').split(',')

ORIGINS = ('user', 'advise', 'watch_trigger', 'skeptic_reframe')
DECISION_TYPES = ('allocate', 'rebalance', 'buy', 'sell', 'hold', 'debt', 'cash', 'pension',
                  'insurance', 'purchase', 'tax', 'income', 'other')
STATUSES = ('draft', 'vetted', 'accepted', 'executed', 'partial', 'declined', 'expired',
            'superseded', 'parked')
TERMINAL_STATUSES = ('declined', 'expired', 'superseded')
GATE_VERDICTS = ('PASS', 'FLAG', 'FAIL')
COVERAGE_VERDICTS = ('SUPPORTED', 'UNDETERMINED')
BANDS = ('do_now', 'do_scoped', 'park', 'reject', 'gated')
RISK_BANDS = ('low', 'moderate', 'high')

DECISION_ID = re.compile(r'D-\d{3,}')
ADVICE_ID = re.compile(r'AR-\d{8}-\d{2}')
VIEW_ID = re.compile(r'VW-\d{8}-\d{2}')
REVIEW_ID = re.compile(r'RV-\d{8}-\d{2}')
ALERT_ID = re.compile(r'ALERT-\d{8}-\d{2}')
EVIDENCE_KEY = re.compile(r'EV-[0-9a-f]{8,}')
QUERY_ID = re.compile(r'Q-[A-Za-z0-9][A-Za-z0-9._-]*')


class StateError(Exception):
    """A refusal the user can act on. Tools print the message without a traceback."""


def safe_console() -> None:
    """Make tool output UTF-8 and unkillable, whatever code page the console is using.

    Two failures this prevents, both seen on Windows:

    - A tool that has already written state dies on a UnicodeEncodeError while printing an
      instrument name or an institution outside cp1252. `errors="backslashreplace"` keeps the
      message readable and the exit status honest.
    - A caller capturing the output as UTF-8 gets `None` instead of text, because the console
      encoded a non-ASCII character (a middle dot, a euro sign) in the local code page. Forcing
      UTF-8 makes what a tool prints the same bytes everywhere, which matters because these tools
      are read by other tools at least as often as by a person.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='backslashreplace')
        except (AttributeError, ValueError):
            try:
                stream.reconfigure(errors='backslashreplace')
            except (AttributeError, ValueError):
                pass


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def today() -> str:
    return dt.date.today().isoformat()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


def atomic_write_text(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=f'.{path.name}.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def write_json(path: Path, value) -> None:
    atomic_write_text(path, json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def read_json(path: Path, what: str):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        raise StateError(f'{what} not found: {path}') from None
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StateError(f'{what} is not valid UTF-8 JSON: {path} ({exc})') from None


OPERATION_ID = re.compile(r'WF-\d{8}-[0-9a-f]{8}(?:-\d+)?:[a-z][a-z0-9-]{0,63}:\d+')


def canonical_digest(value) -> str:
    return sha256_bytes(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                   separators=(',', ':')).encode('utf-8'))


def write_receipt(root: Path, receipt_out: Path, operation_id: str, owner: str,
                  files: list[tuple[str, Path]], domains: list[tuple[str, str, str]]) -> Path:
    """Write an owner receipt for tools/workflow_state.py after the owner's domain writes committed.

    Called last, so a receipt on disk means every output it names was already written. File outputs
    are hashed now; use a domain output for shared stores that later commands legitimately change
    (the observation corpus, the tracker), otherwise reconciliation would treat a later, lawful
    write as a changed result.
    """
    root = Path(root).resolve()
    if not OPERATION_ID.fullmatch(operation_id):
        raise StateError('operation id must look like WF-YYYYMMDD-xxxxxxxx:<step>:<attempt>')

    def inside(path: Path, what: str) -> str:
        resolved = (Path(path) if Path(path).is_absolute() else root / path).resolve()
        if root not in resolved.parents:
            raise StateError(f'{what} must be inside the repository root {root}')
        return resolved.relative_to(root).as_posix()

    outputs = [{'name': name, 'kind': 'file', 'reference': inside(path, name),
                'digest': file_sha256(root / inside(path, name))} for name, path in files]
    outputs += [{'name': name, 'kind': 'domain', 'reference': reference, 'digest': digest}
                for name, reference, digest in domains]
    target = root / inside(receipt_out, 'receipt path')
    write_json(target, {'operation_id': operation_id, 'owner': owner, 'committed_at': now_utc(),
                        'outputs': outputs})
    return target


@contextlib.contextmanager
def exclusive_lock(path: Path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            owner = path.read_text(encoding='utf-8').strip()
        except OSError:
            owner = 'unreadable'
        raise StateError(f'another run holds {path} ({owner}). If that process is no longer running, '
                         'the run was interrupted: inspect the files it was writing, then delete the '
                         'lock file yourself.') from None
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(f'pid={os.getpid()} started={now_utc()}\n')
        yield
    finally:
        path.unlink(missing_ok=True)


def canonical_status(value: str) -> str:
    """Read leniently (case, spaces, hyphens); return the canonical spelling or refuse."""
    status = re.sub(r'[\s-]+', '_', value.strip().lower())
    if status not in STATUSES:
        raise StateError(f'unknown decision status: {value!r}')
    return status


def read_tracker(path: Path) -> list[dict[str, str]]:
    path = Path(path)
    if not path.exists():
        raise StateError(f'decision tracker not found: {path}. Vet a decision with /decide first.')
    with path.open(encoding='utf-8-sig', newline='') as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != DECISION_FIELDS:
            raise StateError(f'unexpected decision_tracker.csv header: {reader.fieldnames}')
        rows = list(reader)
    for number, row in enumerate(rows, start=2):
        if None in row or any(value is None for value in row.values()):
            raise StateError(f'malformed decision_tracker.csv row at line {number}')
    ids = [row['decision_id'] for row in rows]
    if len(ids) != len(set(ids)):
        raise StateError('decision_tracker.csv contains a duplicate decision_id; resolve it first')
    return rows


def write_tracker(path: Path, rows: list[dict[str, str]]) -> None:
    out = io.StringIO(newline='')
    writer = csv.DictWriter(out, fieldnames=DECISION_FIELDS, lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    atomic_write_text(path, out.getvalue())


def tracker_header() -> str:
    return ','.join(DECISION_FIELDS)


def validate_decision_row(row: dict[str, str]) -> None:
    """Refuse a row whose enumerated fields are outside the rubric's vocabulary.

    Gate-before-score is enforced here as well as in tools/decision_score.py: a FAIL on any gate
    forces band `gated` and an empty score, so no gated decision can carry a number that a later
    report might quote as if it had survived.
    """
    problems = []
    if not DECISION_ID.fullmatch(row['decision_id']):
        problems.append(f"decision_id {row['decision_id']!r}")
    if row['origin'] not in ORIGINS:
        problems.append(f"origin {row['origin']!r}")
    if row['decision_type'] not in DECISION_TYPES:
        problems.append(f"decision_type {row['decision_type']!r}")
    try:
        canonical_status(row['status'])
    except StateError as exc:
        problems.append(str(exc))
    for field in ('gate1', 'gate2', 'gate3'):
        if row[field] not in GATE_VERDICTS:
            problems.append(f'{field} {row[field]!r}')
    # Gate 4 reports coverage, which can never FAIL: absence of evidence is UNDETERMINED (a FLAG).
    if row['gate4'] not in (*GATE_VERDICTS, *COVERAGE_VERDICTS):
        problems.append(f"gate4 {row['gate4']!r}")
    if row['gate4'] == 'FAIL':
        problems.append('gate4 never FAILs: missing evidence is UNDETERMINED, a FLAG with cap:55')
    if row['band'] not in BANDS:
        problems.append(f"band {row['band']!r}")
    if row['amount_eur'] and not re.fullmatch(r'-?\d+(\.\d{1,2})?', row['amount_eur']):
        problems.append(f"amount_eur {row['amount_eur']!r}")
    gated = row['band'] == 'gated' or 'FAIL' in (row['gate1'], row['gate2'], row['gate3'])
    if gated and row['band'] != 'gated':
        problems.append('a failed gate requires band gated')
    if gated and row['score']:
        problems.append('a gated decision must have an empty score')
    if not gated and not re.fullmatch(r'\d{1,3}', row['score'] or ''):
        problems.append(f"score {row['score']!r}")
    if problems:
        raise StateError(f"tracker row {row['decision_id']} is invalid: " + '; '.join(problems))


# --- hash-chained append-only ledgers ----------------------------------------------------------
#
# track/actions.jsonl records what the user actually did with their money. Each line is one event
# whose `hash` is the SHA-256 of its canonical JSON without `hash`, and whose `previous` is the
# hash of the line before (64 zeros for the first). Nothing here rewrites a line: a correction is
# a new event. tools/track_actions.py is the only writer.

GENESIS = '0' * 64


def canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':'))


def read_chain(path: Path, what: str = 'ledger') -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    raw = path.read_bytes()
    if raw and not raw.endswith(b'\n'):
        raise StateError(f'{what} ends with an incomplete entry from an interrupted write. Inspect '
                         'the last line; it was never acknowledged, so its event must be recorded '
                         'again.')
    events, previous = [], GENESIS
    for number, line in enumerate(raw.decode('utf-8').splitlines(), start=1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            raise StateError(f'{what} line {number} is not valid JSON') from None
        if not isinstance(event, dict):
            raise StateError(f'{what} line {number} is not an event object')
        body = {k: v for k, v in event.items() if k != 'hash'}
        if event.get('previous') != previous or event.get('hash') != sha256_bytes(
                canonical_json(body).encode()):
            raise StateError(f'{what} line {number} breaks the hash chain; the file was edited')
        previous = event['hash']
        events.append(event)
    return events


def append_chain(path: Path, events: list[dict], kind: str, payload: dict) -> dict:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {'kind': kind, 'recorded_at': now_utc(), 'payload': payload,
            'previous': events[-1]['hash'] if events else GENESIS}
    event = body | {'hash': sha256_bytes(canonical_json(body).encode())}
    with path.open('ab') as handle:
        handle.write((canonical_json(event) + '\n').encode('utf-8'))
        handle.flush()
        os.fsync(handle.fileno())
    events.append(event)
    return event
