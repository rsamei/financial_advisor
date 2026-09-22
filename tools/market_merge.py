#!/usr/bin/env python3
"""The single dedup authority for market observations. Every fuzzy merge it makes, it reports.

Usage (from the repository root):
    python tools/market_merge.py merge --records <file> [--records <file> ...] [--dry-run]
    python tools/market_merge.py stats
    python tools/market_merge.py get --key ecb:EXR.USD@2026-09-18

What this tool enforces that prose cannot:

- **One authority.** Nothing else writes `market/observations.json`. Two components deduplicating
  with slightly different keys is how a corpus grows two versions of one fact and a report quotes
  whichever it reached first.
- **Identity is exact, not clever.** Records merge when their `key` matches, or when
  (provider, series_id|symbol, asof, kind) matches - that is the *fuzzy* rule, and every merge it
  performs is listed in the report. Nothing merges on a similar title.
- **`null` is fillable; `""` is an answer.** A later record fills a field that was `null`. It never
  overwrites a non-null value, and never turns `""` (known empty) back into unknown. A genuine
  disagreement between two providers is recorded in `conflicts`, not resolved by writing order.
- **Protected fields are never rewritten**: `first_seen`, `cited_in`, `screened_for`. An evidence
  card cites a record; a merge that silently reset those would break the citation.
- **A merge never invents a source.** `sources` is the union of the providers that actually
  returned the record, which is what coverage counts operators from.

Stdlib only. Exit 0 ok; 1 state or schema error; 4 bad arguments.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fa_state import StateError, read_json, safe_console, write_json  # noqa: E402

OBSERVATIONS = Path('market') / 'observations.json'
PROTECTED = ('first_seen', 'cited_in', 'screened_for')
# Fields whose disagreement between two providers is a real conflict worth reporting, rather than
# a formatting difference.
MATERIAL = ('value', 'unit', 'currency', 'asof', 'is_forecast')


def fuzzy_key(record: dict):
    """The one fuzzy identity rule: same provider, same INSTRUMENT, same date, same kind.

    Returns None when the record names no instrument, and a record with no fuzzy key merges on its
    exact key alone. Found live on 2026-09-20: two different ECB press releases published the same
    day share provider, kind and date, and an instrument-less fuzzy key silently collapsed them
    into one. A dedup rule that merges two distinct facts is worse than no dedup rule.
    """
    instrument = record.get('series_id') or record.get('symbol')
    if not instrument:
        return None
    return (record.get('provider'), instrument, str(record.get('asof')), record.get('kind'))


def load(root: Path) -> dict:
    path = root / OBSERVATIONS
    if not path.is_file():
        return {}
    data = read_json(path, 'market/observations.json')
    if not isinstance(data, dict):
        raise StateError('market/observations.json must be an object keyed by record key')
    return data


def _merge_one(existing: dict, incoming: dict, report: dict) -> dict:
    merged = dict(existing)
    for field, value in incoming.items():
        if field in PROTECTED or field == 'sources':
            continue
        current = merged.get(field)
        if current is None and value is not None:
            merged[field] = value                      # fill an unknown
        elif field in MATERIAL and current is not None and value is not None and current != value:
            report['conflicts'].append(
                {'key': existing.get('key'), 'field': field, 'kept': current, 'rejected': value,
                 'from': incoming.get('provider')})
            # The first recorded value stands. A later pull does not overwrite a fact by arriving
            # second; the disagreement is surfaced so a human decides.
    merged['sources'] = sorted(set(existing.get('sources') or []) | set(incoming.get('sources') or []))
    for field in PROTECTED:
        if field in existing:
            merged[field] = existing[field]
    return merged


def merge(root: Path, batches: list[list[dict]]) -> tuple[dict, dict]:
    corpus = load(root)
    by_fuzzy = {fuzzy_key(record): key for key, record in corpus.items()
                if fuzzy_key(record) is not None}
    report = {'added': 0, 'merged_exact': 0, 'merged_fuzzy': [], 'conflicts': [], 'skipped': []}

    for batch in batches:
        for record in batch:
            key = record.get('key')
            if not key:
                report['skipped'].append({'reason': 'record has no key', 'record': record})
                continue
            if key in corpus:
                corpus[key] = _merge_one(corpus[key], record, report)
                report['merged_exact'] += 1
                continue
            identity = fuzzy_key(record)
            twin = by_fuzzy.get(identity) if identity is not None else None
            if twin:
                report['merged_fuzzy'].append({'incoming': key, 'into': twin,
                                               'rule': 'provider+instrument+asof+kind'})
                corpus[twin] = _merge_one(corpus[twin], record, report)
                continue
            corpus[key] = dict(record)
            if identity is not None:
                by_fuzzy[identity] = key
            report['added'] += 1
    return corpus, report


def save(root: Path, corpus: dict) -> None:
    """The only write of market/observations.json. merge() stays pure so a dry run is honest."""
    write_json(root / OBSERVATIONS, corpus)


def stats(corpus: dict) -> dict:
    by_tier: dict[str, int] = {}
    by_provider: dict[str, int] = {}
    for record in corpus.values():
        by_tier[record.get('tier') or '?'] = by_tier.get(record.get('tier') or '?', 0) + 1
        for provider in record.get('sources') or [record.get('provider')]:
            by_provider[provider] = by_provider.get(provider, 0) + 1
    return {'records': len(corpus), 'by_tier': dict(sorted(by_tier.items())),
            'by_provider': dict(sorted(by_provider.items())),
            'forecasts': sum(1 for r in corpus.values() if r.get('is_forecast'))}


def main(argv=None) -> int:
    safe_console()
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--root', type=Path, default=Path.cwd())
    sub = parser.add_subparsers(dest='command', required=True)
    merger = sub.add_parser('merge')
    merger.add_argument('--records', type=Path, action='append', required=True)
    merger.add_argument('--dry-run', action='store_true')
    merger.add_argument('--json', action='store_true')
    sub.add_parser('stats')
    getter = sub.add_parser('get')
    getter.add_argument('--key', required=True)
    args = parser.parse_args(argv)

    try:
        if args.command == 'stats':
            print(json.dumps(stats(load(args.root)), indent=2))
            return 0
        if args.command == 'get':
            corpus = load(args.root)
            if args.key not in corpus:
                raise StateError(f'no such record: {args.key}')
            print(json.dumps(corpus[args.key], indent=2, ensure_ascii=False))
            return 0

        batches = []
        for path in args.records:
            batch = read_json(path, str(path))
            if not isinstance(batch, list):
                raise StateError(f'{path}: expected a JSON array of records')
            batches.append(batch)
        corpus, report = merge(args.root, batches)
        if not args.dry_run:
            save(args.root, corpus)
        if args.json:
            print(json.dumps(report, indent=2, ensure_ascii=False))
        else:
            print(f"merge: {report['added']} added, {report['merged_exact']} merged on key, "
                  f"{len(report['merged_fuzzy'])} merged fuzzily, "
                  f"{len(report['conflicts'])} conflict(s)"
                  + (' [dry run, nothing written]' if args.dry_run else ''))
            for item in report['merged_fuzzy']:
                print(f"  fuzzy: {item['incoming']} -> {item['into']} ({item['rule']})")
            for item in report['conflicts']:
                print(f"  conflict on {item['key']}.{item['field']}: kept {item['kept']!r}, "
                      f"rejected {item['rejected']!r} from {item['from']}")
            for item in report['skipped']:
                print(f"  skipped: {item['reason']}")
        return 0
    except StateError as exc:
        print(f'market_merge: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
