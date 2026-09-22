"""CNN Fear & Greed index - one composite sentiment reading, 0-100.

Tier S. Operator: cnn. No key required.

    fetch({})

What this number is: a composite of seven market-internal indicators, published daily.
What it is not: a forecast, a signal, or a reason. Sentiment extremes are, at best, weakly
contrarian over months; the reading tells you what the market felt yesterday, and
08-behavioural-guardrails.md exists precisely because feelings are the worst available input to a
decision about someone's rent money. It is recorded as a `sentiment` record and can never satisfy
the primary-source condition for a claim about anything other than sentiment itself.
"""
from __future__ import annotations

from . import base

CONTRACT = {
    'name': 'cnn_fear_greed',
    'operator': 'cnn',
    'tier': 'S',
    'kinds': ('sentiment',),
    'argv': {},
    'endpoints': ['https://production.dataviz.cnn.io/index/fearandgreed/graphdata'],
    'needs_env': [],
}

URL = 'https://production.dataviz.cnn.io/index/fearandgreed/graphdata'


def fetch(args: dict, transport=None) -> list[dict]:
    payload = base.get_json(URL, operator='cnn', transport=transport)
    current = (payload or {}).get('fear_and_greed')
    if not isinstance(current, dict) or current.get('score') is None:
        raise base.ProviderError('cnn_fear_greed: response carries no current score')
    asof = str(current.get('timestamp') or '')[:10] or None
    return [base.record(
        provider='cnn_fear_greed', tier='S', kind='sentiment',
        native_id=f'fear_greed@{asof}', title='CNN Fear & Greed index',
        asof=asof, value=_number(current.get('score')), unit='index 0-100', url=URL,
        extra={'rating': current.get('rating'),
               'previous_close': _number(current.get('previous_close')),
               'previous_1_week': _number(current.get('previous_1_week')),
               'previous_1_month': _number(current.get('previous_1_month'))})]


def _number(value):
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None
