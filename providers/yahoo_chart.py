"""Yahoo Finance chart JSON - daily closes for equities, ETFs, indices and FX.

Tier A. Operator: yahoo. No key required.

    fetch({'symbol': 'CSSPX.MI', 'range': '1mo'})
    fetch({'symbol': 'EURUSD=X', 'range': '5d'})

**This is an unofficial endpoint.** It is not a published API, it carries no service commitment,
and it can change or start refusing plain clients without notice - which is exactly what `stooq`
did on 2026-09-20. Two consequences, both deliberate:

1. It is a *fallback*, not the reference. Where an official source publishes the same number - the
   ECB reference FX fix, for instance - that source wins, and `tools/fx.py` will not convert at a
   Yahoo rate.
2. A price from here is an observation about a quote, not a valuation, and not an official close.
   The record carries the exchange and the currency Yahoo reports so a later reader can tell which
   listing produced the number: the same ETF quoted in Milan and in London is two different rows.
"""
from __future__ import annotations

from . import base

CONTRACT = {
    'name': 'yahoo_chart',
    'operator': 'yahoo',
    'tier': 'A',
    'kinds': ('price',),
    'argv': {'--symbol': 'Yahoo symbol, e.g. CSSPX.MI, VWCE.DE, EURUSD=X',
             '--range': '5d, 1mo, 3mo, 6mo, 1y, 2y, 5y, 10y, max (default 1mo)',
             '--interval': '1d (default) or 1wk or 1mo'},
    'endpoints': ['https://query1.finance.yahoo.com/v8/finance/chart/{symbol}'],
    'needs_env': [],
}

URL = ('https://query1.finance.yahoo.com/v8/finance/chart/{symbol}'
       '?range={range}&interval={interval}')
RANGES = ('5d', '1mo', '3mo', '6mo', '1y', '2y', '5y', '10y', 'max')
INTERVALS = ('1d', '1wk', '1mo')


def fetch(args: dict, transport=None) -> list[dict]:
    symbol, = base.require(args, 'symbol')
    symbol = base.validate_identifier(str(symbol).upper(), 'yahoo_chart --symbol',
                                      allowed='.-=^', maxlen=24)
    window = str(args.get('range') or '1mo').lower()
    if window not in RANGES:
        raise base.ProviderError(f'yahoo_chart --range: expected one of {", ".join(RANGES)}')
    interval = str(args.get('interval') or '1d').lower()
    if interval not in INTERVALS:
        raise base.ProviderError(f'yahoo_chart --interval: expected one of {", ".join(INTERVALS)}')

    url = URL.format(symbol=symbol, range=window, interval=interval)
    payload = base.get_json(url, operator='yahoo', transport=transport)
    chart = (payload or {}).get('chart') or {}
    if chart.get('error'):
        raise base.ProviderError(f'yahoo_chart: {symbol}: {chart["error"]}')
    results = chart.get('result')
    if not isinstance(results, list) or not results:
        raise base.ProviderError(f'yahoo_chart: no chart result for {symbol}')

    result = results[0]
    meta = result.get('meta') or {}
    stamps = result.get('timestamp') or []
    quote = ((result.get('indicators') or {}).get('quote') or [{}])[0]
    closes = quote.get('close') or []

    out = []
    for stamp, close in zip(stamps, closes):
        if close is None:
            continue                      # a holiday or a halted session is unknown, not zero
        asof = _date(stamp)
        out.append(base.record(
            provider='yahoo_chart', tier='A', kind='price', native_id=f'{symbol}@{asof}',
            title=f'{symbol} {interval} close', symbol=symbol, asof=asof, value=float(close),
            unit='close', currency=meta.get('currency'), url=url,
            extra={'exchange': meta.get('exchangeName'),
                   'instrument_type': meta.get('instrumentType'),
                   'unofficial_endpoint': True}))
    if not out:
        raise base.ProviderError(f'yahoo_chart: no closes returned for {symbol}')
    return out


def _date(stamp):
    import datetime as dt
    return dt.datetime.fromtimestamp(stamp, dt.timezone.utc).date().isoformat()
