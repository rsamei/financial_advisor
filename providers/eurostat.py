"""Eurostat JSON-stat API - euro-area and Italian official statistics (HICP, unemployment, GDP).

Tier M. Operator: eurostat. No key required.

    fetch({'dataset': 'prc_hicp_manr', 'geo': 'IT',
           'filters': 'coicop=CP00,unit=RCH_A', 'last': 12})

Most datasets need their non-time dimensions pinned before a single series remains; HICP needs at
least `coicop` (CP00 = all items) and `unit` (RCH_A = annual rate of change). Verified live against
the API on 2026-09-20: without them the call below refuses, by design.

JSON-stat is a dimension-indexed format: values live in a flat map keyed by a single integer index
computed from the dimension sizes. This module decodes only the time dimension, which is what a
macro claim ever rests on, and it refuses rather than guesses when a filter leaves more than one
series in the response - a silently collapsed multi-series answer is the kind of number that looks
authoritative and means nothing.
"""
from __future__ import annotations

import urllib.parse

from . import base

CONTRACT = {
    'name': 'eurostat',
    'operator': 'eurostat',
    'tier': 'M',
    'kinds': ('series',),
    'argv': {'--dataset': 'Eurostat dataset code, e.g. prc_hicp_manr',
             '--geo': 'ISO country or aggregate code, e.g. IT, EA20',
             '--filters': 'dimension filters as key=value pairs, comma separated - usually '
                          'required, e.g. coicop=CP00,unit=RCH_A for HICP',
             '--last': 'observations to return'},
    'endpoints': ['https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/{dataset}'],
    'needs_env': [],
}

BASE = 'https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data'


def _decode(payload: dict, dataset: str, url: str) -> list[dict]:
    dimension = payload.get('dimension') or {}
    size = payload.get('size') or []
    ids = payload.get('id') or []
    if not ids or 'time' not in ids:
        raise base.ProviderError(f'eurostat: {dataset} response has no time dimension')
    non_time = [name for name, count in zip(ids, size) if name != 'time' and count > 1]
    if non_time:
        raise base.ProviderError(
            f'eurostat: the filter leaves more than one series ({", ".join(non_time)} vary). '
            'Narrow the filters; this provider will not collapse several series into one number.')
    periods = dimension.get('time', {}).get('category', {}).get('index', {})
    if not periods:
        raise base.ProviderError(f'eurostat: {dataset} response has no time categories')
    order = {position: period for period, position in periods.items()}
    values = payload.get('value') or {}
    label = (payload.get('label') or dataset)

    # Distinguish every non-time series, both for exact and fuzzy merge. The old dataset-only
    # identity collapsed Italy and euro-area observations. Retrieval window is not identity.
    selectors = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
    selectors = {k: v for k, v in selectors.items() if k not in ('format', 'lastTimePeriod', 'lang')}
    for name in ids:
        if name == 'time':
            continue
        categories = dimension.get(name, {}).get('category', {}).get('index', {})
        if isinstance(categories, dict) and len(categories) == 1:
            selectors[name] = next(iter(categories))
    identity = dataset + ':' + ','.join(f'{k}={v}' for k, v in sorted(selectors.items()))

    out = []
    for raw_index, value in values.items():
        period = order.get(int(raw_index))
        if period is None:
            continue
        out.append(base.record(
            provider='eurostat', tier='M', kind='series',
            native_id=f'{identity}@{period}', title=label, series_id=identity,
            asof=period, value=value, url=url,
            extra={'dataset': dataset, 'dimensions': selectors}))
    return sorted(out, key=lambda item: item['asof'])


def fetch(args: dict, transport=None) -> list[dict]:
    dataset, = base.require(args, 'dataset')
    dataset = base.validate_identifier(dataset.lower(), 'eurostat --dataset', allowed='_', maxlen=40)
    last = int(args.get('last') or 12)
    if last < 1 or last > 500:
        raise base.ProviderError('eurostat --last: expected 1-500')

    query = [f'format=JSON', f'lastTimePeriod={last}']
    if args.get('geo'):
        query.append('geo=' + base.validate_identifier(
            str(args['geo']).upper(), 'eurostat --geo', maxlen=8))
    for pair in str(args.get('filters') or '').split(','):
        if not pair.strip():
            continue
        name, _, value = pair.partition('=')
        query.append('{}={}'.format(
            base.validate_identifier(name.strip(), 'eurostat filter name', allowed='_'),
            base.validate_identifier(value.strip(), 'eurostat filter value', allowed='_-.')))

    url = f'{BASE}/{dataset}?' + '&'.join(query)
    records = _decode(base.get_json(url, operator='eurostat', transport=transport), dataset, url)
    if not records:
        raise base.ProviderError(f'eurostat: no observations for {dataset}')
    return records
