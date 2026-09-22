#!/usr/bin/env python3
"""Historical price-return baselines and past-only rolling comparisons. Read-only, stdlib only."""
from __future__ import annotations

import bisect
import datetime as dt
import math
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fa_state import StateError, read_json


def horizon_days(value):
    match = re.fullmatch(r'([1-9][0-9]*)(d|m|y)?', str(value))
    if not match:
        raise StateError('horizon must be positive days, or a duration such as 6m or 1y')
    days = round(int(match[1]) * {'d': 1, 'm': 365.25 / 12, 'y': 365.25}[match[2] or 'd'])
    if days > 3653:
        raise StateError('horizon must not exceed ten years')
    return days


def history(root, symbol, as_of, provider=None):
    try:
        limit = dt.date.fromisoformat(as_of)
    except (ValueError, TypeError):
        raise StateError('as_of must be an ISO date') from None
    corpus = read_json(Path(root) / 'market/observations.json', 'observations')
    rows = []
    for record in corpus.values():
        if (str(record.get('symbol', '')).upper() != symbol.upper()
                or record.get('kind') != 'price' or record.get('is_forecast')
                or (provider and record.get('provider') != provider)):
            continue
        try:
            day = dt.date.fromisoformat(str(record.get('asof'))[:10])
        except ValueError:
            continue
        if day > limit:
            continue
        value = record.get('value')
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise StateError('historical prices must be finite and positive')
        rows.append((day, record))
    if not rows:
        raise StateError('no recorded price history for this subject and cutoff')
    signatures = {(r.get('provider'), r.get('currency'), r.get('unit')) for _, r in rows}
    if len(signatures) != 1:
        raise StateError('mixed provider/currency/price basis; select one consistent series with --provider')
    by_day = {}
    for day, record in rows:
        if day in by_day and by_day[day]['value'] != record['value']:
            raise StateError('conflicting prices on the same date')
        by_day[day] = record
    return sorted(by_day.items())


def windows(rows, days, tolerance=7):
    """Non-overlapping windows; endpoint on/before target, at most tolerance days old."""
    dates = [d for d, _ in rows]
    out, i = [], 0
    while i < len(rows):
        target = dates[i] + dt.timedelta(days=days)
        if target > dates[-1]:
            break
        j = bisect.bisect_right(dates, target) - 1
        if j <= i or (target - dates[j]).days > min(tolerance, max(0, days // 4)):
            i += 1
            continue
        out.append({'start': dates[i].isoformat(), 'end': dates[j].isoformat(),
                    'return': rows[j][1]['value'] / rows[i][1]['value'] - 1,
                    'keys': [rows[i][1].get('key'), rows[j][1].get('key')]})
        i = j
    return out


def percentile(values, fraction):
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low, high = math.floor(position), math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def base_rate(root, symbol, horizon, as_of, provider=None):
    days = horizon_days(horizon)
    rows = history(root, symbol, as_of, provider)
    rows = [row for row in rows if available_by(row, dt.date.fromisoformat(as_of))]
    if not rows:
        raise StateError('no price history with recorded availability by the requested cutoff')
    samples = windows(rows, days)
    if not samples:
        raise StateError('insufficient recorded history for one complete horizon')
    values = [s['return'] for s in samples]
    return {'kind': 'historical_price_returns', 'subject': symbol, 'as_of': as_of,
            'horizon_days': days, 'n_windows': len(values),
            'p10': percentile(values, .1), 'p50': percentile(values, .5), 'p90': percentile(values, .9),
            'worst': min(values), 'best': max(values), 'windows': samples,
            'provider': rows[0][1].get('provider'), 'currency': rows[0][1].get('currency'),
            'last_observation': rows[-1][0].isoformat(),
            'note': 'Historical price changes, not total returns or future probabilities. '
                    'Dividends, fees, taxes and FX conversion are excluded. Windows do not '
                    'overlap, but are not necessarily independent. Small samples are unstable.'}


def available_by(row, origin):
    when, record = row
    try:
        fetched = dt.date.fromisoformat(str(record.get('fetched_at'))[:10])
        published = dt.date.fromisoformat(str(record.get('published') or when)[:10])
    except ValueError:
        return False
    return max(when, fetched, published) <= origin


def benchmark(root, symbol, horizon, as_of, provider=None, minimum_windows=5):
    """Compare historical median returns with zero price change on identical held-out periods.

    Only rows fetched/published by the forecast origin enter training. This intentionally
    refuses to claim a historical track record from a price file downloaded today.
    """
    rows = history(root, symbol, as_of, provider)
    days = horizon_days(horizon)
    trials, skipped = [], 0
    for sample in windows(rows, days):
        origin = dt.date.fromisoformat(sample['start'])
        training = []
        for when, row in rows:
            if when > origin:
                break
            # Conservatively require a recorded availability date. Observation date alone
            # does not prove a revised or adjusted series was available then.
            if available_by((when, row), origin):
                training.append((when, row))
        past = windows(training, days) if training else []
        if len(past) < minimum_windows:
            skipped += 1
            continue
        values = [p['return'] for p in past]
        predicted = statistics.median(values)
        low, high = percentile(values, .1), percentile(values, .9)
        realised = sample['return']
        trials.append(sample | {'training_windows': len(past), 'median_prediction': predicted,
                               'low': low, 'high': high, 'in_range': low <= realised <= high,
                               'median_absolute_error': abs(realised - predicted),
                               'no_change_absolute_error': abs(realised)})
    result = {'subject': symbol, 'horizon_days': days, 'as_of': as_of, 'trials': trials,
              'n_trials': len(trials), 'skipped_origins': skipped,
              'note': 'Past-only evaluation of recorded vintages; missing availability dates '
                      'are excluded. Historical ranges are not guaranteed prediction intervals. '
                      'This comparison never changes decision scores or ranks investments.'}
    if not trials:
        return result | {'status': 'insufficient_history'}
    median_error = statistics.mean(t['median_absolute_error'] for t in trials)
    naive_error = statistics.mean(t['no_change_absolute_error'] for t in trials)
    return result | {'status': 'measured_sample', 'median_mean_absolute_error': median_error,
                     'no_change_mean_absolute_error': naive_error,
                     'range_coverage': statistics.mean(t['in_range'] for t in trials),
                     'median_better_in_this_sample': median_error < naive_error}
