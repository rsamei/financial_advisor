#!/usr/bin/env python3
"""Read-only reality check for a wealth goal: what reaching it on time would take. No forecast.

python tools/goal_check.py check --inputs <local inputs.json>
Inputs and outputs carry household amounts and belong under ignored advice/ or decisions/.

Arithmetic on the household's own recorded numbers. The result is a what-if: it never enters a
gate record, never moves a score dimension and never originates an action (reference 13).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fa_state import StateError, read_json, safe_console, today
from forecast_history import base_rate

START_BASES = {'net_worth': 'net_worth_eur', 'invested_assets': 'invested_assets_eur',
               'liquid_assets': 'liquid_assets_eur'}
MAX_ANNUAL_RETURN = 100.0          # 10 000 % a year; beyond this the answer is "no rate does it"
MAX_MONTHS_SEARCHED = 600
DAYS_PER_MONTH = 365.25 / 12


def number(value, name, minimum=None):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or (minimum is not None and value < minimum)):
        raise StateError(f'{name}: a finite number' +
                         (f' >= {minimum}' if minimum is not None else '') + ' is required')
    return float(value)


def monthly_rate(annual):
    return (1 + annual) ** (1 / 12) - 1


def future_value(start, monthly, annual, months):
    """Start amount compounding monthly, plus an end-of-month saving. `annual` is a fraction."""
    rate = monthly_rate(annual)
    growth = (1 + rate) ** months
    if abs(rate) < 1e-12:
        return start + monthly * months
    return start * growth + monthly * (growth - 1) / rate


def required_return(start, monthly, target, months):
    """Smallest yearly growth that reaches the target on time; None when no rate below the cap does."""
    if future_value(start, monthly, MAX_ANNUAL_RETURN, months) < target:
        return None
    low, high = -0.99, MAX_ANNUAL_RETURN
    for _ in range(200):
        middle = (low + high) / 2
        if future_value(start, monthly, middle, months) < target:
            low = middle
        else:
            high = middle
    return high


def required_saving(start, target, annual, months):
    rate = monthly_rate(annual)
    growth = (1 + rate) ** months
    gap = target - start * growth
    if gap <= 0:
        return 0.0
    return gap / months if abs(rate) < 1e-12 else gap * rate / (growth - 1)


def months_to_reach(start, monthly, target, annual):
    rate, amount = monthly_rate(annual), start
    for month in range(1, MAX_MONTHS_SEARCHED + 1):
        amount = amount * (1 + rate) + monthly
        if amount >= target:
            return month
    return None


def add_months(day, months):
    return day + dt.timedelta(days=round(months * DAYS_PER_MONTH))


def euros(value):
    return f'{value:,.0f} EUR'


def history_check(root, spec, needed_over_horizon, days, as_of):
    """How often a recorded price series rose at least this much over periods of this length."""
    if not isinstance(spec, dict) or not spec.get('symbol'):
        raise StateError('history needs a symbol, and a provider when several recorded one')
    try:
        rates = base_rate(root, str(spec['symbol']), str(days), as_of, spec.get('provider'))
    except StateError as exc:
        return {'status': 'insufficient_history', 'symbol': spec['symbol'], 'reason': str(exc)}
    reached = sum(1 for window in rates['windows'] if window['return'] >= needed_over_horizon)
    return {'status': 'measured_sample', 'symbol': spec['symbol'], 'provider': rates['provider'],
            'n_windows': rates['n_windows'], 'n_reached': reached,
            'first_window_start': rates['windows'][0]['start'],
            'last_window_end': rates['windows'][-1]['end'],
            'best_window_return': rates['best'], 'needed_return_over_horizon': needed_over_horizon,
            'note': rates['note'] + ' The comparison assumes the whole starting amount sat in this '
                    'one series, which is a simplification, and a count of past periods is not a '
                    'probability.'}


def check(profile, derived, inputs, root=None, as_of=None):
    if not isinstance(inputs, dict):
        raise StateError('inputs must be an object')
    allowed = {'goal_id', 'start_basis', 'start_amount_eur', 'monthly_saving_eur',
               'what_if_annual_returns_pct', 'history'}
    if set(inputs) - allowed:
        raise StateError('unsupported inputs: ' + ', '.join(sorted(set(inputs) - allowed)))
    try:
        start_day = dt.date.fromisoformat(as_of or today())
    except (TypeError, ValueError):
        raise StateError('as_of must be an ISO date') from None
    goals = [g for g in profile.get('goals', []) if g.get('goal_id') == inputs.get('goal_id')]
    if len(goals) != 1:
        raise StateError('goal_id must name exactly one recorded goal')
    goal = goals[0]
    result = {'kind': 'what_if', 'tool': 'goal_check.py', 'as_of': start_day.isoformat(),
              'goal_id': goal['goal_id'], 'target_date': goal.get('target_date'),
              'sources': ['ledger:goals[*]', 'derived:balance_sheet'], 'warnings': [],
              'note': 'Arithmetic on recorded numbers, not a forecast, a probability or a '
                      'recommendation. It never enters a gate record or a score. Growth rates '
                      'are before fees, taxes and inflation unless the inputs say otherwise.'}
    if goal.get('target_amount_eur') is None or not goal.get('target_date'):
        return result | {'status': 'unknown', 'plain': [
            'This goal has no target amount or no target date yet, so it cannot be checked.']}
    target = number(goal['target_amount_eur'], 'goal.target_amount_eur', 0)
    try:
        due = dt.date.fromisoformat(goal['target_date'])
    except (TypeError, ValueError):
        raise StateError('goal.target_date must be an ISO date') from None

    sheet = derived.get('balance_sheet') or {}
    basis = inputs.get('start_basis')
    if basis == 'explicit':
        start = number(inputs.get('start_amount_eur'), 'start_amount_eur')
    elif basis in START_BASES:
        start = number(sheet.get(START_BASES[basis]), 'derived ' + START_BASES[basis])
    else:
        raise StateError('start_basis must be explicit, ' + ', '.join(sorted(START_BASES)))
    if 'monthly_saving_eur' in inputs:
        monthly, saving_source = number(inputs['monthly_saving_eur'], 'monthly_saving_eur'), 'inputs'
    else:
        monthly = number(sheet.get('monthly_savings_capacity_eur'),
                         'derived monthly_savings_capacity_eur')
        saving_source = 'derived:monthly_savings_capacity_eur'
        result['warnings'].append('Monthly saving is what is left after recorded spending; it '
                                  'assumes all of it is saved every month.')
    try:
        if (start_day - dt.date.fromisoformat(str(derived.get('computed_on')))).days > 90:
            result['warnings'].append('The derived balance sheet is older than 90 days; refresh it.')
    except ValueError:
        result['warnings'].append('The derived balance sheet carries no usable date; refresh it.')
    result |= {'target_eur': round(target, 2), 'start_eur': round(start, 2), 'start_basis': basis,
               'monthly_saving_eur': round(monthly, 2), 'monthly_saving_source': saving_source}

    days = (due - start_day).days
    if start >= target:
        return result | {'status': 'already_reached', 'plain': [
            f'You are already at or above this target ({euros(start)} against {euros(target)}).']}
    if days <= 0:
        return result | {'status': 'past_due', 'plain': [
            'The target date has passed and the target was not reached. Pick a new date or amount.']}
    months = days / DAYS_PER_MONTH
    flat = future_value(start, monthly, 0, months)
    gap = target - flat
    plain = [f'You have about {months:.0f} months until {due.isoformat()}.']
    result |= {'months_left': round(months, 1), 'saving_alone_reaches_eur': round(flat, 2),
               'gap_without_growth_eur': round(max(0.0, gap), 2)}

    if gap <= 0:
        result['status'] = 'saving_alone_is_enough'
        result['required_annual_return_pct'] = 0.0
        plain.append(f'Saving {euros(monthly)} a month gets you to about {euros(flat)} even if '
                     f'your money does not grow at all. That is above the {euros(target)} target, '
                     'so this goal does not depend on investment returns.')
    else:
        needed = required_return(start, monthly, target, months)
        result['status'] = 'needs_growth'
        result['required_annual_return_pct'] = None if needed is None else round(needed * 100, 1)
        plain.append(f'If you keep saving {euros(monthly)} a month and your money does not grow, '
                     f'you would have about {euros(flat)} by then: {euros(gap)} short of '
                     f'{euros(target)}.')
        if needed is None:
            plain.append('No yearly growth rate up to 10,000% closes that gap in the time left.')
        else:
            plain.append(f'To close the gap through growth alone, your money would have to grow '
                         f'about {needed * 100:.0f}% a year. Every 100 EUR would need to become '
                         f'{100 * (1 + needed):.0f} EUR each year, before fees and taxes.')
        if basis == 'net_worth' and needed is not None:
            invested = sheet.get('invested_assets_eur')
            if isinstance(invested, (int, float)) and 0 <= invested < start:
                plain.append(f'That rate treats everything you own as growing. Only about '
                             f'{euros(invested)} of your {euros(start)} is recorded as invested, '
                             'so the growth needed on the invested part alone is higher.')
        save_instead = required_saving(start, target, 0, months)
        result['saving_needed_without_growth_eur'] = round(save_instead, 2)
        plain.append(f'With no growth at all, you would need to save about {euros(save_instead)} '
                     f'a month instead of {euros(monthly)}.')
        reach = months_to_reach(start, monthly, target, 0) if monthly > 0 else None
        result['date_reached_without_growth'] = (add_months(start_day, reach).isoformat()
                                                 if reach else None)
        plain.append(f'At your current saving and no growth, you would reach the target around '
                     f'{add_months(start_day, reach).isoformat()}.' if reach else
                     'At your current saving and no growth, the target is not reached within '
                     '50 years.')
        if inputs.get('history') is not None and needed is not None:
            if root is None:
                raise StateError('a history comparison needs the repository root')
            over_horizon = (1 + needed) ** (days / 365.25) - 1
            seen = history_check(root, inputs['history'], over_horizon, days, start_day.isoformat())
            result['history'] = seen
            if seen['status'] == 'measured_sample':
                plain.append(
                    f"In the recorded prices of {seen['symbol']} there are {seen['n_windows']} "
                    f"separate periods of this length ({seen['first_window_start']} to "
                    f"{seen['last_window_end']}). A rise this large happened in "
                    f"{seen['n_reached']} of them; the best was "
                    f"{seen['best_window_return'] * 100:.0f}%. These are price changes only, and "
                    'a small number of past periods says little about the future.')
            else:
                plain.append(f"There is not enough recorded price history for {seen['symbol']} to "
                             'compare this with the past, so the system has no evidence either way.')

    rates = inputs.get('what_if_annual_returns_pct', [])
    if not isinstance(rates, list) or len(rates) > 5:
        raise StateError('what_if_annual_returns_pct must be a list of at most five rates')
    scenarios = []
    for rate in rates:
        annual = number(rate, 'what_if_annual_returns_pct') / 100
        if not -0.5 <= annual <= 1:
            raise StateError('what-if yearly rates must be between -50 and 100')
        reach = months_to_reach(start, monthly, target, annual)
        scenarios.append({
            'assumed_annual_return_pct': rate,
            'amount_on_target_date_eur': round(future_value(start, monthly, annual, months), 2),
            'monthly_saving_needed_eur': round(required_saving(start, target, annual, months), 2),
            'date_reached': add_months(start_day, reach).isoformat() if reach else None})
    if scenarios:
        result['what_if'] = scenarios
        result['what_if_note'] = ('Each rate is an assumption supplied by the workflow to show '
                                  'sensitivity. None is a prediction of what markets will do.')
    plain.append('This is arithmetic on your own numbers, not a prediction or a recommendation.')
    return result | {'plain': plain}


def main(argv=None):
    safe_console()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--root', type=Path, default=Path.cwd())
    parser.add_argument('command', choices=('check',))
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--as-of')
    args = parser.parse_args(argv)
    try:
        result = check(read_json(args.root / 'profile/profile.json', 'profile'),
                       read_json(args.root / 'profile/derived.json', 'derived figures'),
                       read_json(args.inputs, 'inputs'), args.root, args.as_of)
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0
    except (StateError, TypeError, ValueError) as exc:
        print(f'goal_check: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
