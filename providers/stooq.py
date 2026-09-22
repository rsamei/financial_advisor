"""Stooq daily OHLC CSV - prices and historical windows for equities, ETFs, indices and FX.

Tier A. Operator: stooq. No key required.

    fetch({'symbol': 'spy.us', 'last': 250})

Stooq symbols carry a market suffix (`.us`, `.de`, `.pl`); an unsuffixed symbol usually resolves to
a Polish listing, which is rarely what the caller meant. This module refuses a symbol without a
suffix rather than fetching the wrong instrument and labelling it confidently.

A close is an observation, not a valuation. What it supports is "the price on this date was X" -
nothing about whether that price is reasonable.
"""
from __future__ import annotations

from . import base

CONTRACT = {
    'name': 'stooq',
    'operator': 'stooq',
    'tier': 'A',
    'kinds': ('price',),
    'argv': {'--symbol': 'stooq symbol with market suffix, e.g. spy.us, eurusd',
             '--last': 'trading days to return (default 30)'},
    'endpoints': ['https://stooq.com/q/d/l/'],
    'needs_env': [],
}

URL = 'https://stooq.com/q/d/l/?s={symbol}&i=d'
FX_SYMBOLS = {'eurusd', 'eurgbp', 'eurchf', 'eurjpy', 'usdjpy', 'gbpusd'}


def fetch(args: dict, transport=None) -> list[dict]:
    symbol, = base.require(args, 'symbol')
    symbol = base.validate_identifier(str(symbol).lower(), 'stooq --symbol', allowed='.-^', maxlen=24)
    if '.' not in symbol and symbol not in FX_SYMBOLS and not symbol.startswith('^'):
        raise base.ProviderError(
            f'stooq --symbol: {symbol!r} has no market suffix. An unsuffixed symbol resolves to a '
            'different listing than most callers mean; pass e.g. "spy.us" or "csspx.de".')
    last = int(args.get('last') or 30)
    if last < 1 or last > 5000:
        raise base.ProviderError('stooq --last: expected 1-5000')

    url = URL.format(symbol=symbol)
    text = base.http_get(url, operator='stooq', transport=transport)
    if text.lstrip().lower().startswith(('<!doctype', '<html')):
        # Observed live on 2026-09-20: stooq answers a plain client with a JavaScript
        # browser-verification page instead of CSV. That is a blocked source, not an empty one,
        # and it must be reported as such rather than parsed into zero rows.
        raise base.ProviderError(
            'stooq: the endpoint returned a browser-verification page instead of CSV. This source '
            'is not reachable from a stdlib client right now; use yahoo_chart for tier A prices '
            'and record stooq as failed.')
    if text.strip().lower().startswith('no data') or text.strip() == '':
        raise base.ProviderError(f'stooq: no data for {symbol} (the symbol may not exist)')
    rows = base.parse_csv(text)
    header = [column.strip().lower() for column in rows[0]] if rows else []
    for column in ('date', 'close'):
        if column not in header:
            raise base.ProviderError(f'stooq: CSV has no {column} column')
    index = {name: position for position, name in enumerate(header)}

    out = []
    for row in rows[1:][-last:]:
        if len(row) < len(header):
            continue
        try:
            close = float(row[index['close']])
        except ValueError:
            continue
        out.append(base.record(
            provider='stooq', tier='A', kind='price', native_id=f'{symbol}@{row[index["date"]]}',
            title=f'{symbol.upper()} daily close', symbol=symbol.upper(),
            asof=row[index['date']], value=close, unit='close', url=url,
            extra={'open': _maybe(row, index, 'open'), 'high': _maybe(row, index, 'high'),
                   'low': _maybe(row, index, 'low'), 'volume': _maybe(row, index, 'volume')}))
    if not out:
        raise base.ProviderError(f'stooq: no usable rows for {symbol}')
    return out


def _maybe(row, index, column):
    if column not in index or index[column] >= len(row):
        return None
    try:
        return float(row[index[column]])
    except ValueError:
        return None
