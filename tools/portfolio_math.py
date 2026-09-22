#!/usr/bin/env python3
"""Portfolio arithmetic: drift, concentration, stress, tax lots and a rebalance proposal.

Usage (from the repository root):
    python tools/portfolio_math.py allocation [--root .]
    python tools/portfolio_math.py drift      --targets <file>
    python tools/portfolio_math.py stress     [--action <file>]
    python tools/portfolio_math.py rebalance  --targets <file> [--band 5]
    python tools/portfolio_math.py lots       --symbol SYN0000001 --units 100
    python tools/portfolio_math.py cost-drag

Every number a report quotes about the user's own money is computed here, from
`profile/balance_sheet.json`, and never by an agent. Agents receive ratios; this tool owns the
absolute arithmetic, which is why no prompt in this repository needs to see a balance.

What this tool enforces that prose cannot:

- **Stress is applied to the post-action balance sheet**, not the current one. The question a gate
  asks is whether the household still works *after* the thing it is about to do.
- **Tax lots use the Italian average-cost method** (costo medio ponderato), because that is what
  the user's broker will actually apply - a FIFO estimate would produce a gain figure that differs
  from their statement and quietly misstate the tax.
- **A rebalance proposal respects bands**, and proposes nothing when every class is inside its
  band. Trading because a number moved 1 % is a cost, not a strategy.
- **Nothing here decides anything.** It computes; `decision_score.py` judges. A drift figure is not
  an instruction to trade.

Stdlib only. Exit 0 ok; 1 state or schema error; 4 bad arguments.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fa_state import StateError, read_json, safe_console  # noqa: E402

# Stress scenarios (04). Magnitudes are calibrated to history, not predicted.
SCENARIOS = {
    'equity_drawdown': {'label': 'equities -30 %', 'equity': -0.30},
    'rate_shock': {'label': 'rates +2 pp (bond value by duration)', 'rate_pp': 2.0},
    'currency_move': {'label': 'EUR/USD -10 % on unhedged non-EUR', 'fx': -0.10},
    'crypto_drawdown': {'label': 'crypto -70 %', 'crypto': -0.70},
    'income_loss': {'label': 'no income for 6 months', 'months_without_income': 6},
}
# Used only when a bond holding declares no duration: a broad euro aggregate sits near 6 years.
DEFAULT_BOND_DURATION_YEARS = 6.0
CASH_LIKE = ('cash', 'deposit')
# Rebalance bands (06): absolute for major classes, relative for small ones.
DEFAULT_ABSOLUTE_BAND_PP = 5.0
DEFAULT_RELATIVE_BAND = 0.25
MINOR_CLASS_THRESHOLD_PCT = 10.0


def load_balance(root: Path) -> dict:
    return read_json(Path(root) / 'profile' / 'balance_sheet.json', 'balance_sheet.json')


def _value(item, field='value_eur'):
    value = item.get(field)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def holdings_by_class(balance: dict) -> dict[str, float]:
    """Cash-like asset rows count as `cash`; holdings carry their own class."""
    out: dict[str, float] = {}
    for asset in balance.get('assets') or []:
        value = _value(asset)
        if value is not None and asset.get('type') in CASH_LIKE:
            out['cash'] = out.get('cash', 0.0) + value
    for holding in balance.get('holdings') or []:
        value = _value(holding)
        if value is None:
            continue
        name = holding.get('asset_class') or 'unknown'
        out[name] = out.get(name, 0.0) + value
    return out


def invested_total(balance: dict) -> float:
    return sum(holdings_by_class(balance).values())


def allocation(balance: dict) -> dict:
    by_class = holdings_by_class(balance)
    total = sum(by_class.values())
    if not total:
        return {'invested_eur': 0.0, 'by_class_pct': {}, 'by_class_eur': {},
                'note': 'no invested assets recorded'}
    return {
        'invested_eur': round(total, 2),
        'by_class_eur': {k: round(v, 2) for k, v in sorted(by_class.items())},
        'by_class_pct': {k: round(v / total * 100, 2) for k, v in sorted(by_class.items())},
    }


def concentration(balance: dict) -> dict:
    total = invested_total(balance)
    issuers: dict[str, float] = {}
    sectors: dict[str, float] = {}
    for holding in balance.get('holdings') or []:
        value = _value(holding)
        if value is None:
            continue
        if holding.get('issuer'):
            issuers[holding['issuer']] = issuers.get(holding['issuer'], 0.0) + value
        if holding.get('sector'):
            sectors[holding['sector']] = sectors.get(holding['sector'], 0.0) + value
    unlabelled = [h.get('isin_or_symbol') for h in balance.get('holdings') or []
                  if _value(h) is not None and not h.get('issuer')]

    def top(mapping):
        if not mapping or not total:
            return None, None
        name = max(mapping, key=mapping.get)
        return name, round(mapping[name] / total * 100, 2)

    issuer_name, issuer_pct = top(issuers)
    sector_name, sector_pct = top(sectors)
    return {
        'invested_eur': round(total, 2),
        'largest_issuer': issuer_name, 'largest_issuer_pct': issuer_pct,
        'largest_sector': sector_name, 'largest_sector_pct': sector_pct,
        'crypto_share_pct': round(holdings_by_class(balance).get('crypto', 0.0) / total * 100, 2)
        if total else None,
        # Named rather than ignored: an unlabelled holding could be the concentration.
        'holdings_without_issuer': unlabelled,
    }


def currency_exposure(balance: dict) -> dict:
    total = invested_total(balance)
    by_currency: dict[str, float] = {}
    for asset in balance.get('assets') or []:
        value = _value(asset)
        if value is not None and asset.get('type') in CASH_LIKE:
            code = asset.get('currency') or 'EUR'
            by_currency[code] = by_currency.get(code, 0.0) + value
    for holding in balance.get('holdings') or []:
        value = _value(holding)
        if value is None:
            continue
        code = holding.get('currency') or 'EUR'
        by_currency[code] = by_currency.get(code, 0.0) + value
    non_eur = sum(v for k, v in by_currency.items() if k != 'EUR')
    return {
        'by_currency_pct': {k: round(v / total * 100, 2) for k, v in sorted(by_currency.items())}
        if total else {},
        'non_eur_pct': round(non_eur / total * 100, 2) if total else None,
    }


def cost_drag(balance: dict) -> dict:
    """Weighted TER across holdings that declare one, plus what it costs per year in EUR."""
    weighted, covered, missing = 0.0, 0.0, []
    for holding in balance.get('holdings') or []:
        value = _value(holding)
        if value is None:
            continue
        ter = _value(holding, 'ter_pct')
        if ter is None:
            missing.append(holding.get('isin_or_symbol'))
            continue
        weighted += value * ter
        covered += value
    if not covered:
        return {'weighted_ter_pct': None, 'annual_cost_eur': None, 'coverage_pct': 0.0,
                'holdings_without_ter': missing}
    ter_pct = weighted / covered
    total = invested_total(balance)
    return {
        'weighted_ter_pct': round(ter_pct, 4),
        'annual_cost_eur': round(covered * ter_pct / 100, 2),
        'coverage_pct': round(covered / total * 100, 2) if total else None,
        # A 0.05 % headline TER across half the portfolio is not a 0.05 % portfolio.
        'holdings_without_ter': missing,
    }


def drift(balance: dict, targets: dict) -> dict:
    """Current allocation against target weights. Targets are percentages summing to ~100."""
    if not isinstance(targets, dict) or not targets:
        raise StateError('targets must be an object of {asset_class: target_pct}')
    total_target = sum(float(v) for v in targets.values())
    if abs(total_target - 100) > 0.51:
        raise StateError(f'target weights sum to {total_target:g} %, not 100 %')
    current = allocation(balance)['by_class_pct']
    rows = []
    for name in sorted(set(targets) | set(current)):
        target = float(targets.get(name, 0))
        actual = float(current.get(name, 0))
        rows.append({'asset_class': name, 'target_pct': round(target, 2),
                     'current_pct': round(actual, 2),
                     'drift_pp': round(actual - target, 2),
                     'relative_drift': round((actual - target) / target, 4) if target else None})
    worst = max(rows, key=lambda row: abs(row['drift_pp'])) if rows else None
    return {'rows': rows, 'largest_drift_pp': worst['drift_pp'] if worst else None,
            'largest_drift_class': worst['asset_class'] if worst else None}


def _outside_band(row: dict, absolute_band: float, relative_band: float) -> bool:
    """Major classes use an absolute band; small ones use a relative band (06)."""
    if row['target_pct'] >= MINOR_CLASS_THRESHOLD_PCT:
        return abs(row['drift_pp']) > absolute_band
    return row['relative_drift'] is not None and abs(row['relative_drift']) > relative_band


def rebalance(balance: dict, targets: dict, absolute_band: float = DEFAULT_ABSOLUTE_BAND_PP,
              relative_band: float = DEFAULT_RELATIVE_BAND) -> dict:
    report = drift(balance, targets)
    total = invested_total(balance)
    trades = []
    for row in report['rows']:
        if not _outside_band(row, absolute_band, relative_band):
            continue
        delta_eur = round((row['target_pct'] - row['current_pct']) / 100 * total, 2)
        trades.append({'asset_class': row['asset_class'],
                       'direction': 'buy' if delta_eur > 0 else 'sell',
                       'amount_eur': abs(delta_eur), 'drift_pp': row['drift_pp'],
                       'band_rule': ('absolute ±%.1f pp' % absolute_band
                                     if row['target_pct'] >= MINOR_CLASS_THRESHOLD_PCT
                                     else 'relative %.0f %%' % (relative_band * 100))})
    return {
        'trades': trades,
        'drift': report,
        'bands': {'absolute_pp': absolute_band, 'relative': relative_band,
                  'minor_class_threshold_pct': MINOR_CLASS_THRESHOLD_PCT},
        'note': ('every class is inside its band; rebalancing now would be a cost, not a strategy'
                 if not trades else
                 'a sale may realise a taxable gain - check 07 before executing'),
    }


def average_cost_lots(balance: dict, symbol: str, units: float) -> dict:
    """Italian average-cost basis (costo medio ponderato) for a partial sale.

    This is the method the broker will apply in regime amministrato. Computing FIFO instead would
    produce a gain that does not match the statement, and a tax figure nobody can reconcile.
    """
    matching = [h for h in balance.get('holdings') or [] if h.get('isin_or_symbol') == symbol]
    if not matching:
        raise StateError(f'no holding with isin_or_symbol {symbol!r}')
    total_units = sum(_value(h, 'units') or 0.0 for h in matching)
    if total_units <= 0:
        raise StateError(f'{symbol}: no units recorded')
    if units <= 0 or units > total_units:
        raise StateError(f'{symbol}: asked to sell {units:g} of {total_units:g} units held')
    total_cost = sum((_value(h, 'units') or 0.0) * (_value(h, 'avg_cost_eur') or 0.0)
                     for h in matching)
    total_value = sum(_value(h) or 0.0 for h in matching)
    average_cost = total_cost / total_units
    price = total_value / total_units
    proceeds = price * units
    basis = average_cost * units
    return {
        'symbol': symbol, 'units_sold': units, 'units_held': total_units,
        'average_cost_eur': round(average_cost, 4), 'price_eur': round(price, 4),
        'proceeds_eur': round(proceeds, 2), 'cost_basis_eur': round(basis, 2),
        'gain_eur': round(proceeds - basis, 2),
        'method': 'average cost (costo medio ponderato)',
        'tax_note': 'the rate and the gains/losses asymmetry are in 07; no rate is applied here '
                    'until that row carries an official source and a verified_on date',
    }


def stress(balance: dict, action: dict | None = None) -> dict:
    """Apply each scenario to the POST-action balance sheet and report what survives."""
    working = apply_action(balance, action) if action else balance
    by_class = holdings_by_class(working)
    flow = working.get('cash_flow') or {}
    essential = _value(flow, 'monthly_essential_expenses')
    liquid = sum(_value(a) or 0.0 for a in working.get('assets') or []
                 if a.get('liquidity_tier') in ('L0', 'L1'))
    exposure = currency_exposure(working)
    non_eur_pct = exposure['non_eur_pct'] or 0.0
    invested = sum(by_class.values())

    results = {}
    for name, scenario in SCENARIOS.items():
        loss = 0.0
        detail = scenario['label']
        if 'equity' in scenario:
            loss = by_class.get('equity', 0.0) * abs(scenario['equity'])
        elif 'crypto' in scenario:
            loss = by_class.get('crypto', 0.0) * abs(scenario['crypto'])
        elif 'rate_pp' in scenario:
            duration = _portfolio_duration(working)
            loss = by_class.get('bond', 0.0) * duration * scenario['rate_pp'] / 100
            detail += f' at duration {duration:g}y'
        elif 'fx' in scenario:
            loss = invested * non_eur_pct / 100 * abs(scenario['fx'])
            detail += f' on {non_eur_pct:g} % unhedged'
        months_covered = None
        if 'months_without_income' in scenario:
            months = scenario['months_without_income']
            drain = (essential or 0.0) * months
            months_covered = round(liquid / essential, 1) if essential else None
            results[name] = {
                'label': detail, 'loss_eur': None,
                'liquid_after_eur': round(liquid - drain, 2),
                'emergency_months_covered': months_covered,
                'survives': liquid >= drain if essential else None,
                'note': None if essential else 'monthly_essential_expenses is unset, so this '
                                               'scenario cannot be evaluated',
            }
            continue
        results[name] = {
            'label': detail,
            'loss_eur': round(loss, 2),
            'invested_after_eur': round(invested - loss, 2),
            'loss_pct_of_invested': round(loss / invested * 100, 2) if invested else None,
            'emergency_months_covered': round(liquid / essential, 1) if essential else None,
        }
    worst = max((r for r in results.values() if r.get('loss_eur') is not None),
                key=lambda r: r['loss_eur'], default=None)
    return {
        'scenarios': results,
        'worst_single_loss_eur': worst['loss_eur'] if worst else None,
        'invested_eur': round(invested, 2),
        'applied_to': 'post-action balance sheet' if action else 'current balance sheet',
        'note': 'magnitudes are calibrated to history, not predicted; the test is whether the plan '
                'survives them, not whether they happen',
    }


def _portfolio_duration(balance: dict) -> float:
    """Value-weighted duration across bond holdings; the default is used where one is missing."""
    weighted, total = 0.0, 0.0
    for holding in balance.get('holdings') or []:
        if holding.get('asset_class') != 'bond':
            continue
        value = _value(holding) or 0.0
        duration = _value(holding, 'duration_years')
        weighted += value * (duration if duration is not None else DEFAULT_BOND_DURATION_YEARS)
        total += value
    return round(weighted / total, 2) if total else DEFAULT_BOND_DURATION_YEARS


def apply_action(balance: dict, action: dict) -> dict:
    """Return a copy of the balance sheet as it would look after one action.

    Supported: {'kind': 'buy'|'sell', 'asset_class': ..., 'amount_eur': ..., 'from': 'cash'|'debt'}
    and {'kind': 'repay_debt', 'amount_eur': ...}. Anything else is refused rather than approximated:
    a stress run on a balance sheet nobody can reproduce is worse than no stress run.
    """
    kind = action.get('kind')
    amount = action.get('amount_eur')
    if not isinstance(amount, (int, float)) or isinstance(amount, bool) or amount <= 0:
        raise StateError('action.amount_eur must be a positive number')
    working = json.loads(json.dumps(balance))          # a deep copy; nothing mutates the instance
    assets = working.get('assets') or []
    cash_rows = [a for a in assets if a.get('type') in CASH_LIKE and _value(a) is not None]
    if not cash_rows and kind in ('buy', 'repay_debt'):
        raise StateError('no cash or deposit row to fund this action')

    def take_cash(value: float) -> None:
        remaining = value
        for row in sorted(cash_rows, key=lambda r: r.get('liquidity_tier') or 'L9'):
            available = _value(row) or 0.0
            used = min(available, remaining)
            row['value_eur'] = round(available - used, 2)
            remaining -= used
            if remaining <= 0:
                break
        if remaining > 0.01:
            raise StateError(f'the action needs {value:.2f} EUR but only '
                             f'{value - remaining:.2f} EUR of cash is recorded')

    if kind == 'buy':
        if action.get('from') == 'debt':
            working.setdefault('liabilities', []).append({
                'type': 'personal_loan', 'lender': action.get('lender') or 'unspecified',
                'balance_eur': amount, 'rate_pct': action.get('rate_pct'),
                'remaining_term_months': action.get('term_months'),
                'monthly_payment_eur': action.get('monthly_payment_eur'),
                'prepayment_penalty': None, 'tax_deductible': None,
                'as_of': working.get('as_of')})
        else:
            take_cash(amount)
        working.setdefault('holdings', []).append({
            'isin_or_symbol': action.get('instrument') or 'PROPOSED',
            'name': action.get('instrument') or 'proposed position',
            'asset_class': action.get('asset_class') or 'equity',
            'units': None, 'avg_cost_eur': None, 'value_eur': amount,
            'account': action.get('account'), 'issuer': action.get('issuer'),
            'sector': action.get('sector'), 'domicile': action.get('domicile'),
            'currency': action.get('currency') or 'EUR', 'ter_pct': action.get('ter_pct'),
            'distribution_policy': action.get('distribution_policy'),
            'as_of': working.get('as_of')})
    elif kind == 'sell':
        target_class = action.get('asset_class')
        remaining = amount
        for holding in working.get('holdings') or []:
            if target_class and holding.get('asset_class') != target_class:
                continue
            value = _value(holding) or 0.0
            used = min(value, remaining)
            holding['value_eur'] = round(value - used, 2)
            remaining -= used
            if remaining <= 0:
                break
        if remaining > 0.01:
            raise StateError(f'not enough {target_class or "holdings"} to sell {amount:.2f} EUR')
        if cash_rows:
            cash_rows[0]['value_eur'] = round((_value(cash_rows[0]) or 0.0) + amount, 2)
    elif kind == 'repay_debt':
        take_cash(amount)
        remaining = amount
        for liability in working.get('liabilities') or []:
            balance_eur = _value(liability, 'balance_eur') or 0.0
            used = min(balance_eur, remaining)
            liability['balance_eur'] = round(balance_eur - used, 2)
            remaining -= used
            if remaining <= 0:
                break
    else:
        raise StateError(f'unsupported action kind {kind!r}: expected buy, sell or repay_debt')
    return working


def main(argv=None) -> int:
    safe_console()
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--root', type=Path, default=Path.cwd())
    parser.add_argument('--json', action='store_true')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('allocation')
    sub.add_parser('concentration')
    sub.add_parser('cost-drag')
    for name in ('base-rate', 'forecast-check'):
        historical = sub.add_parser(name)
        historical.add_argument('--symbol', required=True)
        historical.add_argument('--horizon', required=True)
        historical.add_argument('--as-of', required=True)
        historical.add_argument('--provider')
    drifter = sub.add_parser('drift')
    drifter.add_argument('--targets', type=Path, required=True)
    rebalancer = sub.add_parser('rebalance')
    rebalancer.add_argument('--targets', type=Path, required=True)
    rebalancer.add_argument('--band', type=float, default=DEFAULT_ABSOLUTE_BAND_PP)
    stresser = sub.add_parser('stress')
    stresser.add_argument('--action', type=Path, default=None)
    lots = sub.add_parser('lots')
    lots.add_argument('--symbol', required=True)
    lots.add_argument('--units', type=float, required=True)

    args = parser.parse_args(argv)
    try:
        if args.command in ('base-rate', 'forecast-check'):
            from forecast_history import base_rate, benchmark
            method = base_rate if args.command == 'base-rate' else benchmark
            result = method(args.root, args.symbol, args.horizon, args.as_of, args.provider)
            print(json.dumps(result, indent=2, allow_nan=False))
            return 0
        balance = load_balance(args.root)
        if args.command == 'allocation':
            result = allocation(balance) | currency_exposure(balance)
        elif args.command == 'concentration':
            result = concentration(balance)
        elif args.command == 'cost-drag':
            result = cost_drag(balance)
        elif args.command == 'drift':
            result = drift(balance, read_json(args.targets, 'targets'))
        elif args.command == 'rebalance':
            result = rebalance(balance, read_json(args.targets, 'targets'), args.band)
        elif args.command == 'stress':
            result = stress(balance, read_json(args.action, 'action') if args.action else None)
        elif args.command == 'lots':
            result = average_cost_lots(balance, args.symbol, args.units)
        else:
            return 4
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except StateError as exc:
        print(f'portfolio_math: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
