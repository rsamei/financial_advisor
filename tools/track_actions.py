#!/usr/bin/env python3
"""Record what the user actually did, snapshot what they actually hold, and score old advice.

Usage (from the repository root):
    python tools/track_actions.py record   --action <file>
    python tools/track_actions.py list     [--limit 20]
    python tools/track_actions.py verify
    python tools/track_actions.py snapshot --holdings <file> [--as-of YYYY-MM-DD]
    python tools/track_actions.py review   --decision D-001 --benchmark <file> [--as-of ...]

`record` is deliberately **absent from the permission allowlist**. It writes the permanent record of
what someone did with their own money; that should cross a human's desk every time.

What this tool enforces that prose cannot:

- **The action ledger is append-only and hash-chained.** A correction is a new event. Editing a
  past action would rewrite the only honest input this system has about its own performance.
- **`snapshot` may touch exactly two arrays** of `profile/balance_sheet.json` - `assets` and
  `holdings` - and nothing else. Cash flow, liabilities, pension, insurance and the source ledger
  change when the user's life changes, which is a `/setup --refresh` event, not a market-value
  refresh. The tool refuses a snapshot that would alter anything else.
- **Review arithmetic uses recorded observations only.** A missing price exits 8 and says which
  price is missing. It never estimates, interpolates or "assumes flat" - a made-up benchmark would
  make the system's self-assessment a fiction, which is worse than having no self-assessment.
- **Process and outcome are scored separately**, and the pairing is reported explicitly: a good
  outcome from a bad process is luck, and this is the one place in the repository where that
  distinction is the whole point.

Stdlib only. Exit 0 ok; 1 state or schema error; 4 bad arguments; 8 a required recorded price is
missing.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fa_state import (StateError, append_chain, read_chain, read_json, safe_console,  # noqa: E402
                      today, write_json)

ACTIONS = Path('track') / 'actions.jsonl'
SNAPSHOTS = Path('track') / 'snapshots'
BALANCE = Path('profile') / 'balance_sheet.json'
OBSERVATIONS = Path('market') / 'observations.json'
MISSING_PRICE = 8

ACTION_ID = re.compile(r'A-\d{8}-\d{2}')
WHAT = ('buy', 'sell', 'deposit', 'withdraw', 'repay_debt', 'contribute', 'rebalance',
        'insure', 'other')
# The only two arrays a snapshot may replace (02 and plan section 7).
SNAPSHOT_FIELDS = ('assets', 'holdings')
# Process and outcome are scored independently; the pair is the verdict.
VERDICTS = ('good_process/good_outcome', 'good_process/bad_outcome',
            'bad_process/good_outcome', 'bad_process/bad_outcome')


def next_action_id(events: list[dict], when: str) -> str:
    stamp = when.replace('-', '')
    used = [event['payload']['action_id'] for event in events
            if event.get('payload', {}).get('action_id', '').startswith(f'A-{stamp}')]
    return f'A-{stamp}-{len(used) + 1:02d}'


def record(root: Path, action: dict) -> dict:
    """Append one action. Everything about it is what the user reports, not what was recommended."""
    required = ('what', 'executed_on')
    missing = [field for field in required if not action.get(field)]
    if missing:
        raise StateError(f'action is missing {", ".join(missing)}')
    if action['what'] not in WHAT:
        raise StateError(f'what: {action["what"]!r} is not one of {", ".join(WHAT)}')
    try:
        dt.date.fromisoformat(str(action['executed_on']))
    except ValueError:
        raise StateError(f'executed_on: {action["executed_on"]!r} is not an ISO date') from None
    for field in ('units', 'price', 'fees'):
        value = action.get(field)
        if value is not None and (not isinstance(value, (int, float)) or isinstance(value, bool)):
            raise StateError(f'{field}: expected a number or null, got {value!r}')

    path = Path(root) / ACTIONS
    events = read_chain(path, 'track/actions.jsonl')
    payload = {
        'action_id': action.get('action_id') or next_action_id(events, str(action['executed_on'])),
        'decision_id': action.get('decision_id'),
        'executed_on': str(action['executed_on']),
        'what': action['what'],
        'instrument': action.get('instrument'),
        'units': action.get('units'),
        'price': action.get('price'),
        'fees': action.get('fees'),
        'currency': action.get('currency') or 'EUR',
        'account': action.get('account'),
        'note': action.get('note'),
    }
    if not ACTION_ID.fullmatch(payload['action_id']):
        raise StateError(f'action_id {payload["action_id"]!r} must look like A-YYYYMMDD-NN')
    if any(event['payload']['action_id'] == payload['action_id'] for event in events):
        raise StateError(f'{payload["action_id"]} is already recorded; a correction is a new '
                         'action, never an edit')
    return append_chain(path, events, 'action', payload)


def snapshot(root: Path, holdings: dict, as_of: str | None = None) -> dict:
    """Refresh the two market-value arrays in 02, and write a dated snapshot file.

    Refuses anything that would change a field outside `assets` and `holdings`, because the one
    thing that makes this second writer safe is that its reach is small and checked.
    """
    as_of = as_of or today()
    try:
        dt.date.fromisoformat(as_of)
    except ValueError:
        raise StateError(f'as_of: {as_of!r} is not an ISO date') from None
    if not isinstance(holdings, dict):
        raise StateError('the snapshot must be an object with "assets" and/or "holdings"')
    extra = set(holdings) - set(SNAPSHOT_FIELDS)
    if extra:
        raise StateError(
            f'a snapshot may only refresh {" and ".join(SNAPSHOT_FIELDS)}; it was asked to change '
            f'{sorted(extra)}. Cash flow, liabilities, pension and insurance change when the '
            "user's life changes - that is `/setup --refresh`, not a market-value refresh.")

    path = Path(root) / BALANCE
    balance = read_json(path, 'balance_sheet.json')
    before = {field: balance.get(field) for field in SNAPSHOT_FIELDS}
    untouched = {k: v for k, v in balance.items() if k not in SNAPSHOT_FIELDS}

    for field in SNAPSHOT_FIELDS:
        if field not in holdings:
            continue
        rows = holdings[field]
        if not isinstance(rows, list):
            raise StateError(f'{field} must be a list of rows')
        for row in rows:
            row.setdefault('as_of', as_of)
        balance[field] = rows
    balance['as_of'] = as_of

    # Nothing outside the two arrays and `as_of` may differ. Checked, not trusted.
    after_untouched = {k: v for k, v in balance.items()
                       if k not in (*SNAPSHOT_FIELDS, 'as_of')}
    changed = [k for k, v in after_untouched.items() if untouched.get(k) != v]
    if changed:
        raise StateError(f'a snapshot changed {changed}, which it must never do')

    write_json(path, balance)
    snapshot_path = Path(root) / SNAPSHOTS / f'{as_of}.json'
    write_json(snapshot_path, {'as_of': as_of, 'recorded_at': today(),
                               **{field: balance.get(field) for field in SNAPSHOT_FIELDS}})
    return {'as_of': as_of, 'snapshot': str(snapshot_path),
            'assets_before': len(before['assets'] or []),
            'assets_after': len(balance.get('assets') or []),
            'holdings_before': len(before['holdings'] or []),
            'holdings_after': len(balance.get('holdings') or [])}


class MissingPrice(StateError):
    """A price the review needs was never recorded. Exit 8; never estimated."""


def recorded_price(root: Path, symbol: str, on: str, max_staleness_days: int | None = None) -> dict:
    """Find a recorded observation for this instrument on or before a date.

    Returns the observation and how stale it is. Raises rather than interpolating: a benchmark the
    system invented would make its own scorecard fiction.

    `max_staleness_days` bounds how far back the fallback may reach. Scoring passes it, because
    without a bound a six-month-old price silently becomes "the price today" and a view that missed
    completely is scored as having been exactly right. An unbounded lookup is fine for a start
    price near a known trade date; it is never fine for an end price.
    """
    path = Path(root) / OBSERVATIONS
    if not path.is_file():
        raise MissingPrice(f'no recorded observations, so no price for {symbol} on {on}. '
                           'Run /market, or record the price by hand before reviewing.')
    corpus = read_json(path, 'market/observations.json')
    target = dt.date.fromisoformat(on)
    best = None
    for record_ in corpus.values():
        if (record_.get('symbol') or '').upper() != symbol.upper():
            continue
        if record_.get('value') is None or not record_.get('asof'):
            continue
        try:
            when = dt.date.fromisoformat(str(record_['asof'])[:10])
        except ValueError:
            continue
        if when > target:
            continue
        if best is None or when > best[0]:
            best = (when, record_)
    if best is None:
        raise MissingPrice(
            f'no recorded price for {symbol} on or before {on}. This review will not estimate one: '
            'a made-up benchmark would make the scorecard fiction. Pull the price with /market, '
            'or say in the review that the comparison could not be made.')
    when, record_ = best
    age = (target - when).days
    if max_staleness_days is not None and age > max_staleness_days:
        raise MissingPrice(
            f'the newest recorded {symbol} price on or before {on} is from {when} - {age} days '
            f'earlier, and this comparison allows {max_staleness_days}. Using it would report a '
            'stale price as the price on the day. Pull a fresh one with /market.')
    return {'symbol': symbol, 'asof': when.isoformat(), 'value': record_['value'],
            'ev_key': record_.get('key'), 'days_before_target': age}


def review(root: Path, *, decision_id: str, symbol: str, bought_on: str, as_of: str,
           units: float, price_paid: float, fees: float, benchmark_symbol: str) -> dict:
    """Outcome arithmetic in EUR, after costs, against a benchmark and against doing nothing."""
    start = recorded_price(root, symbol, bought_on)
    end = recorded_price(root, symbol, as_of)
    bench_start = recorded_price(root, benchmark_symbol, bought_on)
    bench_end = recorded_price(root, benchmark_symbol, as_of)

    invested = units * price_paid + fees
    now_value = units * end['value']
    action_result = now_value - invested
    bench_return = (bench_end['value'] - bench_start['value']) / bench_start['value']
    bench_result = invested * bench_return
    # Doing nothing means the money stayed where it was: no gain, and the fees never spent.
    do_nothing_result = 0.0

    return {
        'decision_id': decision_id,
        'window': {'from': bought_on, 'to': as_of},
        'invested_eur': round(invested, 2),
        'value_now_eur': round(now_value, 2),
        'action_result_eur': round(action_result, 2),
        'benchmark': {'symbol': benchmark_symbol, 'return_pct': round(bench_return * 100, 2),
                      'result_eur': round(bench_result, 2),
                      'ev_keys': [bench_start['ev_key'], bench_end['ev_key']]},
        'do_nothing_result_eur': do_nothing_result,
        'vs_benchmark_eur': round(action_result - bench_result, 2),
        'vs_do_nothing_eur': round(action_result - do_nothing_result, 2),
        'prices_used': [start, end, bench_start, bench_end],
        'fees_eur': fees,
        'note': 'outcome only. Whether the process was sound is judged separately, on what was '
                'knowable at the time - see 11-outcome-review.md.',
    }


def main(argv=None) -> int:
    safe_console()
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--root', type=Path, default=Path.cwd())
    parser.add_argument('--json', action='store_true')
    sub = parser.add_subparsers(dest='command', required=True)
    recorder = sub.add_parser('record')
    recorder.add_argument('--action', type=Path, required=True)
    lister = sub.add_parser('list')
    lister.add_argument('--limit', type=int, default=20)
    sub.add_parser('verify')
    snapshotter = sub.add_parser('snapshot')
    snapshotter.add_argument('--holdings', type=Path, required=True)
    snapshotter.add_argument('--as-of', dest='as_of')
    reviewer = sub.add_parser('review')
    reviewer.add_argument('--decision', required=True)
    reviewer.add_argument('--symbol', required=True)
    reviewer.add_argument('--benchmark', required=True)
    reviewer.add_argument('--bought-on', dest='bought_on', required=True)
    reviewer.add_argument('--as-of', dest='as_of', required=True)
    reviewer.add_argument('--units', type=float, required=True)
    reviewer.add_argument('--price-paid', dest='price_paid', type=float, required=True)
    reviewer.add_argument('--fees', type=float, default=0.0)

    args = parser.parse_args(argv)
    try:
        if args.command == 'record':
            event = record(args.root, read_json(args.action, 'action'))
            payload = event['payload']
            print(f"recorded {payload['action_id']}: {payload['what']} "
                  f"{payload.get('instrument') or ''} on {payload['executed_on']}")
            print(f"chain hash {event['hash'][:16]}")
            return 0

        if args.command == 'list':
            events = read_chain(Path(args.root) / ACTIONS, 'track/actions.jsonl')
            if not events:
                print('no actions recorded')
                return 0
            for event in events[-args.limit:]:
                payload = event['payload']
                print(f"{payload['executed_on']}  {payload['action_id']}  {payload['what']:10} "
                      f"{payload.get('instrument') or '-':16} "
                      f"decision {payload.get('decision_id') or '-'}")
            return 0

        if args.command == 'verify':
            events = read_chain(Path(args.root) / ACTIONS, 'track/actions.jsonl')
            print(f'track_actions: chain intact, {len(events)} action(s) recorded')
            return 0

        if args.command == 'snapshot':
            result = snapshot(args.root, read_json(args.holdings, 'snapshot'), args.as_of)
            print(json.dumps(result, indent=2) if args.json else
                  f"snapshot {result['as_of']}: {result['holdings_after']} holding(s), "
                  f"{result['assets_after']} asset row(s) -> {result['snapshot']}")
            return 0

        result = review(args.root, decision_id=args.decision, symbol=args.symbol,
                        bought_on=args.bought_on, as_of=args.as_of, units=args.units,
                        price_paid=args.price_paid, fees=args.fees,
                        benchmark_symbol=args.benchmark)
        print(json.dumps(result, indent=2))
        return 0
    except MissingPrice as exc:
        print(f'track_actions: {exc}', file=sys.stderr)
        return MISSING_PRICE
    except StateError as exc:
        print(f'track_actions: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
