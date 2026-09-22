"""Congressional trade disclosures, from the public Stock Watcher mirrors of the filed PTRs.

Tier I. Operator: stockwatcher. No key required.

    fetch({'chamber': 'house', 'last': 50})
    fetch({'chamber': 'senate', 'ticker': 'AAPL'})

These are **public filings** required by the STOCK Act: members of Congress must disclose
transactions, and the disclosures are published. Reading them is not inside information; it is
reading a public register, which is why 05-market-intelligence.md treats this as a first-class
source and the compliance guard has nothing to say about it.

What the data cannot tell you, stated here because the headline is irresistible:

- **Disclosure lags the trade by 30-45 days**, and the deadline is routinely missed. By the time a
  filing is visible, the price has had six weeks to move.
- **Amounts are ranges, not figures** ("$1,001 - $15,000"). This module keeps the range and never
  converts it to a midpoint: a midpoint is a number nobody disclosed.
- A trade may be made by a spouse, a trust or a managed account the member does not direct.

It is evidence about positioning, and it moves only the evidence-strength dimension in 04.
"""
from __future__ import annotations

from . import base

CONTRACT = {
    'name': 'congress_trades',
    'operator': 'stockwatcher',
    'tier': 'I',
    'kinds': ('insider_tx',),
    'argv': {'--chamber': 'house or senate', '--ticker': 'optional ticker filter',
             '--last': 'transactions to return (default 50)'},
    'endpoints': ['https://house-stock-watcher-data.s3-us-west-2.amazonaws.com/data/'
                  'all_transactions.json',
                  'https://senate-stock-watcher-data.s3-us-west-2.amazonaws.com/aggregate/'
                  'all_transactions.json'],
    'needs_env': [],
}

SOURCES = {
    'house': 'https://house-stock-watcher-data.s3-us-west-2.amazonaws.com/data/all_transactions.json',
    'senate': 'https://senate-stock-watcher-data.s3-us-west-2.amazonaws.com/aggregate/all_transactions.json',
}


def fetch(args: dict, transport=None) -> list[dict]:
    chamber = str(args.get('chamber') or 'house').strip().lower()
    if chamber not in SOURCES:
        raise base.ProviderError(f'congress_trades --chamber: expected house or senate, '
                                 f'got {chamber!r}')
    ticker = args.get('ticker')
    if ticker:
        ticker = base.validate_identifier(str(ticker).upper(), 'congress_trades --ticker',
                                          allowed='.-', maxlen=12)
    last = int(args.get('last') or 50)
    if last < 1 or last > 500:
        raise base.ProviderError('congress_trades --last: expected 1-500')

    payload = base.get_json(SOURCES[chamber], operator='stockwatcher', transport=transport)
    if not isinstance(payload, list):
        raise base.ProviderError('congress_trades: response is not a list of transactions')

    out = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        symbol = (item.get('ticker') or '').strip().upper()
        if ticker and symbol != ticker:
            continue
        if symbol in ('', '--'):
            continue                      # a filing without a ticker identifies no instrument
        traded = item.get('transaction_date') or item.get('transaction_date_str')
        disclosed = item.get('disclosure_date')
        out.append(base.record(
            provider='congress_trades', tier='I', kind='insider_tx',
            native_id=f'{chamber}:{item.get("representative") or item.get("senator")}:'
                      f'{symbol}:{traded}:{item.get("type")}',
            title=f'{item.get("representative") or item.get("senator")} '
                  f'{item.get("type")} {symbol}',
            symbol=symbol, asof=_date(traded), published=_date(disclosed),
            value=None, unit='range', url=item.get('ptr_link'),
            extra={'chamber': chamber,
                   'member': item.get('representative') or item.get('senator'),
                   'transaction_type': item.get('type'),
                   'amount_range': item.get('amount'),      # kept as a range, never a midpoint
                   'owner': item.get('owner'),
                   'disclosure_lag_days': _lag(traded, disclosed),
                   'asset_description': item.get('asset_description')}))
        if len(out) >= last:
            break
    if not out:
        raise base.ProviderError(
            f'congress_trades: no transactions matched' + (f' for {ticker}' if ticker else ''))
    return out


def _date(raw):
    import datetime as dt
    if not raw:
        return None
    text = str(raw).strip()
    for pattern in ('%Y-%m-%d', '%m/%d/%Y'):
        try:
            return dt.datetime.strptime(text, pattern).date().isoformat()
        except ValueError:
            continue
    return None          # an unparseable date is unknown, never today


def _lag(traded, disclosed):
    """The lag is the point: a disclosure is old news by construction, and the report says so."""
    import datetime as dt
    first, second = _date(traded), _date(disclosed)
    if not first or not second:
        return None
    return (dt.date.fromisoformat(second) - dt.date.fromisoformat(first)).days
