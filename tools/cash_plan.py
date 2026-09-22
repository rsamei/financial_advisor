#!/usr/bin/env python3
"""Split available cash into what is already needed and what is free; never a verdict.

python tools/cash_plan.py plan  --inputs <local cash_question.json>
python tools/cash_plan.py check --inputs <local cash_question.json> --proposal <local proposal.json>
Inputs and outputs contain household amounts and belong under the ignored advice/ run directory.

`plan` answers "how much of my cash is spoken for, and what could the rest be used for". It
applies only rules that already exist: the emergency reserve (02), cash for anything needed
within three years stays cash (06 ladder), high-interest debt before investing (06). It lists
the eligible uses of the free remainder, always including keeping it as cash, and ranks none
of them beyond those existing rules. `check` reconciles a proposal whose components were vetted
through /decide: amounts must fit the free remainder once, and a blocked or waiting component
returns its money to the unallocated line. The opening sentence never recommends a blocked step.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from decision_score import score  # noqa: E402
from fa_state import StateError, read_json, safe_console, today  # noqa: E402

HIGH_INTEREST_PCT = 6.0       # 06-portfolio-construction.md, "Debt versus investing"
CASH_HORIZON_MONTHS = 36      # 06-portfolio-construction.md, cash placement ladder: 3 years+ is not cash
FRESH_DAYS = 90               # 02-balance-sheet-and-cashflow.md, stale_fields
BLOCKED = ('gated', 'reject', 'park')
STATUS_WORDS = {'do_now': 'reasonable to consider', 'do_scoped': 'only as a smaller or staged step',
                'park': 'wait for now', 'reject': 'do not proceed',
                'gated': 'do not proceed; a required check failed'}


def money(value, name, minimum=0):
    if (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
            or (minimum is not None and value < minimum)):
        raise StateError(f'{name}: a finite number >= {minimum} is required')
    return round(float(value), 2)


def iso(value, name):
    try:
        return dt.date.fromisoformat(value)
    except (TypeError, ValueError):
        raise StateError(f'{name}: an ISO date is required') from None


def euros(value):
    return f'€{value:,.0f}'


def months_between(start, end):
    return (end.year - start.year) * 12 + end.month - start.month - (end.day < start.day)


def sourced_rows(rows, name):
    if not isinstance(rows, list):
        raise StateError(f'{name} must be a list')
    for row in rows:
        if not isinstance(row, dict) or not row.get('label') or not row.get('source'):
            raise StateError(f'each {name} row needs a label and a source')
        money(row.get('amount_eur'), f'{name}.amount_eur')
    return rows


def plan(balance, profile, question):
    if not isinstance(question, dict):
        raise StateError('inputs must be an object')
    allowed = {'as_of', 'scope', 'windfall', 'arrears', 'debt_payments_in_expenses', 'earmarks',
               'restricted', 'commitments', 'goal_kinds', 'ips_status'}
    if set(question) - allowed:
        raise StateError('unsupported inputs: ' + ', '.join(sorted(set(question) - allowed)))
    start = iso(question.get('as_of') or today(), 'as_of')
    scope = question.get('scope', 'current_balances')
    if scope not in ('current_balances', 'windfall', 'monthly_surplus'):
        raise StateError('scope must be current_balances, windfall or monthly_surplus')
    unknowns, warnings, sources = [], [], ['ledger:cash_flow.*', 'ledger:assets[*].value_eur']
    flow = balance.get('cash_flow') or {}
    fields = {}
    for key in ('monthly_net_income', 'monthly_essential_expenses', 'monthly_discretionary'):
        fields[key] = None if flow.get(key) is None else money(flow[key], key)
        if fields[key] is None:
            unknowns.append(f'Your {key.replace("monthly_", "monthly ").replace("_", " ")} is not recorded.')
    reserve_months = flow.get('emergency_fund_target_months')
    if reserve_months is None:
        unknowns.append('How many months of essential spending you want as a safety cushion is not set.')
    else:
        reserve_months = money(reserve_months, 'emergency_fund_target_months')
    debts = balance.get('liabilities') or []
    in_expenses = question.get('debt_payments_in_expenses')
    if debts and not isinstance(in_expenses, bool):
        raise StateError('confirm debt_payments_in_expenses to avoid counting payments twice')
    debt_payment = 0 if in_expenses else sum(money(d.get('monthly_payment_eur'), 'monthly_payment_eur')
                                             for d in debts)

    cash_rows = [a for a in balance.get('assets', []) if a.get('liquidity_tier') in ('L0', 'L1')]
    if not cash_rows:
        raise StateError('no recorded L0/L1 assets; available cash is unknown')
    stale = False
    for label, row in [('cash_flow', flow)] + [('cash account', a) for a in cash_rows]:
        recorded = iso(row.get('as_of'), label + '.as_of')
        if recorded > start:
            raise StateError(f'{label}: a future-dated input cannot be used')
        if (start - recorded).days > FRESH_DAYS:
            stale = True
    if stale:
        warnings.append('Some balances or budget figures are more than 90 days old; confirm them before acting.')
    on_hand = round(sum(money(a.get('value_eur'), 'cash value_eur') for a in cash_rows), 2)
    windfall = 0.0
    if scope == 'windfall':
        row = question.get('windfall') or {}
        if not row.get('source'):
            raise StateError('a windfall needs amount_eur and a source')
        windfall = money(row.get('amount_eur'), 'windfall.amount_eur')
        if row.get('already_in_balances') is True:
            windfall = 0.0
    restricted = sourced_rows(question.get('restricted', []), 'restricted')
    blocked_cash = round(sum(r['amount_eur'] for r in restricted), 2)
    if blocked_cash > on_hand + windfall + 0.005:
        raise StateError('restricted cash exceeds the recorded cash')
    usable = round(on_hand + windfall - blocked_cash, 2)

    lines = []   # the allocation table: what the usable cash is already needed for

    def take(kind, label, need, source, goal_id=None):
        nonlocal left
        placed = round(min(need, max(0.0, left)), 2)
        left = round(left - placed, 2)
        lines.append({'kind': kind, 'label': label, 'needed_eur': round(need, 2),
                      'allocated_eur': placed, 'shortfall_eur': round(need - placed, 2),
                      'source': source} | ({'goal_id': goal_id} if goal_id else {}))

    left = usable
    earmarks = sourced_rows(question.get('earmarks', []), 'earmarks')
    by_goal = {}
    for row in earmarks:
        # Money set aside for a goal is taken once here and reduces that goal's need below.
        if row.get('goal_id'):
            by_goal[row['goal_id']] = by_goal.get(row['goal_id'], 0) + row['amount_eur']
        take('earmark', row['label'], row['amount_eur'], row['source'], row.get('goal_id'))

    essential, income = fields['monthly_essential_expenses'], fields['monthly_net_income']
    reserve_known = reserve_months is not None and essential is not None
    if reserve_known:
        take('reserve', 'Safety cushion', (essential + debt_payment) * reserve_months,
             'ledger:cash_flow.emergency_fund_target_months')
    horizon_end = start.replace(year=start.year + 1) if start.month != 2 or start.day != 29 else start + dt.timedelta(days=365)
    for row in sourced_rows(question.get('commitments', []), 'commitments'):
        due = iso(row.get('due'), 'commitments.due')
        if due < start:
            raise StateError('a commitment is already past due; record it as arrears instead')
        if due <= horizon_end:
            take('commitment', row['label'], row['amount_eur'], row['source'])

    kinds = question.get('goal_kinds') or {}
    goals = profile.get('goals') or []
    near, long_goals = [], []
    for goal in goals:
        key = goal.get('goal_id')
        kind = kinds.get(key)
        if kind not in ('cash_outlay', 'wealth'):
            unknowns.append(f'Goal {key}: whether it is a payment you will make or a savings level is not confirmed.')
            continue
        if goal.get('target_amount_eur') is None or not goal.get('target_date'):
            unknowns.append(f'Goal {key}: the amount or date is missing.')
            continue
        when = iso(goal['target_date'], 'goal.target_date')
        months = months_between(start, when)
        if kind == 'cash_outlay' and months <= CASH_HORIZON_MONTHS:
            near.append((when, goal.get('priority') or 99, key, goal))
        else:
            long_goals.append({'goal_id': key, 'months_left': months, 'kind': kind})
    for when, _, key, goal in sorted(near, key=lambda item: item[:3]):
        need = max(0.0, money(goal['target_amount_eur'], 'goal.target_amount_eur') - by_goal.get(key, 0))
        take('goal', goal.get('description') or f'Goal {key}', need, 'ledger:goals[*]', key)
        sources.append('ledger:goals[*]')

    spoken_for = round(sum(line['allocated_eur'] for line in lines), 2)
    shortfall = round(sum(line['shortfall_eur'] for line in lines), 2)
    surplus = (None if None in (income, essential, fields['monthly_discretionary'])
               else round(income - essential - fields['monthly_discretionary'] - debt_payment, 2))
    arrears = question.get('arrears')
    if arrears is None:
        unknowns.append('Whether any bill or loan payment is overdue has not been confirmed.')

    if arrears is True or (surplus is not None and surplus < 0):
        stage = 'stabilise'
    elif not reserve_known:
        stage = 'reserve_unknown'
    elif shortfall > 0.005:
        stage = 'short'
    elif left > 0.005:
        stage = 'free_cash'
    else:
        stage = 'fully_needed'

    candidates = [{'id': 'keep_as_cash', 'label': 'Keep it as cash', 'status': 'always_available',
                   'why': 'Keeps every option open; it earns only what the account pays.'}]
    high = [d for d in debts if d.get('rate_pct') is not None and money(d['rate_pct'], 'rate_pct') > HIGH_INTEREST_PCT]
    unknown_rate = [d for d in debts if d.get('rate_pct') is None]
    if unknown_rate:
        unknowns.append('The interest rate on at least one debt is not recorded.')
    for debt in high:
        candidates.append({'id': f'repay_{debt.get("type") or "debt"}', 'label': 'Pay down expensive debt',
                           'status': 'first_under_existing_rule', 'rate_pct': debt['rate_pct'],
                           'why': f'It charges {debt["rate_pct"]}% a year; the policy repays debt above '
                                  f'{HIGH_INTEREST_PCT:g}% before any investing, and repayment is a certain saving.',
                           'needs': 'early-repayment terms and fees (prepayment_penalty)'})
    for debt in debts:
        if debt not in high and debt not in unknown_rate:
            candidates.append({'id': f'prepay_{debt.get("type") or "debt"}', 'label': 'Pay extra off a cheaper loan',
                               'status': 'needs_comparison', 'rate_pct': debt['rate_pct'],
                               'why': 'Only worth it if it beats the after-tax return of the alternatives.',
                               'needs': 'early-repayment terms and verified tax treatment'})
    if balance.get('pension'):
        candidates.append({'id': 'pension', 'label': 'Add to a pension', 'status': 'needs_verified_rule',
                           'why': 'May carry a tax benefit, but the money becomes hard to reach.',
                           'needs': 'verified Italian pension deduction rules and your eligibility'})
    ips = question.get('ips_status', 'missing')
    if ips not in ('frozen', 'draft', 'missing'):
        raise StateError('ips_status must be frozen, draft or missing')
    invest = {'id': 'invest_long_term', 'label': 'Invest for the long term (money not needed for 3+ years)',
              'why': 'Only money you will not need for at least three years belongs here.'}
    if high:
        invest |= {'status': 'blocked', 'blocked_by': 'expensive debt is outstanding'}
    elif ips != 'frozen':
        invest |= {'status': 'blocked', 'blocked_by': 'your written investment plan is not confirmed yet'}
    else:
        invest |= {'status': 'needs_vetting'}
    candidates.append(invest)

    result = {'kind': 'cash_plan', 'tool': 'cash_plan.py', 'as_of': start.isoformat(), 'scope': scope,
              'cash_on_hand_eur': on_hand, 'windfall_eur': windfall, 'restricted_eur': blocked_cash,
              'usable_cash_eur': usable, 'allocations': lines, 'spoken_for_eur': spoken_for,
              'shortfall_eur': shortfall, 'free_eur': round(left, 2),
              'monthly_surplus_eur': surplus, 'stage': stage, 'candidates': candidates,
              'long_term_goals': long_goals, 'unknowns': unknowns, 'warnings': warnings,
              'stale_inputs': stale, 'sources': sorted(set(sources)),
              'note': 'Arithmetic on confirmed facts using existing rules; not a verdict. Every '
                      'use of the free amount is vetted separately before it is suggested.'}
    if abs(result['spoken_for_eur'] + result['free_eur'] - usable) > 0.01:
        raise StateError('internal reconciliation failed: allocations do not add up to usable cash')
    result['plain'] = plain(result)
    return result


def plain(r):
    needed = [line for line in r['allocations'] if line['allocated_eur'] > 0]
    kept = ', '.join(f'{euros(line["allocated_eur"])} for {line["label"].lower()}' for line in needed)
    stage = r['stage']
    first = next((c for c in r['candidates'] if c['status'] == 'first_under_existing_rule'), None)
    if stage == 'stabilise':
        answer = ('Keep your cash where it is. Your monthly budget or an overdue payment needs '
                  'fixing first, so none of it should be invested or locked away yet.')
    elif stage == 'reserve_unknown':
        answer = ('Keep your cash available for now. I need to know how big a safety cushion you '
                  'want before I can say how much of it is free.')
    elif stage == 'short':
        answer = (f'Keep all of it where it is: it is already needed ({kept}), and you are still '
                  f'{euros(r["shortfall_eur"])} short of those needs.')
    elif stage == 'fully_needed':
        answer = f'Keep all of it where it is: it is exactly what you need ({kept}).'
    else:
        lead = f'Keep {kept}. ' if kept else ''
        answer = (f'{lead}That leaves about {euros(r["free_eur"])} free. '
                  + (f'The first use to check is paying down the debt charging {first["rate_pct"]}% a year.'
                     if first else 'Its best use still has to be checked; keeping it as cash is the fallback.'))
    surplus = r['monthly_surplus_eur']
    if r['scope'] == 'monthly_surplus' and surplus is not None:
        if r['shortfall_eur'] > 0 and surplus > 0:
            answer += (f' Your monthly spare money of {euros(surplus)} would close the gap in about '
                       f'{math.ceil(r["shortfall_eur"] / surplus)} months if it all went there.')
        elif surplus <= 0:
            answer += ' You have no spare money each month at the moment.'
    why = ['Money you may need within three years, and your safety cushion, stay in cash because '
           'investments can be down exactly when you need them.']
    blocked = [c for c in r['candidates'] if c['status'] == 'blocked']
    for c in blocked:
        why.append(f'{c["label"]} is not an option yet: {c["blocked_by"]}.')
    changes = ['A change in your income or spending', 'A new or moved goal date',
               'New or changed loan terms']
    next_step = (r['unknowns'][0] if r['unknowns'] else
                 'Check the first option above in detail before moving any money.'
                 if stage == 'free_cash' else 'Nothing to do now; look again when your situation changes.')
    return {'answer': answer, 'why': why[:3], 'next_step': next_step, 'would_change': changes,
            'unknowns': r['unknowns'] + r['warnings'],
            'do_nothing': 'If you change nothing, the money stays safe and available, and earns only what the account pays.'}


def check(result, proposal):
    """Reconcile vetted components against the free amount; blocked parts stay unallocated."""
    if not isinstance(proposal, list) or not proposal:
        raise StateError('proposal must be a non-empty list')
    options = {c['id']: c for c in result['candidates']}
    free = result['free_eur']
    used, rows, seen = 0.0, [], set()
    for part in proposal:
        use = part.get('use')
        if use not in options or use in seen:
            raise StateError(f'unknown or repeated use: {use}')
        seen.add(use)
        if use == 'keep_as_cash':
            raise StateError('keep_as_cash is the unallocated remainder, not a component')
        amount = money(part.get('amount_eur'), 'amount_eur')
        verdict = score(part.get('findings'))
        band = verdict['band']
        option = options[use]
        if option['status'] == 'blocked':
            band_note = 'blocked: ' + option['blocked_by']
            band = band if band in BLOCKED else 'park'
        else:
            band_note = None
        counted = 0.0 if band in BLOCKED else amount
        if used + counted > free + 0.005:
            raise StateError(f'{use}: components exceed the free amount ({free}); cash cannot be used twice')
        used += counted
        rows.append({'use': use, 'label': option['label'], 'amount_eur': amount,
                     'decision_id': verdict['decision_id'], 'band': band,
                     'status': STATUS_WORDS[band], 'counted_eur': counted, 'blocked_by': band_note})
    if any(r['use'] == 'invest_long_term' and r['counted_eur'] for r in rows) and any(
            c['status'] == 'first_under_existing_rule' for c in result['candidates']):
        raise StateError('investing cannot be counted while expensive debt is outstanding')
    unallocated = round(free - used, 2)
    go = [r for r in rows if r['counted_eur']]
    if result['stage'] != 'free_cash' or not go:
        opening = result['plain']['answer'] if result['stage'] != 'free_cash' else (
            f'Keep the {euros(free)} free cash as it is for now: none of the checked uses is ready.')
    else:
        parts = [f'{r["label"].lower()} with {euros(r["amount_eur"])} ({r["status"]})' for r in go]
        opening = ('Consider ' + '; then '.join(parts)
                   + (f', and keep the remaining {euros(unallocated)} as cash.' if unallocated > 0.005 else '.'))
    return {'kind': 'cash_plan_check', 'tool': 'cash_plan.py', 'components': rows,
            'free_eur': free, 'counted_eur': round(used, 2), 'unallocated_eur': unallocated,
            'opening': opening,
            'waiting': [f'{r["label"]}: {r["status"]}' + (f' ({r["blocked_by"]})' if r['blocked_by'] else '')
                        for r in rows if not r['counted_eur']]}


def main(argv=None):
    safe_console()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--root', type=Path, default=Path.cwd())
    parser.add_argument('command', choices=('plan', 'check'))
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--proposal', type=Path)
    args = parser.parse_args(argv)
    try:
        balance = read_json(args.root / 'profile/balance_sheet.json', 'balance sheet')
        profile = read_json(args.root / 'profile/profile.json', 'profile')
        result = plan(balance, profile, read_json(args.inputs, 'cash question'))
        if args.command == 'check':
            if not args.proposal:
                raise StateError('check requires --proposal')
            result = check(result, read_json(args.proposal, 'proposal'))
        print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
        return 0
    except (StateError, TypeError, ValueError, KeyError) as exc:
        print(f'cash_plan: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
