#!/usr/bin/env python3
"""Run a provider through the registry, cache its response, and record the pull so it can be replayed.

Usage (from the repository root):
    python tools/market_retrieve.py list [--tier M]
    python tools/market_retrieve.py pull --provider ecb --arg series=FM.B.U2... --arg last=12 \
        --query Q-2026-09-20-macro --mode use|live|offline [--out <file>]
    python tools/market_retrieve.py coverage --query Q-... --claim "..." --records <file> [--tier M]
    python tools/market_retrieve.py freshness

What this tool enforces that prose cannot:

- **The provider list is a hard-coded registry.** A command cannot reach a source that is not in
  the table below, and cannot pass it a free-form URL. Discovery through a web tool is allowed, but
  a discovered URL only counts once `tools/evidence_memory.py add --url` re-fetches and snapshots
  it - otherwise a claim would rest on something nobody can replay.
- **A cache hit never looks fresh.** The response body and its metadata are stored separately: a
  replay keeps the ORIGINAL `fetched_at` and sets `cache: true`, so nothing downstream can mistake
  a week-old cached pull for this morning's.
- **A failure is a failure.** `failed` and `throttled` pulls are recorded with their reason and
  contribute nothing to coverage. There is no path by which a source that did not answer becomes
  a source that answered "nothing".
- **Coverage is arithmetic, not judgement.** c1 counts distinct *operators*, not URLs; c2 checks
  each record against its tier's window; c3 needs a primary source; c4 fails on any forecast.
  Anything short of all four is UNDETERMINED, which is a FLAG - never a quiet PASS.
- **Secrets never enter the record.** Provider argv is recorded verbatim in `market/queries.json`,
  which is tracked by git, so a provider reads its key from the environment and never from argv.

Stdlib only. Exit 0 ok; 1 state or schema error; 2 provider failure; 3 throttled; 4 bad arguments.
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
                      write_json)
from providers import (base, cnn_fear_greed, coingecko, congress_trades, ecb,  # noqa: E402
                       eurostat, finra_short, fred, istat, reddit_json, rss, sec_edgar,
                       stooq, yahoo_chart)

# The registry. `primary` means the source publishes the fact itself (an official statistic, a
# filing, an exchange price) rather than reporting someone else's. Only a primary source can
# satisfy c3.
REGISTRY = {
    'ecb': {'module': ecb, 'primary': True},
    'fred': {'module': fred, 'primary': True},
    'eurostat': {'module': eurostat, 'primary': True},
    # The institute that computes the Italian HICP, rather than a body that republishes it.
    'istat': {'module': istat, 'primary': True},
    'stooq': {'module': stooq, 'primary': True},
    # An unofficial endpoint, so a fallback rather than a reference: where an official source
    # publishes the same number (the ECB FX fix), that source wins.
    'yahoo_chart': {'module': yahoo_chart, 'primary': False},
    'coingecko': {'module': coingecko, 'primary': False},   # an aggregator of exchange prices
    'sec_edgar': {'module': sec_edgar, 'primary': True},
    'rss': {'module': rss, 'primary': False},               # a report about a fact, not the fact
    'congress_trades': {'module': congress_trades, 'primary': True},   # the filed disclosure
    'finra_short': {'module': finra_short, 'primary': True},           # the published tape
    # Sentiment sources are never primary for anything except sentiment itself, and a claim that
    # rests on them alone is UNDETERMINED by construction.
    'cnn_fear_greed': {'module': cnn_fear_greed, 'primary': False},
    'reddit_json': {'module': reddit_json, 'primary': False},
}

# Freshness windows in days, by tier (plan section 4, table 05). Rates and FX inside tier M are
# daily series and get the tight window; monthly statistics get the wide one.
FRESHNESS_DAYS = {'M': 35, 'A': 1, 'S': 7, 'I': 14, 'P': 7}
DAILY_KINDS = ('price',)
DAILY_WINDOW = 1
# Markets close at weekends: a Monday pull of Friday's close must not be "stale".
MARKET_GRACE_DAYS = 4

CACHE = Path('.cache') / 'market'
QUERIES = Path('market') / 'queries.json'
QUERY_ID_PREFIX = 'Q-'
MODES = ('use', 'live', 'offline')


def period_end(asof: str) -> dt.date | None:
    """The last day an `asof` period covers: 2026-09-19, 2026-09 or 2026Q3 all resolve.

    A statistical release is dated by its PERIOD, not by a day. Treating "2026-09" as unparseable
    (or, worse, as the first of the month) would make every monthly print look either broken or a
    month staler than it is. The period's end is the honest reading: the observation covers up to
    that day and no further.
    """
    text = str(asof).strip()
    try:
        return dt.date.fromisoformat(text[:10])
    except ValueError:
        pass
    match = re.fullmatch(r'(\d{4})-(\d{2})', text)
    if match:
        year, month = int(match.group(1)), int(match.group(2))
        return dt.date(year + month // 12, month % 12 + 1, 1) - dt.timedelta(days=1)
    match = re.fullmatch(r'(\d{4})[-]?Q([1-4])', text, re.IGNORECASE)
    if match:
        year, quarter = int(match.group(1)), int(match.group(2))
        month = quarter * 3
        return dt.date(year + month // 12, month % 12 + 1, 1) - dt.timedelta(days=1)
    if re.fullmatch(r'\d{4}', text):
        return dt.date(int(text), 12, 31)
    return None


def window_days(tier: str, kind: str | None = None) -> int:
    if tier not in FRESHNESS_DAYS:
        raise StateError(f'unknown tier {tier!r}')
    if kind in DAILY_KINDS or tier == 'A':
        return DAILY_WINDOW + MARKET_GRACE_DAYS
    return FRESHNESS_DAYS[tier]


def operator_for(provider: str, args: dict) -> str:
    """Who actually runs the service for THIS pull.

    A module that serves several institutions - the RSS reader is the obvious one - exposes
    `operator_for(args)`, because two feeds from one central bank are one source, and a central
    bank and a finance ministry are two. Counting the module instead would make every feed in the
    table interchangeable, which is exactly the mistake c1 exists to prevent.
    """
    module = REGISTRY[provider]['module']
    hook = getattr(module, 'operator_for', None)
    return hook(args) if callable(hook) else module.CONTRACT['operator']


def cache_key(provider: str, args: dict) -> str:
    return canonical_digest({'provider': provider, 'args': {k: str(v) for k, v in sorted(args.items())}})


def _paths(root: Path, digest: str) -> tuple[Path, Path]:
    """Body and metadata live in separate files, on purpose: a body cannot carry its own freshness."""
    folder = root / CACHE
    return folder / f'{digest}.json', folder / f'{digest}.meta.json'


def read_cache(root: Path, digest: str) -> tuple[list | None, dict | None]:
    body_path, meta_path = _paths(root, digest)
    if not body_path.is_file() or not meta_path.is_file():
        return None, None
    try:
        return (json.loads(body_path.read_text(encoding='utf-8')),
                json.loads(meta_path.read_text(encoding='utf-8')))
    except (OSError, json.JSONDecodeError):
        return None, None


def write_cache(root: Path, digest: str, provider: str, args: dict, records: list) -> dict:
    body_path, meta_path = _paths(root, digest)
    meta = {'digest': digest, 'provider': provider, 'args': args, 'fetched_at': now_utc(),
            'records': len(records), 'response_digest': canonical_digest(records)}
    write_json(body_path, records)
    write_json(meta_path, meta)
    return meta


def cache_age_days(meta: dict, today: dt.date) -> float | None:
    try:
        fetched = dt.datetime.fromisoformat(meta['fetched_at']).date()
    except (KeyError, ValueError):
        return None
    return (today - fetched).days


def pull(root: Path, provider: str, args: dict, *, mode: str = 'use', today: dt.date | None = None,
         transport=None) -> tuple[list[dict], dict]:
    """Return (records, pull-record). Raises ProviderError on failure; never returns [] on error."""
    if provider not in REGISTRY:
        raise StateError(f'unknown provider {provider!r}. Registered: ' + ', '.join(sorted(REGISTRY)))
    if mode not in MODES:
        raise StateError(f'unknown mode {mode!r}; expected one of {", ".join(MODES)}')
    module = REGISTRY[provider]['module']
    tier = module.CONTRACT['tier']
    operator = operator_for(provider, args)
    today = today or dt.date.today()
    digest = cache_key(provider, args)
    argv = [item for key, value in sorted(args.items()) for item in (f'--{key}', str(value))]
    base_record = {'provider': provider, 'operator': operator, 'tier': tier,
                   'argv': argv, 'mode': mode, 'cache_key': digest}

    if mode in ('use', 'offline'):
        cached, meta = read_cache(root, digest)
        age = cache_age_days(meta, today) if meta else None
        fresh = age is not None and age <= window_days(tier)
        if cached is not None and (fresh or mode == 'offline'):
            # The body is replayed as recorded; only `cache` is flipped. fetched_at stays the
            # moment the data actually arrived, which is the whole point of the split file.
            records = [dict(item, cache=True) for item in cached]
            return records, base_record | {
                'status': 'cached', 'records': len(records), 'fetched_at': meta['fetched_at'],
                'cache_age_days': age, 'response_digest': meta.get('response_digest')}
        if mode == 'offline':
            raise base.ProviderError(
                f'{provider}: offline mode and no cached response for these arguments', 'failed')

    try:
        records = module.fetch(args, transport)
    except base.ProviderError as exc:
        raise
    except Exception as exc:                        # a parser bug is a provider failure, not a crash
        raise base.ProviderError(f'{provider}: unexpected error: {exc}') from None
    meta = write_cache(root, digest, provider, args, records)
    return records, base_record | {'status': 'ok', 'records': len(records),
                                   'fetched_at': meta['fetched_at'], 'cache_age_days': 0,
                                   'response_digest': meta['response_digest']}


def failed_pull(provider: str, args: dict, mode: str, exc: base.ProviderError) -> dict:
    module = REGISTRY.get(provider, {}).get('module')
    return {'provider': provider,
            'operator': module.CONTRACT['operator'] if module else None,
            'tier': module.CONTRACT['tier'] if module else None,
            'argv': [item for key, value in sorted(args.items())
                     for item in (f'--{key}', str(value))],
            'mode': mode, 'status': exc.status, 'records': 0, 'fetched_at': now_utc(),
            'reason': str(exc)}


def record_query(root: Path, query_id: str, tier: str, pull_record: dict,
                 requested_by: str = '/market', workflow_id: str | None = None) -> dict:
    """Append one pull to market/queries.json, the tracked, replayable provenance file."""
    if not query_id.startswith(QUERY_ID_PREFIX):
        raise StateError(f'query id must start with {QUERY_ID_PREFIX}: {query_id!r}')
    path = root / QUERIES
    queries = read_json(path, 'market/queries.json') if path.is_file() else {}
    entry = queries.setdefault(query_id, {'tier': tier, 'requested_by': requested_by,
                                          'pulls': [], 'coverage': None,
                                          'workflow_id': workflow_id})
    for key, value in (('requested_by', requested_by), ('workflow_id', workflow_id)):
        if value and not entry.get(key):
            entry[key] = value
    entry['pulls'].append(pull_record)
    write_json(path, queries)
    return entry


def coverage(records: list[dict], pulls: list[dict], tier: str,
             today: dt.date | None = None) -> dict:
    """c1-c4 from 05-market-intelligence.md. Every branch here is arithmetic on recorded data.

    c1 counts distinct operators among SUCCESSFUL pulls that returned at least one record. Two
    mirrors of one feed share an operator and count once; a failed source counts zero, however
    loudly it failed.
    """
    today = today or dt.date.today()
    successful = [p for p in pulls if p.get('status') in ('ok', 'cached') and p.get('records')]
    operators = sorted({p['operator'] for p in successful if p.get('operator')})
    c1 = len(operators)

    stale: list[str] = []
    for item in records:
        asof = item.get('asof')
        if not asof:
            stale.append(f'{item.get("key")}: no asof date')
            continue
        when = period_end(asof)
        if when is None:
            stale.append(f'{item.get("key")}: unparseable asof {asof!r}')
            continue
        limit = window_days(item.get('tier') or tier, item.get('kind'))
        age = (today - when).days
        if age > limit:
            stale.append(f'{item.get("key")}: {age} days old, window is {limit}')

    primary = sorted({p['provider'] for p in successful
                      if REGISTRY.get(p['provider'], {}).get('primary')})
    forecasts = sorted({item.get('key') for item in records if item.get('is_forecast')})

    result = {
        'c1_independent_operators': c1, 'operators': operators,
        'c2_within_freshness': not stale, 'stale': stale,
        'c3_primary_source': bool(primary), 'primary_providers': primary,
        'c4_observation_not_forecast': not forecasts, 'forecast_keys': forecasts,
        'failed_sources': [{'provider': p['provider'], 'status': p['status'],
                            'reason': p.get('reason')} for p in pulls
                           if p.get('status') not in ('ok', 'cached')],
    }
    result['verdict'] = ('SUPPORTED' if c1 >= 3 and not stale and primary and not forecasts
                         else 'UNDETERMINED')
    result['why'] = _why(result)
    return result


def _why(result: dict) -> list[str]:
    reasons = []
    if result['c1_independent_operators'] < 3:
        reasons.append(f"c1: {result['c1_independent_operators']} independent operator(s), needs 3"
                       " (failed sources never count)")
    if result['stale']:
        reasons.append('c2: outside the freshness window - ' + '; '.join(result['stale']))
    if not result['c3_primary_source']:
        reasons.append('c3: no primary source - a report about a fact is not the fact')
    if result['forecast_keys']:
        reasons.append('c4: rests on a forecast - ' + ', '.join(result['forecast_keys']))
    return reasons


def set_coverage(root: Path, query_id: str, claim: str, result: dict) -> None:
    path = root / QUERIES
    queries = read_json(path, 'market/queries.json')
    if query_id not in queries:
        raise StateError(f'no such query: {query_id}')
    queries[query_id]['coverage'] = dict(result, claim=claim, computed_at=now_utc())
    write_json(path, queries)


# --- CLI --------------------------------------------------------------------------------------

def _args(pairs: list[str]) -> dict:
    args = {}
    for pair in pairs:
        key, sep, value = pair.partition('=')
        if not sep:
            raise StateError(f'--arg expects key=value, got {pair!r}')
        args[key.strip().lstrip('-')] = value.strip()
    return args


def main(argv=None) -> int:
    safe_console()
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--root', type=Path, default=Path.cwd())
    parser.add_argument('--today', default=None)
    sub = parser.add_subparsers(dest='command', required=True)

    listing = sub.add_parser('list')
    listing.add_argument('--tier', choices=base.TIERS)
    sub.add_parser('freshness')

    puller = sub.add_parser('pull')
    puller.add_argument('--provider', required=True)
    puller.add_argument('--arg', action='append', default=[])
    puller.add_argument('--query', required=True)
    puller.add_argument('--mode', default='use', choices=MODES)
    puller.add_argument('--out', type=Path, default=None)
    puller.add_argument('--workflow', default=None)

    cover = sub.add_parser('coverage')
    cover.add_argument('--query', required=True)
    cover.add_argument('--claim', required=True)
    cover.add_argument('--records', type=Path, required=True)
    cover.add_argument('--json', action='store_true')

    args = parser.parse_args(argv)
    try:
        today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()
    except ValueError:
        print(f'market_retrieve: --today is not an ISO date: {args.today!r}', file=sys.stderr)
        return 4

    try:
        if args.command == 'list':
            for name in sorted(REGISTRY):
                contract = REGISTRY[name]['module'].CONTRACT
                if args.tier and contract['tier'] != args.tier:
                    continue
                flag = 'primary' if REGISTRY[name]['primary'] else 'secondary'
                print(f"{name:10} tier {contract['tier']}  operator {contract['operator']:16} "
                      f"{flag}  env: {', '.join(contract['needs_env']) or 'none'}")
                for option, help_text in contract['argv'].items():
                    print(f'    {option:14} {help_text}')
            return 0

        if args.command == 'freshness':
            for tier, days in sorted(FRESHNESS_DAYS.items()):
                print(f'{tier}: {days} days'
                      + (f" ({DAILY_WINDOW}+{MARKET_GRACE_DAYS} for prices)"
                         if tier in ('M', 'A') else ''))
            return 0

        if args.command == 'pull':
            provider_args = _args(args.arg)
            module = REGISTRY.get(args.provider, {}).get('module')
            tier = module.CONTRACT['tier'] if module else None
            try:
                records, pull_record = pull(args.root, args.provider, provider_args,
                                            mode=args.mode, today=today)
            except base.ProviderError as exc:
                record_query(args.root, args.query, tier, failed_pull(
                    args.provider, provider_args, args.mode, exc), workflow_id=args.workflow)
                print(f'market_retrieve: {exc}', file=sys.stderr)
                print(f'recorded as a {exc.status} source in market/queries.json; it counts '
                      'nothing toward coverage', file=sys.stderr)
                return exc.exit_code
            record_query(args.root, args.query, tier, pull_record, workflow_id=args.workflow)
            out = args.out or (args.root / CACHE / f'{pull_record["cache_key"]}.records.json')
            write_json(out, records)
            print(f"{args.provider}: {pull_record['status']}, {len(records)} record(s), "
                  f"fetched_at {pull_record['fetched_at']} -> {out}")
            return 0

        if args.command == 'coverage':
            records = read_json(args.records, 'records file')
            queries = read_json(args.root / QUERIES, 'market/queries.json')
            if args.query not in queries:
                raise StateError(f'no such query: {args.query}')
            entry = queries[args.query]
            result = coverage(records, entry['pulls'], entry.get('tier') or 'M', today)
            set_coverage(args.root, args.query, args.claim, result)
            if args.json:
                print(json.dumps(result, indent=2))
            else:
                print(f"{result['verdict']}: {args.claim}")
                print(f"  c1 operators: {result['c1_independent_operators']} "
                      f"({', '.join(result['operators']) or 'none'})")
                print(f"  c2 fresh: {result['c2_within_freshness']} | "
                      f"c3 primary: {result['c3_primary_source']} | "
                      f"c4 observation: {result['c4_observation_not_forecast']}")
                for reason in result['why']:
                    print(f'  - {reason}')
                for failure in result['failed_sources']:
                    print(f"  failed: {failure['provider']} ({failure['status']})")
            return 0
        return 4
    except StateError as exc:
        print(f'market_retrieve: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
