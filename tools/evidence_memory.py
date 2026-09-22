#!/usr/bin/env python3
"""Immutable evidence cards: a claim, the quote that supports it, and the snapshot it came from.

Usage (from the repository root):
    python tools/evidence_memory.py snapshot --source <file>
    python tools/evidence_memory.py add --candidate card.json
    python tools/evidence_memory.py add --candidate card.json --url https://... [--allow-stale]
    python tools/evidence_memory.py check --key EV-1a2b3c4d
    python tools/evidence_memory.py check --all
    python tools/evidence_memory.py get --key EV-1a2b3c4d

An `EV-` key is what a gate record, a report number and a scenario assumption cite. This tool is
what makes that citation mean something:

- **The quote must occur verbatim in the snapshot.** Not "be supported by", not "paraphrase" -
  occur, after whitespace normalisation. A card whose quote is not in its snapshot is rejected,
  because the failure mode being prevented is a confident sentence about a number nobody published.
- **Cards are immutable and content-addressed.** The key is the digest of the card's own payload.
  Re-adding identical content returns the same key; changed content is a NEW card that may
  `supersedes` the old one. Nothing is edited in place, so a report written last month still
  resolves to exactly what it cited.
- **`mnpi_screen: "clear"` is required.** A card without it cannot be stored, and therefore cannot
  be cited by any agent or report. The screen runs in tools/compliance_guard.py.
- **A forecast is marked `is_forecast: true`** and can never satisfy the coverage condition c4.
  It may inform narrative; it may not support a gate.
- **`add --url` re-fetches with urllib and snapshots the response.** A URL a web tool or an MCP
  connector discovered counts toward coverage only after this step, so every counted source is
  replayable offline by someone who was not there.

Stdlib only. Exit 0 ok; 1 state or schema error; 2 fetch failure; 4 bad arguments; 9 MNPI refusal.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fa_state import (StateError, canonical_digest, now_utc, read_json, safe_console,  # noqa: E402
                      sha256_bytes, write_json)
from providers import base  # noqa: E402

EVIDENCE = Path('market') / 'evidence'
SNAPSHOTS = Path('market') / 'snapshots'
KEY_RE = re.compile(r'EV-[0-9a-f]{8,16}')
TOPIC_RE = re.compile(r'[a-z0-9][a-z0-9_-]{0,63}')
DIRECTIONS = ('supportive', 'adverse', 'neutral', 'mixed')
HORIZONS = ('spot', '1m', '3m', '6m', '1y', '3y', '5y+')
CONFIDENCES = ('low', 'medium', 'high')
REQUIRED = ('claim', 'topic', 'tier', 'provider', 'quote', 'asof')
MNPI_REFUSAL = 9
FETCH_FAILED = 2
MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024
WHITESPACE = re.compile(r'\s+')


def normalise(text: str) -> str:
    """Whitespace-insensitive comparison. Line wrapping is not a difference of substance."""
    return WHITESPACE.sub(' ', text).strip()


def snapshot_path(root: Path, digest: str) -> Path:
    return root / SNAPSHOTS / f'{digest}.txt'


def write_snapshot(root: Path, text: str) -> str:
    raw = text.encode('utf-8')
    if len(raw) > MAX_SNAPSHOT_BYTES:
        raise StateError(f'snapshot is {len(raw)} bytes; the limit is {MAX_SNAPSHOT_BYTES}')
    digest = sha256_bytes(raw)
    path = snapshot_path(root, digest)
    if not path.is_file():                 # content-addressed: identical text is stored once
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
    return digest


def read_snapshot(root: Path, digest: str) -> str:
    path = snapshot_path(root, digest)
    if not path.is_file():
        raise StateError(f'no snapshot {digest}; a card cannot outlive the text it quotes')
    return path.read_text(encoding='utf-8')


def card_key(payload: dict) -> str:
    return 'EV-' + canonical_digest(payload)[:12]


def card_path(root: Path, topic: str, key: str) -> Path:
    return root / EVIDENCE / topic / f'{key}.json'


def validate(candidate: dict, root: Path) -> dict:
    """Return the payload a key is computed from, or refuse with every problem found."""
    if not isinstance(candidate, dict):
        raise StateError('a candidate card must be a JSON object')
    problems = [f'{field} is required' for field in REQUIRED if not candidate.get(field)]

    topic = str(candidate.get('topic') or '')
    if topic and not TOPIC_RE.fullmatch(topic):
        problems.append(f'topic {topic!r}: lowercase letters, digits, hyphen and underscore only')
    if candidate.get('tier') and candidate['tier'] not in base.TIERS:
        problems.append(f'tier {candidate.get("tier")!r} is not one of {", ".join(base.TIERS)}')
    for field, allowed in (('direction', DIRECTIONS), ('horizon', HORIZONS),
                           ('confidence', CONFIDENCES)):
        if candidate.get(field) and candidate[field] not in allowed:
            problems.append(f'{field} {candidate[field]!r} is not one of {", ".join(allowed)}')
    if candidate.get('asof'):
        try:
            dt.date.fromisoformat(str(candidate['asof'])[:10])
        except ValueError:
            problems.append(f'asof {candidate["asof"]!r} is not an ISO date')
    if candidate.get('mnpi_screen') != 'clear':
        problems.append('mnpi_screen must be "clear" - run tools/compliance_guard.py screen first; '
                        'a card that has not been screened cannot be cited by anything')
    keys = candidate.get('record_keys')
    if keys is not None and not (isinstance(keys, list) and all(isinstance(k, str) for k in keys)):
        problems.append('record_keys must be a list of observation keys')
    supersedes = candidate.get('supersedes')
    if supersedes and not KEY_RE.fullmatch(str(supersedes)):
        problems.append(f'supersedes {supersedes!r} is not an EV- key')

    digest = candidate.get('snapshot_digest')
    if not digest:
        problems.append('snapshot_digest is required - run `snapshot --source <file>` first')
    elif not problems:
        text = read_snapshot(root, digest)
        if normalise(candidate['quote']) not in normalise(text):
            problems.append(
                'the quote does not occur in the snapshot. A card asserts that a source said '
                'this; if the words are not in the recorded text, the card is an assertion about '
                'nothing. Quote the source exactly, or snapshot the text that does say it.')
    if problems:
        raise StateError('candidate card rejected: ' + '; '.join(problems))

    return {
        'claim': candidate['claim'].strip(),
        'topic': topic,
        'tier': candidate['tier'],
        'provider': candidate['provider'],
        'record_keys': sorted(candidate.get('record_keys') or []),
        'quote': candidate['quote'].strip(),
        'snapshot_digest': digest,
        'asof': str(candidate['asof'])[:10],
        'url': candidate.get('url'),
        'direction': candidate.get('direction') or 'neutral',
        'horizon': candidate.get('horizon') or 'spot',
        'confidence': candidate.get('confidence') or 'low',
        'is_forecast': bool(candidate.get('is_forecast')),
        'mnpi_screen': 'clear',
        'supersedes': candidate.get('supersedes'),
    }


def add(root: Path, candidate: dict) -> tuple[dict, bool]:
    payload = validate(candidate, root)
    key = card_key(payload)
    path = card_path(root, payload['topic'], key)
    if path.is_file():
        return read_json(path, str(path)), False        # identical content, same card
    card = dict(payload, key=key, created_at=now_utc(), cited_in=[])
    write_json(path, card)
    return card, True


def fetch_and_snapshot(root: Path, url: str, transport=None) -> tuple[str, str]:
    """Re-fetch a discovered URL ourselves, so what counts toward coverage is replayable."""
    text = base.http_get(url, operator='discovered', transport=transport)
    return write_snapshot(root, text), text


def find_card(root: Path, key: str) -> Path:
    if not KEY_RE.fullmatch(key):
        raise StateError(f'{key!r} is not an EV- key')
    matches = sorted((root / EVIDENCE).glob(f'*/{key}.json'))
    if not matches:
        raise StateError(f'no such evidence card: {key}')
    return matches[0]


def check(root: Path, key: str | None = None) -> list[dict]:
    """Re-verify cards against their snapshots. A card that stops verifying is reported, never fixed."""
    paths = [find_card(root, key)] if key else sorted((root / EVIDENCE).glob('*/EV-*.json'))
    results = []
    for path in paths:
        card = read_json(path, str(path))
        problems = []
        try:
            text = read_snapshot(root, card.get('snapshot_digest', ''))
            if normalise(card.get('quote', '')) not in normalise(text):
                problems.append('the quote no longer occurs in its snapshot')
        except StateError as exc:
            problems.append(str(exc))
        payload = {field: card.get(field) for field in (
            'claim', 'topic', 'tier', 'provider', 'record_keys', 'quote', 'snapshot_digest',
            'asof', 'url', 'direction', 'horizon', 'confidence', 'is_forecast', 'mnpi_screen',
            'supersedes')}
        if card_key(payload) != card.get('key'):
            problems.append('the card content does not match its key - it was edited in place, '
                            'and a card is immutable by construction')
        if card.get('mnpi_screen') != 'clear':
            problems.append('mnpi_screen is not "clear"; this card must not be cited')
        results.append({'key': card.get('key'), 'path': str(path.relative_to(root)),
                        'ok': not problems, 'problems': problems})
    return results


def main(argv=None) -> int:
    safe_console()
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--root', type=Path, default=Path.cwd())
    sub = parser.add_subparsers(dest='command', required=True)

    snap = sub.add_parser('snapshot')
    snap.add_argument('--source', type=Path, required=True)

    adder = sub.add_parser('add')
    adder.add_argument('--candidate', type=Path, required=True)
    adder.add_argument('--url', default=None,
                       help='re-fetch this URL and snapshot it before validating the card')

    checker = sub.add_parser('check')
    checker.add_argument('--key')
    checker.add_argument('--all', action='store_true')

    getter = sub.add_parser('get')
    getter.add_argument('--key', required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == 'snapshot':
            text = args.source.read_text(encoding='utf-8', errors='replace')
            digest = write_snapshot(args.root, text)
            print(f'snapshot {digest} ({len(text)} chars) -> '
                  f'{snapshot_path(args.root, digest).relative_to(args.root)}')
            return 0

        if args.command == 'add':
            candidate = read_json(args.candidate, 'candidate card')
            if args.url:
                try:
                    digest, _ = fetch_and_snapshot(args.root, args.url)
                except base.ProviderError as exc:
                    print(f'evidence_memory: could not re-fetch {args.url}: {exc}', file=sys.stderr)
                    return FETCH_FAILED
                candidate['snapshot_digest'] = digest
                candidate.setdefault('url', args.url)
            if candidate.get('mnpi_screen') == 'mnpi_suspected':
                print('evidence_memory: refused - this candidate was screened as suspected MNPI. '
                      'It cannot become a card, and tools/compliance_guard.py has logged it.',
                      file=sys.stderr)
                return MNPI_REFUSAL
            card, created = add(args.root, candidate)
            print(f"{card['key']}: {'stored' if created else 'already stored (identical content)'}"
                  f" -> {card_path(args.root, card['topic'], card['key']).relative_to(args.root)}")
            if card.get('is_forecast'):
                print('  marked is_forecast: it can inform narrative and can never satisfy c4')
            return 0

        if args.command == 'check':
            if not args.key and not args.all:
                print('evidence_memory: pass --key or --all', file=sys.stderr)
                return 4
            results = check(args.root, args.key)
            bad = [item for item in results if not item['ok']]
            for item in results:
                print(f"{item['key']}: {'OK' if item['ok'] else 'FAILED'}")
                for problem in item['problems']:
                    print(f'  - {problem}')
            print(f'{len(results)} card(s), {len(bad)} failing')
            return 1 if bad else 0

        card = read_json(find_card(args.root, args.key), 'evidence card')
        print(json.dumps(card, indent=2, ensure_ascii=False))
        return 0
    except StateError as exc:
        print(f'evidence_memory: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
