#!/usr/bin/env python3
"""Read-only household what-if calculations; no probabilities, rankings or recommendations.

python tools/household_plan.py project --assumptions <local.json> --months 12
python tools/household_plan.py compare --assumptions <local.json> --options <local.json>
Inputs and outputs containing household amounts belong under ignored advice/ or decisions/.
"""
from __future__ import annotations

import argparse
import calendar
import datetime as dt
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fa_state import StateError, read_json, safe_console, today


def number(value, name, minimum=None):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or (minimum is not None and value < minimum)):
        raise StateError(f'{name}: a finite number' +
                         (f' >= {minimum}' if minimum is not None else '') + ' is required')
    return float(value)


def date(value, name):
    try:
        return dt.date.fromisoformat(value)
    except (TypeError, ValueError):
        raise StateError(f'{name}: an ISO date is required') from None


def add_months(day, months):
    year, month = divmod(day.year * 12 + day.month - 1 + months, 12)
    return dt.date(year, month + 1, min(day.day, calendar.monthrange(year, month + 1)[1]))


def project(balance, profile, assumptions, months=12, as_of=None, option=None, stress=False):
    """Daily accrual, month-end summaries, dated cash events and sequential goal funding.

    Goals consume cash once, in date/priority order, while protecting the emergency reserve.
    Unfunded goals are explicit shortfalls, never assumed borrowing. No investment is liquidated.
    """
    if not isinstance(assumptions, dict):
        raise StateError('assumptions must be an object')
    allowed_assumptions = {'debt_payments_in_expenses', 'events_complete', 'events',
                           'goal_kinds', 'annual_expense_growth_pct', 'stress'}
    if set(assumptions) - allowed_assumptions:
        raise StateError('unsupported assumptions: ' + ', '.join(sorted(set(assumptions) - allowed_assumptions)))
    if not isinstance(months, int) or isinstance(months, bool) or not 1 <= months <= 120:
        raise StateError('months must be an integer from 1 to 120')
    start = date(as_of or today(), 'as_of')
    end = add_months(start, months)
    flow = balance.get('cash_flow') or {}
    income = number(flow.get('monthly_net_income'), 'monthly_net_income', 0)
    essential = number(flow.get('monthly_essential_expenses'), 'monthly_essential_expenses', 0)
    discretionary = number(flow.get('monthly_discretionary'), 'monthly_discretionary', 0)
    reserve_months = number(flow.get('emergency_fund_target_months'), 'emergency_fund_target_months', 0)
    debts = balance.get('liabilities') or []
    if debts and not isinstance(assumptions.get('debt_payments_in_expenses'), bool):
        raise StateError('confirm debt_payments_in_expenses to avoid counting payments twice')
    debt_payment = (sum(number(row.get('monthly_payment_eur'), 'monthly_payment_eur', 0)
                        for row in debts) if not assumptions.get('debt_payments_in_expenses') else 0)
    cash_rows = [a for a in balance.get('assets', []) if a.get('liquidity_tier') in ('L0', 'L1')]
    if not cash_rows:
        raise StateError('no recorded L0/L1 assets; available cash is unknown')
    cash = sum(number(a.get('value_eur'), 'available asset value_eur', 0) for a in cash_rows)
    warnings = []
    for label, row in [('cash_flow', flow)] + [('available asset', a) for a in cash_rows]:
        recorded = date(row.get('as_of'), label + '.as_of')
        if recorded > start:
            raise StateError(f'{label}: cannot project using a future-dated input')
        if (start - recorded).days > 90:
            warnings.append(f'{label} is older than the profile freshness window; refresh it')
    if assumptions.get('events_complete') is not True:
        warnings.append('Irregular bills and income have not been confirmed complete.')
    if profile.get('liquidity_events'):
        warnings.append('Profile liquidity events must be reconciled with the dated events input; '
                        'they are not automatically added or treated as certain.')
    option = option or {}
    allowed = {'id', 'label', 'cash_change_eur', 'monthly_income_change_eur',
               'monthly_expense_change_eur', 'pausable_monthly_expense_eur', 'fees_eur',
               'tax_cost_eur', 'tax_status', 'source'}
    if set(option) - allowed:
        raise StateError('unsupported option fields: ' + ', '.join(sorted(set(option) - allowed)))
    if option and ('fees_eur' not in option or not option.get('source')):
        raise StateError('each option requires explicit fees_eur and a source or assumption label')
    income += number(option.get('monthly_income_change_eur', 0), 'monthly_income_change_eur')
    expenses = essential + discretionary + debt_payment
    expenses += number(option.get('monthly_expense_change_eur', 0), 'monthly_expense_change_eur')
    if income < 0 or expenses < essential + debt_payment:
        raise StateError('an option cannot assume negative income or cut essential/debt spending')
    # A voluntary contribution can be stopped; rent cannot. Only the part of this option's monthly
    # outflow that the user can switch off may be modelled as pausing during an income loss, and
    # only for as long as the income loss lasts. Default 0 leaves every existing result unchanged.
    pausable = number(option.get('pausable_monthly_expense_eur', 0),
                      'pausable_monthly_expense_eur', 0)
    if pausable and pausable > max(0, number(option.get('monthly_expense_change_eur', 0),
                                             'monthly_expense_change_eur')):
        raise StateError('pausable_monthly_expense_eur cannot exceed this option\'s own positive '
                         'monthly_expense_change_eur: only the option\'s own contribution can be '
                         'paused, never recorded essential spending')
    fees = number(option.get('fees_eur', 0), 'fees_eur', 0)
    tax_status = option.get('tax_status', 'not_applicable' if not option else 'unverified')
    if tax_status not in ('verified', 'unverified', 'not_applicable'):
        raise StateError('tax_status must be verified, unverified or not_applicable')
    tax = option.get('tax_cost_eur')
    if tax_status == 'verified':
        if not option.get('source'):
            raise StateError('verified tax costs require a dated rule/source reference')
        tax = number(tax, 'tax_cost_eur', 0)
    elif tax_status == 'not_applicable':
        if tax not in (None, 0):
            raise StateError('tax cost conflicts with not_applicable')
        tax = 0
    else:
        warnings.append('Tax treatment is unverified; cash figures exclude unknown tax costs.')
        tax = 0
    cash += number(option.get('cash_change_eur', 0), 'cash_change_eur') - fees - tax
    growth = number(assumptions.get('annual_expense_growth_pct', 0), 'annual_expense_growth_pct')
    if not -50 <= growth <= 100:
        raise StateError('annual_expense_growth_pct must be between -50 and 100')
    shock = assumptions.get('stress', {}) if stress else {}
    if not isinstance(shock, dict) or set(shock) - {'income_loss_pct', 'income_loss_months',
                                                  'expense_increase_pct', 'investment_fall_pct'}:
        raise StateError('unsupported stress assumptions')
    if stress and not shock:
        warnings.append('No adverse shock supplied; this is not a completed stress test.')
    loss = number(shock.get('income_loss_pct', 0), 'income_loss_pct', 0)
    extra = number(shock.get('expense_increase_pct', 0), 'expense_increase_pct', 0)
    duration = number(shock.get('income_loss_months', 0), 'income_loss_months', 0)
    if loss > 100 or duration > months or not duration.is_integer():
        raise StateError('income loss must be at most 100%; duration must be whole months within horizon')
    if loss > 0 and duration == 0:
        raise StateError('income loss requires a positive duration')
    shock_end = add_months(start, int(duration))
    if pausable:
        if stress and loss > 0:
            warnings.append(
                f'{pausable:.2f} EUR a month of this option is modelled as paused for the '
                f'{int(duration)} month(s) of the income loss. That is a stated intention, not '
                'recorded behaviour, and it flatters this option against one that cannot be paused.')
        else:
            warnings.append(
                f'{pausable:.2f} EUR a month is declared pausable but nothing pauses it here: '
                'this run has no income loss, so the figure changes nothing.')
    investment_fall = number(shock.get('investment_fall_pct', 0), 'investment_fall_pct', 0)
    if investment_fall > 100:
        raise StateError('investment_fall_pct cannot exceed 100')
    investment_loss = (sum(number(h.get('value_eur'), 'holding.value_eur', 0)
                           for h in balance.get('holdings', [])) * investment_fall / 100
                       if investment_fall else 0)
    events = {}
    for event in assumptions.get('events', []):
        when = date(event.get('on'), 'event.on')
        if when <= start or when > end:
            raise StateError('dated events must fall after as_of and within the projection horizon')
        if not event.get('source'):
            raise StateError('each event needs a source or an explicit assumption label')
        events[when] = events.get(when, 0) + number(event.get('amount_eur'), 'event.amount_eur')
    due, goals = {}, []
    seen = set()
    for goal in profile.get('goals', []):
        key = goal.get('goal_id')
        if not key or key in seen:
            raise StateError('goals require unique goal_id values')
        seen.add(key)
        row = {'goal_id': key, 'target_date': goal.get('target_date')}
        goal_kind = (assumptions.get('goal_kinds') or {}).get(key)
        if goal_kind != 'cash_outlay':
            goals.append(row | {'status': 'not_modelled',
                                'reason': 'classify as cash_outlay to model; wealth and retirement '
                                          'targets are not cash payments'})
            continue
        if goal.get('target_amount_eur') is None or not goal.get('target_date'):
            goals.append(row | {'status': 'unknown', 'reason': 'target amount or date is missing'})
            continue
        target = number(goal['target_amount_eur'], 'goal.target_amount_eur', 0)
        when = date(goal['target_date'], 'goal.target_date')
        if when <= start:
            goals.append(row | {'status': 'needs_review', 'reason': 'goal is due or past due'})
        elif when > end:
            goals.append(row | {'status': 'outside_horizon'})
        else:
            priority = number(goal.get('priority', 99), 'goal.priority', 0)
            due.setdefault(when, []).append((priority, key, target, row))
    reserve = (essential + debt_payment) * reserve_months
    first_shortfall = start.isoformat() if cash < reserve - 0.005 else None
    minimum = cash
    timeline = []
    day = start + dt.timedelta(days=1)
    while day <= end:
        factor = (1 + growth / 100) ** ((day - start).days / 365.25)
        paused = pausable if (loss > 0 and day <= shock_end) else 0
        expense = (expenses - paused) * factor * (1 + extra / 100)
        daily_income = income * (1 - loss / 100 if day <= shock_end else 1)
        cash += (daily_income - expense) / calendar.monthrange(day.year, day.month)[1]
        cash += events.get(day, 0)
        reserve = (essential + debt_payment) * factor * (1 + extra / 100) * reserve_months
        for _, _, target, row in sorted(due.get(day, []), key=lambda item: item[:2]):
            funded = min(target, max(0, cash - reserve))
            cash -= funded
            goals.append(row | {'status': 'covered' if funded >= target - 0.005 else 'shortfall',
                                'funded_eur': round(funded, 2),
                                'shortfall_eur': round(target - funded, 2)})
        minimum = min(minimum, cash)
        if cash < reserve - 0.005 and first_shortfall is None:
            first_shortfall = day.isoformat()
        if day.day == calendar.monthrange(day.year, day.month)[1] or day == end:
            timeline.append({'on': day.isoformat(), 'cash_eur': round(cash, 2),
                             'protected_reserve_eur': round(reserve, 2),
                             'available_above_reserve_eur': round(max(0, cash - reserve), 2)})
        day += dt.timedelta(days=1)
    return {'kind': 'what_if', 'tool': 'household_plan.py', 'as_of': start.isoformat(),
            'through': end.isoformat(), 'option_id': option.get('id', 'do_nothing'),
            'stress': stress, 'assumptions': assumptions, 'option': option,
            'tax_status': tax_status, 'warnings': warnings, 'goals': goals, 'timeline': timeline,
            'ending_cash_eur': round(cash, 2), 'minimum_cash_eur': round(minimum, 2),
            'first_reserve_shortfall': first_shortfall,
            'investment_stress_loss_eur': round(investment_loss, 2),
            'investment_stress_scope': 'Current recorded holdings only; use portfolio_math.py '
                                       'stress --action for the post-action portfolio.',
            'sources': ['ledger:cash_flow.*', 'ledger:assets[*].value_eur', 'ledger:goals[*]'],
            'note': 'Conditional calculation, not a probability or recommendation. Cash flows '
                    'accrue daily; investment returns and sales are not assumed. Goals use cash '
                    'once in date/priority order. Cash transfers are not gains in net worth. '
                    'The combined stress is illustrative, not a predicted event.'}


def compare(balance, profile, assumptions, options, months=12, as_of=None):
    if not isinstance(options, list) or not all(isinstance(o, dict) for o in options):
        raise StateError('options must be a list of objects')
    ids = [o.get('id') for o in options]
    if any(not isinstance(key, str) or not key for key in ids) or len(set(ids)) != len(ids) or 'do_nothing' in ids:
        raise StateError('option ids must be unique; do_nothing is included automatically')
    results = [project(balance, profile, assumptions, months, as_of, option, stressed)
               for option in [None, *options] for stressed in (False, True)]
    return {'kind': 'what_if_comparison', 'results': results,
            'note': 'No option is ranked or approved. Vet each candidate separately; unknown '
                    'tax costs prevent an after-tax comparison. Do nothing uses the same inputs.'}


def main(argv=None):
    safe_console()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--root', type=Path, default=Path.cwd())
    parser.add_argument('command', choices=('project', 'compare'))
    parser.add_argument('--assumptions', type=Path, required=True)
    parser.add_argument('--options', type=Path)
    parser.add_argument('--months', type=int, default=12)
    parser.add_argument('--as-of')
    args = parser.parse_args(argv)
    try:
        balance = read_json(args.root / 'profile/balance_sheet.json', 'balance sheet')
        profile = read_json(args.root / 'profile/profile.json', 'profile')
        assumptions = read_json(args.assumptions, 'assumptions')
        if args.command == 'compare':
            if not args.options:
                raise StateError('compare requires --options')
            result = compare(balance, profile, assumptions, read_json(args.options, 'options'),
                             args.months, args.as_of)
        else:
            result = project(balance, profile, assumptions, args.months, args.as_of)
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0
    except (StateError, TypeError, ValueError) as exc:
        print(f'household_plan: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
