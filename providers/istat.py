"""ISTAT SDMX API - Italian official statistics, primarily HICP (IPCA) for the Italian overlay.

Tier M. Operator: istat. No key required.

    fetch({'dataflow': '168_761_DF_DCSP_IPCA1B2025_1',
           'key': 'M.IT.<data_type>.<measure>.<ecoicop>', 'last': 12})

Why this provider exists. Eurostat and the ECB both republish the Italian HICP, and as of
2026-09-21 both stopped at 2025-12. ISTAT is the institute that actually *computes* the Italian
number, so it is primary by construction and is the only route to a third independent operator for
a claim about Italian inflation - which is the claim that matters most for an Italian tax resident.

Two deliberate design choices, both learned from defects in sibling providers:

1. **The record key carries the whole series key, including REF_AREA.** `providers/eurostat.py`
   builds its key from dataset + period only, so an EA20 pull and an IT pull of the same dataset
   collide in the merge store and one is silently discarded. A key that omits the dimension the
   caller varied is a key that loses data. Here the native id is `<dataflow>/<key>@<period>`.
2. **No dimension code is guessed.** `key` is passed through whole, validated character by
   character, and never assembled from friendly names inside this module. ISTAT's codelists
   (`CL_TIPO_DATO2`, `CL_MISURA1`, `CL_ECOICOP_2`, `CL_FREQ`) change between index bases, and a
   provider that hardcodes a code silently returns the wrong series after a rebasing.

The dimension order for the HICP dataflow above is `FREQ.REF_AREA.DATA_TYPE.MEASURE.ECOICOP_2`,
read from the DSD `DCSP_IPCA1B2025` on 2026-09-21.

Note on the service: ISTAT's catalogue endpoints (the full `dataflow` and `datastructure` lists)
are slow enough to time out, and the service starts refusing connections under rapid repeated
requests. Wildcard data keys make the server scan and also time out. A fully specified key is the
only shape this module issues, and `MIN_INTERVAL` in `base` throttles the operator.

Stdlib only.
"""
from __future__ import annotations

from . import base

CONTRACT = {
    'name': 'istat',
    'operator': 'istat',
    'tier': 'M',
    'kinds': ('series',),
    'argv': {'--dataflow': 'ISTAT dataflow id, e.g. 168_761_DF_DCSP_IPCA1B2025_1 (HICP monthly)',
             '--key': 'fully specified SDMX series key, dot separated, no wildcards - for the '
                      'HICP dataflow the order is FREQ.REF_AREA.DATA_TYPE.MEASURE.ECOICOP_2',
             '--agency': 'agency id (default IT1)',
             '--version': 'dataflow version (default 1.0)',
             '--last': 'observations to return (default 12)'},
    'endpoints': ['https://esploradati.istat.it/SDMXWS/rest/data/{agency},{dataflow},{version}/{key}'],
    'needs_env': [],
}

BASE = 'https://esploradati.istat.it/SDMXWS/rest/data'


def _series_and_periods(payload: dict, url: str) -> tuple[dict, list[str]]:
    """Pull the single series and the time labels out of an SDMX-JSON message.

    Refuses rather than guesses when the key left more than one series: a collapsed multi-series
    answer is the kind of number that looks authoritative and means nothing.
    """
    datasets = payload.get('dataSets') or []
    if not datasets:
        raise base.ProviderError(f'istat: {url} carried no dataSets')
    series = datasets[0].get('series') or {}
    if not series:
        observations = datasets[0].get('observations')
        if observations:
            raise base.ProviderError(
                'istat: the response is flat (no series dimension). Pass a fully specified key.')
        raise base.ProviderError(f'istat: {url} returned no observations')
    if len(series) > 1:
        raise base.ProviderError(
            f'istat: the key leaves {len(series)} series. Narrow it; this provider will not '
            'collapse several series into one number.')

    structure = payload.get('structure') or {}
    observation_dimensions = (structure.get('dimensions') or {}).get('observation') or []
    time_values = None
    for dimension in observation_dimensions:
        if str(dimension.get('id', '')).upper() in ('TIME_PERIOD', 'TIME'):
            time_values = [value.get('id') or value.get('name')
                           for value in dimension.get('values') or []]
            break
    if not time_values:
        raise base.ProviderError('istat: the response has no time dimension')
    return next(iter(series.values())), time_values


def _decode(payload: dict, dataflow: str, key: str, url: str) -> list[dict]:
    series, periods = _series_and_periods(payload, url)
    observations = series.get('observations') or {}
    title = ((payload.get('structure') or {}).get('name')
             or (payload.get('structure') or {}).get('description')
             or dataflow)

    out = []
    for raw_index, cell in observations.items():
        try:
            position = int(raw_index)
        except (TypeError, ValueError):
            continue
        if position >= len(periods):
            continue
        # An SDMX observation is an array whose first element is the value; the rest are
        # attribute references this module does not need.
        value = cell[0] if isinstance(cell, list) and cell else cell
        if value is None:
            # A suppressed or not-yet-published point. Skipped, never read as zero.
            continue
        out.append(base.record(
            provider='istat', tier='M', kind='series',
            native_id=f'{dataflow}/{key}@{periods[position]}',
            title=title, series_id=f'{dataflow}/{key}',
            asof=periods[position], value=value, url=url))
    return sorted(out, key=lambda item: item['asof'])


def fetch(args: dict, transport=None) -> list[dict]:
    dataflow, key = base.require(args, 'dataflow', 'key')
    dataflow = base.validate_identifier(str(dataflow), 'istat --dataflow', allowed='_', maxlen=80)
    # Dots separate dimensions; a plus is SDMX's OR. Both are structural, so both are allowed -
    # but an empty dimension (a wildcard) is not: it makes the service scan and time out.
    key = base.validate_identifier(str(key), 'istat --key', allowed='._+-', maxlen=120)
    if '..' in key or key.startswith('.') or key.endswith('.'):
        raise base.ProviderError(
            'istat --key: wildcard (empty) dimensions are not accepted. The service scans the '
            'whole dataflow for them and times out. Specify every dimension.')
    agency = base.validate_identifier(str(args.get('agency') or 'IT1'), 'istat --agency', maxlen=12)
    version = base.validate_identifier(str(args.get('version') or '1.0'), 'istat --version',
                                       allowed='.', maxlen=12)
    last = int(args.get('last') or 12)
    if last < 1 or last > 500:
        raise base.ProviderError('istat --last: expected 1-500')

    url = (f'{BASE}/{agency},{dataflow},{version}/{key}'
           f'?format=jsondata&lastNObservations={last}')
    records = _decode(base.get_json(url, operator='istat', transport=transport),
                      dataflow, key, url)
    if not records:
        raise base.ProviderError(f'istat: no observations for {dataflow}/{key}')
    return records
