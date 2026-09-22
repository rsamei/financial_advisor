"""ECB Data Portal (SDMX 2.1 CSV) - policy rates, yields, euro-area statistics, reference FX.

Tier M. Operator: European Central Bank. No key required.

    fetch({'series': 'FM.B.U2.EUR.4F.KR.MRR_FR.LEV', 'last': 12})
    fetch({'fx': 'USD'})            # euro reference rate, one currency
    fetch({'fx': 'USD,GBP,CHF'})

The series key is the ECB's own dotted identifier; the dataflow is its first segment. The CSV
endpoint returns one row per observation with `TIME_PERIOD` and `OBS_VALUE` columns.

Reference rates are the ECB daily fix, published once per TARGET business day at about 16:00 CET.
They are the rate this repository converts at, because they are an official published series with
a date - not because they are the best price anyone could trade at. `tools/fx.py` refuses to
convert without one of these records.
"""
from __future__ import annotations

from . import base

CONTRACT = {
    'name': 'ecb',
    'operator': 'ecb',
    'tier': 'M',
    'kinds': ('series', 'price'),
    'argv': {
        '--series': 'ECB series key, e.g. FM.B.U2.EUR.4F.KR.MRR_FR.LEV',
        '--fx': 'comma-separated ISO-4217 codes for euro reference rates',
        '--last': 'how many observations to return (default 12)',
    },
    'endpoints': ['https://data-api.ecb.europa.eu/service/data/{flow}/{key}'],
    'needs_env': [],
}

BASE = 'https://data-api.ecb.europa.eu/service/data'
FX_FLOW = 'EXR'
# D = daily, SP00 = reference rate, A = average/standard presentation.
FX_KEY = 'D.{currency}.EUR.SP00.A'


def _rows(text: str) -> list[dict]:
    rows = base.parse_csv(text)
    if not rows:
        raise base.ProviderError('ecb: empty CSV response')
    header = [column.strip() for column in rows[0]]
    if 'TIME_PERIOD' not in header or 'OBS_VALUE' not in header:
        raise base.ProviderError('ecb: CSV lacks TIME_PERIOD/OBS_VALUE - the response is not an '
                                 'SDMX data message')
    return [dict(zip(header, row)) for row in rows[1:] if len(row) == len(header)]


def _value(raw: str) -> float | None:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None      # a suppressed or missing observation is unknown, not zero


def _fetch_series(series: str, last: int, transport) -> list[dict]:
    key = base.validate_identifier(series, 'ecb --series', allowed='._-+', maxlen=128)
    flow = key.split('.')[0]
    url = f'{BASE}/{flow}/{key[len(flow) + 1:]}?format=csvdata&lastNObservations={last}'
    rows = _rows(base.http_get(url, operator='ecb', transport=transport))
    out = []
    for row in rows:
        asof = row.get('TIME_PERIOD')
        out.append(base.record(
            provider='ecb', tier='M', kind='series', native_id=f'{key}@{asof}',
            title=row.get('TITLE') or row.get('TITLE_COMPL') or key,
            series_id=key, asof=asof, value=_value(row.get('OBS_VALUE')),
            unit=row.get('UNIT') or None, url=url))
    if not out:
        raise base.ProviderError(f'ecb: no observations for {key}')
    return out


def _fetch_fx(currencies: str, last: int, transport) -> list[dict]:
    out = []
    for raw in currencies.split(','):
        currency = base.validate_identifier(raw.strip().upper(), 'ecb --fx', maxlen=3)
        if len(currency) != 3:
            raise base.ProviderError(f'ecb --fx: {currency!r} is not an ISO-4217 code')
        key = FX_KEY.format(currency=currency)
        url = f'{BASE}/{FX_FLOW}/{key}?format=csvdata&lastNObservations={last}'
        for row in _rows(base.http_get(url, operator='ecb', transport=transport)):
            out.append(base.record(
                provider='ecb', tier='M', kind='price',
                native_id=f'EXR.{currency}@{row.get("TIME_PERIOD")}',
                title=f'ECB euro reference rate {currency}/EUR',
                symbol=f'EUR{currency}', series_id=f'{FX_FLOW}.{key}',
                asof=row.get('TIME_PERIOD'), value=_value(row.get('OBS_VALUE')),
                unit=f'{currency} per EUR', currency=currency, url=url))
    if not out:
        raise base.ProviderError('ecb: no reference rates returned')
    return out


def fetch(args: dict, transport=None) -> list[dict]:
    last = int(args.get('last') or 12)
    if last < 1 or last > 500:
        raise base.ProviderError('ecb --last: expected 1-500')
    if args.get('fx'):
        return _fetch_fx(args['fx'], last, transport)
    series, = base.require(args, 'series')
    return _fetch_series(series, last, transport)
