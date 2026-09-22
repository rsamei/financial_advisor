"""FRED (Federal Reserve Bank of St. Louis) - US and international macro series.

Tier M. Operator: stlouisfed. `FRED_API_KEY` is optional: without it this module uses the public
`fredgraph.csv` endpoint, which serves the same observations without a key and without metadata.

    fetch({'series': 'DGS10', 'last': 24})

Two endpoints, one operator. Using the CSV fallback does NOT make this a second independent source
for coverage purposes - independence is a property of who runs the service, and both are the
St. Louis Fed.
"""
from __future__ import annotations

import os

from . import base

CONTRACT = {
    'name': 'fred',
    'operator': 'stlouisfed',
    'tier': 'M',
    'kinds': ('series',),
    'argv': {'--series': 'FRED series id, e.g. DGS10', '--last': 'observations to return'},
    'endpoints': ['https://api.stlouisfed.org/fred/series/observations',
                  'https://fred.stlouisfed.org/graph/fredgraph.csv'],
    'needs_env': ['FRED_API_KEY (optional)'],
}

API = 'https://api.stlouisfed.org/fred/series/observations'
CSV = 'https://fred.stlouisfed.org/graph/fredgraph.csv'


def _value(raw):
    # FRED writes '.' for a missing observation. That is unknown, not zero.
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _from_api(series: str, last: int, key: str, transport) -> list[dict]:
    url = (f'{API}?series_id={series}&api_key={key}&file_type=json'
           f'&sort_order=desc&limit={last}')
    payload = base.get_json(url, operator='fred', transport=transport)
    observations = payload.get('observations')
    if not isinstance(observations, list):
        raise base.ProviderError('fred: response has no observations array')
    # The key is in the URL, so the recorded url drops it: queries.json is tracked by git.
    public_url = f'{API}?series_id={series}&file_type=json'
    return [base.record(provider='fred', tier='M', kind='series',
                        native_id=f'{series}@{item.get("date")}', title=series, series_id=series,
                        asof=item.get('date'), value=_value(item.get('value')), url=public_url)
            for item in observations]


def _from_csv(series: str, last: int, transport) -> list[dict]:
    url = f'{CSV}?id={series}'
    rows = base.parse_csv(base.http_get(url, operator='fred', transport=transport))
    if len(rows) < 2 or len(rows[0]) < 2:
        raise base.ProviderError(f'fred: fredgraph.csv returned no rows for {series}')
    observations = rows[1:][-last:]
    return [base.record(provider='fred', tier='M', kind='series',
                        native_id=f'{series}@{row[0]}', title=series, series_id=series,
                        asof=row[0], value=_value(row[1]), url=url)
            for row in observations]


def fetch(args: dict, transport=None) -> list[dict]:
    series, = base.require(args, 'series')
    series = base.validate_identifier(series.upper(), 'fred --series', allowed='_', maxlen=40)
    last = int(args.get('last') or 24)
    if last < 1 or last > 500:
        raise base.ProviderError('fred --last: expected 1-500')
    key = os.environ.get('FRED_API_KEY', '').strip()
    records = _from_api(series, last, key, transport) if key else _from_csv(series, last, transport)
    if not records:
        raise base.ProviderError(f'fred: no observations for {series}')
    return sorted(records, key=lambda record: record['asof'])
