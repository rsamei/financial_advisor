"""CoinGecko free API - crypto spot prices and market data, quoted in EUR.

Tier A. Operator: coingecko. No key required on the free tier; it throttles aggressively, and a
429 is reported as `throttled`, never as "no data".

    fetch({'ids': 'bitcoin,ethereum', 'vs': 'eur'})

Crypto prices are observations like any other. Nothing here says a price is reasonable, and the
crypto share limit in 03-risk-profile.md is not negotiable by a chart.
"""
from __future__ import annotations

from . import base

CONTRACT = {
    'name': 'coingecko',
    'operator': 'coingecko',
    'tier': 'A',
    'kinds': ('price',),
    'argv': {'--ids': 'comma-separated CoinGecko asset ids, e.g. bitcoin,ethereum',
             '--vs': 'quote currency (default eur)'},
    'endpoints': ['https://api.coingecko.com/api/v3/simple/price'],
    'needs_env': [],
}

URL = ('https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies={vs}'
       '&include_market_cap=true&include_24hr_change=true&include_last_updated_at=true')


def fetch(args: dict, transport=None) -> list[dict]:
    ids, = base.require(args, 'ids')
    asset_ids = [base.validate_identifier(part.strip().lower(), 'coingecko --ids', allowed='-')
                 for part in str(ids).split(',') if part.strip()]
    if not asset_ids:
        raise base.ProviderError('coingecko --ids: no asset ids given')
    vs = base.validate_identifier(str(args.get('vs') or 'eur').lower(), 'coingecko --vs', maxlen=4)

    url = URL.format(ids=','.join(asset_ids), vs=vs)
    payload = base.get_json(url, operator='coingecko', transport=transport)
    if not isinstance(payload, dict):
        raise base.ProviderError('coingecko: response is not an object')

    out = []
    for asset_id in asset_ids:
        entry = payload.get(asset_id)
        if not isinstance(entry, dict) or vs not in entry:
            # A requested asset missing from the response is a failed lookup for that asset, but
            # the others are still real observations; record what came back and let coverage
            # arithmetic see the gap.
            continue
        asof = _date(entry.get('last_updated_at'))
        out.append(base.record(
            provider='coingecko', tier='A', kind='price', native_id=f'{asset_id}@{asof}',
            title=f'{asset_id} spot in {vs.upper()}', symbol=asset_id.upper(),
            asof=asof, value=entry[vs], unit='spot', currency=vs.upper(), url=url,
            extra={'market_cap': entry.get(f'{vs}_market_cap'),
                   'change_24h_pct': entry.get(f'{vs}_24h_change')}))
    if not out:
        raise base.ProviderError(f'coingecko: none of {", ".join(asset_ids)} returned a price')
    return out


def _date(epoch):
    import datetime as dt
    if not isinstance(epoch, (int, float)):
        return base.today()
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).date().isoformat()
