"""FINRA consolidated short interest - short volume and days-to-cover for US listings.

Tier I. Operator: finra. No key required for the published files.

    fetch({'symbol': 'AAPL', 'last': 6})

FINRA publishes short interest twice a month, with a settlement lag of several business days. So a
reading is already a week or two old when it appears, and days-to-cover is computed from an average
volume that a volatile fortnight makes meaningless. It is evidence about positioning. It is not a
prediction, and a crowded short is not a thesis.
"""
from __future__ import annotations

from . import base

CONTRACT = {
    'name': 'finra_short',
    'operator': 'finra',
    'tier': 'I',
    'kinds': ('holding',),
    'argv': {'--symbol': 'US ticker', '--last': 'settlement periods to return (default 6)'},
    'endpoints': ['https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest'],
    'needs_env': [],
}

URL = ('https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest'
       '?symbol={symbol}&limit={limit}')


def fetch(args: dict, transport=None) -> list[dict]:
    symbol, = base.require(args, 'symbol')
    symbol = base.validate_identifier(str(symbol).upper(), 'finra_short --symbol',
                                      allowed='.-', maxlen=12)
    last = int(args.get('last') or 6)
    if last < 1 or last > 60:
        raise base.ProviderError('finra_short --last: expected 1-60')

    payload = base.get_json(URL.format(symbol=symbol, limit=last), operator='finra',
                            transport=transport)
    rows = payload if isinstance(payload, list) else (payload or {}).get('data')
    if not isinstance(rows, list):
        raise base.ProviderError('finra_short: response carries no rows')

    # Probed live 2026-09-21: the GET endpoint ignores `symbol` and answers with the first rows of
    # the whole table (Agilent, 2020). Filtering needs FINRA's POST query, which base.http_get does
    # not offer. A row for another company must never be recorded under the requested ticker.
    out = []
    for row in rows:
        if not isinstance(row, dict) or str(row.get('symbolCode') or '').upper() != symbol:
            continue
        asof = str(row.get('settlementDate') or '')[:10] or None
        out.append(base.record(
            provider='finra_short', tier='I', kind='holding',
            native_id=f'{symbol}@{asof}', title=f'{symbol} consolidated short interest',
            symbol=symbol, asof=asof, value=_number(row.get('currentShortPositionQuantity')),
            unit='shares short', url=URL.format(symbol=symbol, limit=last),
            extra={'previous_short': _number(row.get('previousShortPositionQuantity')),
                   'days_to_cover': _number(row.get('daysToCoverQuantity')),
                   'average_daily_volume': _number(row.get('averageDailyVolumeQuantity')),
                   'settlement_date': asof}))
    if not out:
        raise base.ProviderError(f'finra_short: no short-interest rows for {symbol}')
    return out


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
