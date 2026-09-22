#!/usr/bin/env python3
"""Convert an amount between currencies using a RECORDED ECB reference rate, never a remembered one.

Usage (from the repository root):
    python tools/fx.py convert --amount 1000 --from USD --to EUR [--rates <file>] [--on YYYY-MM-DD]
    python tools/fx.py rates [--rates <file>]

What this tool enforces that prose cannot:

- **A conversion without a recorded rate fails (exit 8).** The model knowing roughly what EUR/USD
  is worth is not evidence. Every converted figure must trace to an observation with an `EV-` key,
  and that key travels with the result so a report can cite it.
- **A stale rate is refused, not quietly used.** Daily FX older than the freshness window
  (1 day for rates and FX, per 05-market-intelligence.md, widened to 4 days so a Monday conversion
  can use Friday's fix) fails with the same exit code unless `--allow-stale` is given, which marks
  the result `stale: true` so the caller must decide in the open.
- **Cross rates go through EUR only.** The ECB publishes rates against EUR; USD->GBP is computed as
  USD->EUR->GBP and the result names both legs. No other path is invented.

The rates file is written by `/market` from the ECB provider (Phase 2) and holds one record per
currency:

    {"as_of": "2026-09-19", "base": "EUR",
     "rates": {"USD": {"rate": 1.0842, "ev_key": "EV-1a2b3c4d", "as_of": "2026-09-19"}}}

`rate` is units of that currency per 1 EUR, which is how the ECB publishes it.

Stdlib only, and no network: this tool reads what `/market` already recorded.
Exit 0 ok; 1 state or schema error; 4 bad arguments; 8 missing or stale recorded rate.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fa_state import StateError, read_json, safe_console  # noqa: E402

DEFAULT_RATES = Path('market') / 'fx_rates.json'
BASE = 'EUR'
MAX_AGE_DAYS = 4
MISSING_RATE = 8


class RateError(StateError):
    """No recorded rate, or the recorded rate is too old. Exit code 8."""


def load_rates(path: Path) -> dict:
    data = read_json(path, 'recorded FX rates')
    if not isinstance(data, dict) or not isinstance(data.get('rates'), dict):
        raise StateError(f'{path}: expected an object with a "rates" mapping')
    if data.get('base', BASE) != BASE:
        raise StateError(f'{path}: rates must be quoted against {BASE}, got {data.get("base")!r}')
    return data


def _leg(rates: dict, currency: str, today: dt.date, allow_stale: bool) -> dict:
    """One currency's rate against EUR, with its provenance and its age."""
    if currency == BASE:
        return {'currency': BASE, 'rate': 1.0, 'ev_key': None, 'as_of': None, 'age_days': 0}
    record = rates['rates'].get(currency)
    if record is None:
        raise RateError(
            f'no recorded rate for {currency}. Run /market to record ECB reference rates; this '
            'tool never invents or remembers a rate.')
    for field in ('rate', 'ev_key', 'as_of'):
        if record.get(field) in (None, ''):
            raise StateError(f'recorded rate for {currency} is missing {field!r}')
    try:
        as_of = dt.date.fromisoformat(str(record['as_of']))
    except ValueError:
        raise StateError(f'recorded rate for {currency}: as_of is not an ISO date') from None
    age = (today - as_of).days
    if age > MAX_AGE_DAYS and not allow_stale:
        raise RateError(
            f'the recorded {currency} rate is {age} days old (as of {as_of}); the window is '
            f'{MAX_AGE_DAYS} days. Refresh with /market, or pass --allow-stale to convert anyway '
            'and have the result marked stale.')
    return {'currency': currency, 'rate': float(record['rate']), 'ev_key': record['ev_key'],
            'as_of': as_of.isoformat(), 'age_days': age}


def convert(amount: float, source: str, target: str, rates: dict, today: dt.date,
            allow_stale: bool = False) -> dict:
    """source -> EUR -> target. The ECB quotes per EUR, so the source leg divides."""
    source_leg = _leg(rates, source, today, allow_stale)
    target_leg = _leg(rates, target, today, allow_stale)
    in_eur = amount / source_leg['rate']
    result = in_eur * target_leg['rate']
    ev_keys = [leg['ev_key'] for leg in (source_leg, target_leg) if leg['ev_key']]
    age = max(source_leg['age_days'], target_leg['age_days'])
    return {
        'amount': amount, 'from': source, 'to': target,
        'converted': round(result, 2),
        'rate_used': round(target_leg['rate'] / source_leg['rate'], 6),
        'via': BASE if BASE not in (source, target) else None,
        'legs': [source_leg, target_leg],
        'ev_keys': ev_keys,
        'as_of': min((leg['as_of'] for leg in (source_leg, target_leg) if leg['as_of']),
                     default=None),
        'stale': age > MAX_AGE_DAYS,
    }


def main(argv=None) -> int:
    safe_console()
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--root', type=Path, default=Path.cwd())
    parser.add_argument('--rates', type=Path, default=None)
    parser.add_argument('--on', default=None, help='override the run date (tests, replay)')
    sub = parser.add_subparsers(dest='command', required=True)
    convert_parser = sub.add_parser('convert')
    convert_parser.add_argument('--amount', type=float, required=True)
    convert_parser.add_argument('--from', dest='source', required=True)
    convert_parser.add_argument('--to', dest='target', default=BASE)
    convert_parser.add_argument('--allow-stale', action='store_true')
    convert_parser.add_argument('--json', action='store_true')
    sub.add_parser('rates')
    args = parser.parse_args(argv)

    try:
        today = dt.date.fromisoformat(args.on) if args.on else dt.date.today()
    except ValueError:
        print(f'fx: --on is not an ISO date: {args.on!r}', file=sys.stderr)
        return 4

    path = args.rates or (args.root / DEFAULT_RATES)
    try:
        rates = load_rates(path)
        if args.command == 'rates':
            print(f"recorded {rates.get('base', BASE)} rates as of {rates.get('as_of')}:")
            for currency, record in sorted(rates['rates'].items()):
                print(f"  {currency}: {record.get('rate')} ({record.get('as_of')}, "
                      f"{record.get('ev_key')})")
            return 0

        source = args.source.upper()
        target = args.target.upper()
        for code in (source, target):
            if not (len(code) == 3 and code.isalpha()):
                print(f'fx: not an ISO-4217 currency code: {code!r}', file=sys.stderr)
                return 4
        result = convert(args.amount, source, target, rates, today, args.allow_stale)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            keys = ', '.join(result['ev_keys']) or 'none'
            note = ' [STALE]' if result['stale'] else ''
            print(f"{result['amount']} {source} = {result['converted']} {target} "
                  f"@ {result['rate_used']} (as of {result['as_of']}, {keys}){note}")
        return 0
    except RateError as exc:
        print(f'fx: {exc}', file=sys.stderr)
        return MISSING_RATE
    except StateError as exc:
        print(f'fx: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
