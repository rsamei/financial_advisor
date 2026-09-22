#!/usr/bin/env python3
"""Validate the financial profile and compute every number that must never be typed by hand.

Usage (from the repository root):
    python tools/profile_check.py check  [--root .] [--today YYYY-MM-DD]
    python tools/profile_check.py derive [--root .] [--today YYYY-MM-DD] [--json] [--write]

`check` validates `profile/profile.json`, `profile/balance_sheet.json` and
`profile/risk_profile.json` against the schemas in `.claude/skills/financial-advisor/01`, `02` and
`03`, and reports every problem it finds rather than the first.

`derive` computes the balance-sheet derived numbers, the risk bands and the hard limits, and with
`--write` stores them into `profile/risk_profile.json` and `profile/derived.json`.

What this tool enforces that prose cannot:

- **A populated number without a source-ledger row is an error.** Every material figure's JSON path
  must be covered by some ledger row's `covers` patterns. "Every fact has a source" is otherwise a
  promise nothing checks, and provenance is the first thing that rots when a number is updated in
  a hurry.
- **Derived numbers are computed, never accepted.** If the instance already carries a computed
  field with a different value, that is reported as a conflict instead of being trusted.
- **Effective band = min(tolerance, capacity).** A user cannot talk their way past their own
  balance sheet, and an unset input scores zero: missing data lowers capacity, never raises it.
- **Staleness is mechanical**: any `as_of` more than 90 days before the run date is listed, and
  Gate 1 FLAGs on it later.
- **`null` is not zero.** An unset figure is excluded from a sum and named in `unset_fields`; it is
  never silently treated as nothing.

Stdlib only. Exit 0 ok; 1 state or schema error; 4 bad arguments.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fa_state import StateError, read_json, safe_console, write_json  # noqa: E402

STALE_DAYS = 90

AGE_BANDS = ('18-24', '25-34', '35-44', '45-54', '55-64', '65+')
MARITAL = ('single', 'married', 'civil_union', 'separated', 'divorced', 'widowed')
INCOME_STABILITY = ('stable', 'variable', 'precarious')
CONTRACT_TYPES = ('permanent', 'fixed_term', 'self_employed', 'contractor', 'retired',
                  'not_working')
TRAJECTORIES = ('rising', 'flat', 'declining', 'uncertain')
FLEXIBILITY = ('hard', 'soft')
CERTAINTY = ('contracted', 'likely', 'possible')
UNCERTAINTY = ('low', 'medium', 'high')

ASSET_TYPES = ('cash', 'deposit', 'brokerage', 'pension', 'crypto', 'real_estate', 'other')
LIQUIDITY_TIERS = ('L0', 'L1', 'L2', 'L3')
CUSTODY_REGIMES = ('amministrato', 'dichiarativo', 'not_applicable')
ASSET_CLASSES = ('equity', 'bond', 'cash', 'crypto', 'commodity', 'real_estate', 'multi_asset')
DISTRIBUTION = ('accumulating', 'distributing')
LIABILITY_TYPES = ('mortgage', 'personal_loan', 'car_loan', 'student_loan', 'credit_card',
                   'family_loan', 'other')
TFR_DESTINATIONS = ('company', 'fondo_pensione', 'mixed', 'not_applicable')
INSURANCE_STATES = ('present', 'absent', 'unknown')
INSURANCE_KINDS = ('health', 'life', 'disability', 'home', 'liability')
# Asset rows that are part of the invested picture rather than a home or a car.
CASH_LIKE = ('cash', 'deposit')

BANDS = ('low', 'moderate', 'high')

# Limits by effective band (03). Single-issuer and single-sector do not widen with the band.
LIMITS: dict[str, dict[str, float]] = {
    'low': {'max_equity_pct': 30, 'max_crypto_pct': 0, 'max_single_issuer_pct': 10,
            'max_single_sector_pct': 25, 'max_non_eur_unhedged_pct': 40,
            'drawdown_to_survive_pct': 15},
    'moderate': {'max_equity_pct': 60, 'max_crypto_pct': 5, 'max_single_issuer_pct': 10,
                 'max_single_sector_pct': 25, 'max_non_eur_unhedged_pct': 60,
                 'drawdown_to_survive_pct': 30},
    'high': {'max_equity_pct': 85, 'max_crypto_pct': 10, 'max_single_issuer_pct': 10,
             'max_single_sector_pct': 25, 'max_non_eur_unhedged_pct': 60,
             'drawdown_to_survive_pct': 45},
}

NET_WORTH_BANDS = (('B1', 25_000), ('B2', 100_000), ('B3', 250_000), ('B4', 500_000),
                   ('B5', 1_000_000), ('B6', float('inf')))

QUESTIONS = tuple(f'Q{n}' for n in range(1, 11))
SKIPPABLE_QUESTIONS = ('Q2',)


# --- small helpers ----------------------------------------------------------------------------

def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _date(value, path: str, problems: list[str]) -> dt.date | None:
    if value is None:
        return None
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError:
        problems.append(f'{path}: not an ISO date: {value!r}')
        return None


def _enum(value, allowed: tuple, path: str, problems: list[str], *, optional: bool = True) -> None:
    if value is None:
        if not optional:
            problems.append(f'{path}: required, and unset')
        return
    if value not in allowed:
        problems.append(f'{path}: {value!r} is not one of {", ".join(map(str, allowed))}')


def _amount(value, path: str, problems: list[str]) -> float | None:
    """A money field is a bare number or null. A string amount is an error, not a parse job."""
    if value is None:
        return None
    if not _is_number(value):
        problems.append(f'{path}: money fields are bare numbers in EUR, got {value!r}')
        return None
    return float(value)


def covers(path: str, pattern: str) -> bool:
    """Does a ledger row's `covers` pattern name this JSON path?

    `*` is the only metacharacter and matches any run of characters. Everything else is literal -
    `fnmatch` cannot be used here because it reads `assets[*]` as a character class matching one
    literal asterisk, which silently matches nothing and would make every provenance check pass.
    """
    return re.fullmatch('.*'.join(re.escape(part) for part in pattern.split('*')), path) is not None


def _share(part: float, whole: float) -> float | None:
    return round(part / whole * 100, 2) if whole else None


def _round(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


# --- validation -------------------------------------------------------------------------------

class Profile:
    """The three instance files, loaded together because no one of them validates alone."""

    def __init__(self, root: Path, today: dt.date):
        self.root = Path(root)
        self.today = today
        folder = self.root / 'profile'
        self.profile = read_json(folder / 'profile.json', 'profile.json')
        self.balance = read_json(folder / 'balance_sheet.json', 'balance_sheet.json')
        self.risk = read_json(folder / 'risk_profile.json', 'risk_profile.json')
        for name, value in (('profile.json', self.profile), ('balance_sheet.json', self.balance),
                            ('risk_profile.json', self.risk)):
            if not isinstance(value, dict):
                raise StateError(f'{name}: top-level JSON value must be an object')

    # -- identity, career, goals --
    def validate_profile(self, problems: list[str]) -> None:
        identity = self.profile.get('identity') or {}
        _enum(identity.get('age_band'), AGE_BANDS, 'identity.age_band', problems)
        _enum(identity.get('marital_status'), MARITAL, 'identity.marital_status', problems)
        residency = identity.get('tax_residency')
        if residency is not None and not (isinstance(residency, str) and len(residency) == 2
                                          and residency.isalpha() and residency.isupper()):
            problems.append(f'identity.tax_residency: expected ISO-3166 alpha-2, got {residency!r}')
        dependants = identity.get('dependants')
        if dependants is not None and not (isinstance(dependants, int) and dependants >= 0):
            problems.append(f'identity.dependants: expected a non-negative integer, got {dependants!r}')

        career = self.profile.get('career') or {}
        _enum(career.get('income_stability'), INCOME_STABILITY, 'career.income_stability', problems)
        _enum(career.get('contract_type'), CONTRACT_TYPES, 'career.contract_type', problems)
        _enum(career.get('career_trajectory'), TRAJECTORIES, 'career.career_trajectory', problems)

        seen: set[str] = set()
        for index, goal in enumerate(self.profile.get('goals') or []):
            path = f'goals[{index}]'
            goal_id = goal.get('goal_id')
            if not goal_id:
                problems.append(f'{path}.goal_id: required')
            elif goal_id in seen:
                problems.append(f'{path}.goal_id: duplicate {goal_id!r}')
            else:
                seen.add(goal_id)
            _enum(goal.get('flexibility'), FLEXIBILITY, f'{path}.flexibility', problems)
            _amount(goal.get('target_amount_eur'), f'{path}.target_amount_eur', problems)
            _date(goal.get('target_date'), f'{path}.target_date', problems)

        for index, event in enumerate(self.profile.get('liquidity_events') or []):
            _enum(event.get('certainty'), CERTAINTY, f'liquidity_events[{index}].certainty', problems)
            _amount(event.get('amount_eur'), f'liquidity_events[{index}].amount_eur', problems)

    # -- balance sheet --
    def validate_balance(self, problems: list[str]) -> None:
        flow = self.balance.get('cash_flow') or {}
        for field in ('monthly_net_income', 'monthly_essential_expenses', 'monthly_discretionary'):
            value = _amount(flow.get(field), f'cash_flow.{field}', problems)
            if value is not None and value < 0:
                problems.append(f'cash_flow.{field}: negative ({value})')
        _enum(flow.get('income_stability'), INCOME_STABILITY, 'cash_flow.income_stability', problems)
        target = flow.get('emergency_fund_target_months')
        if target is not None and not (_is_number(target) and target >= 0):
            problems.append(f'cash_flow.emergency_fund_target_months: {target!r}')
        _date(flow.get('as_of'), 'cash_flow.as_of', problems)

        for index, asset in enumerate(self.balance.get('assets') or []):
            path = f'assets[{index}]'
            _enum(asset.get('type'), ASSET_TYPES, f'{path}.type', problems, optional=False)
            _enum(asset.get('liquidity_tier'), LIQUIDITY_TIERS, f'{path}.liquidity_tier', problems,
                  optional=False)
            _enum(asset.get('custody_regime'), CUSTODY_REGIMES, f'{path}.custody_regime', problems)
            _amount(asset.get('value_eur'), f'{path}.value_eur', problems)
            _date(asset.get('as_of'), f'{path}.as_of', problems)

        for index, holding in enumerate(self.balance.get('holdings') or []):
            path = f'holdings[{index}]'
            _enum(holding.get('asset_class'), ASSET_CLASSES, f'{path}.asset_class', problems,
                  optional=False)
            _enum(holding.get('distribution_policy'), DISTRIBUTION, f'{path}.distribution_policy',
                  problems)
            _amount(holding.get('value_eur'), f'{path}.value_eur', problems)
            _amount(holding.get('avg_cost_eur'), f'{path}.avg_cost_eur', problems)
            _date(holding.get('as_of'), f'{path}.as_of', problems)

        for index, liability in enumerate(self.balance.get('liabilities') or []):
            path = f'liabilities[{index}]'
            _enum(liability.get('type'), LIABILITY_TYPES, f'{path}.type', problems, optional=False)
            for field in ('balance_eur', 'monthly_payment_eur'):
                _amount(liability.get(field), f'{path}.{field}', problems)
            rate = liability.get('rate_pct')
            if rate is not None and not _is_number(rate):
                problems.append(f'{path}.rate_pct: expected a number, got {rate!r}')
            _date(liability.get('as_of'), f'{path}.as_of', problems)

        pension = self.balance.get('pension') or {}
        _enum(pension.get('tfr_destination'), TFR_DESTINATIONS, 'pension.tfr_destination', problems)
        insurance = self.balance.get('insurance') or {}
        for kind in INSURANCE_KINDS:
            _enum(insurance.get(kind, 'unknown'), INSURANCE_STATES, f'insurance.{kind}', problems)

    # -- risk profile --
    def validate_risk(self, problems: list[str]) -> None:
        answers = self.risk.get('questionnaire') or {}
        unknown = set(answers) - set(QUESTIONS)
        if unknown:
            problems.append(f'questionnaire: unknown item(s) {sorted(unknown)}')
        for question in QUESTIONS:
            value = answers.get(question)
            if value is None:
                if question not in SKIPPABLE_QUESTIONS and question in answers:
                    problems.append(f'questionnaire.{question}: null is only allowed for '
                                    f'{", ".join(SKIPPABLE_QUESTIONS)}; leave the item out if '
                                    'it has not been asked yet')
                continue
            if not (isinstance(value, int) and 1 <= value <= 5):
                problems.append(f'questionnaire.{question}: expected an integer 1-5, got {value!r}')
        for index, override in enumerate(self.risk.get('limit_overrides') or []):
            path = f'limit_overrides[{index}]'
            if override.get('limit') not in LIMITS['moderate']:
                problems.append(f'{path}.limit: unknown limit {override.get("limit")!r}')
            _enum(override.get('direction'), ('tighten', 'loosen'), f'{path}.direction', problems,
                  optional=False)
            if not override.get('reason'):
                problems.append(f'{path}.reason: required - a limit change without a reason is '
                                'not a decision, it is a drift')
            _date(override.get('recorded_on'), f'{path}.recorded_on', problems)

    # -- provenance --
    def material_paths(self) -> list[str]:
        """Every populated figure that a gate could rest on."""
        paths: list[str] = []
        flow = self.balance.get('cash_flow') or {}
        for field in ('monthly_net_income', 'monthly_essential_expenses', 'monthly_discretionary'):
            if flow.get(field) is not None:
                paths.append(f'cash_flow.{field}')
        for index, asset in enumerate(self.balance.get('assets') or []):
            if asset.get('value_eur') is not None:
                paths.append(f'assets[{index}].value_eur')
        for index, holding in enumerate(self.balance.get('holdings') or []):
            for field in ('value_eur', 'units'):
                if holding.get(field) is not None:
                    paths.append(f'holdings[{index}].{field}')
        for index, liability in enumerate(self.balance.get('liabilities') or []):
            for field in ('balance_eur', 'rate_pct', 'monthly_payment_eur'):
                if liability.get(field) is not None:
                    paths.append(f'liabilities[{index}].{field}')
        pension = self.balance.get('pension') or {}
        for field in ('public_pension_estimate_eur_year', 'fondo_pensione_balance_eur',
                      'fondo_pensione_annual_contribution_eur', 'employer_match_pct'):
            if pension.get(field) is not None:
                paths.append(f'pension.{field}')
        return paths

    def validate_ledger(self, problems: list[str]) -> None:
        rows = self.balance.get('source_ledger') or []
        patterns: list[str] = []
        for index, row in enumerate(rows):
            path = f'source_ledger[{index}]'
            if not row.get('claim'):
                problems.append(f'{path}.claim: required')
            if not row.get('source'):
                problems.append(f'{path}.source: required - a document path or "user statement"')
            _date(row.get('confirmed_on'), f'{path}.confirmed_on', problems)
            _enum(row.get('uncertainty'), UNCERTAINTY, f'{path}.uncertainty', problems)
            row_covers = row.get('covers') or []
            if not isinstance(row_covers, list) or not all(isinstance(c, str) for c in row_covers):
                problems.append(f'{path}.covers: expected a list of JSON-path patterns')
                continue
            patterns.extend(row_covers)
        for target in self.material_paths():
            if not any(covers(target, pattern) for pattern in patterns):
                problems.append(
                    f'{target}: populated, but no source_ledger row covers it. Add a row whose '
                    '"covers" names this path (wildcards allowed, e.g. "assets[*].value_eur"). '
                    'A number nobody can trace is a number no gate should rest on.')

    def stale_fields(self) -> list[dict]:
        cutoff = self.today - dt.timedelta(days=STALE_DAYS)
        stale: list[dict] = []

        def check(path: str, raw) -> None:
            when = _date(raw, path, [])
            if when and when < cutoff:
                stale.append({'path': path, 'as_of': when.isoformat(),
                              'days': (self.today - when).days})

        check('cash_flow.as_of', (self.balance.get('cash_flow') or {}).get('as_of'))
        for index, asset in enumerate(self.balance.get('assets') or []):
            check(f'assets[{index}].as_of', asset.get('as_of'))
        for index, holding in enumerate(self.balance.get('holdings') or []):
            check(f'holdings[{index}].as_of', holding.get('as_of'))
        for index, liability in enumerate(self.balance.get('liabilities') or []):
            check(f'liabilities[{index}].as_of', liability.get('as_of'))
        check('pension.as_of', (self.balance.get('pension') or {}).get('as_of'))
        return stale

    def validate(self) -> list[str]:
        problems: list[str] = []
        self.validate_profile(problems)
        self.validate_balance(problems)
        self.validate_risk(problems)
        self.validate_ledger(problems)
        return problems


# --- derivation -------------------------------------------------------------------------------

def net_worth_band(net_worth: float | None) -> str | None:
    if net_worth is None:
        return None
    for label, ceiling in NET_WORTH_BANDS:
        if net_worth < ceiling:
            return label
    return 'B6'


def balance_sheet_numbers(profile: Profile) -> dict:
    balance, unset = profile.balance, []
    flow = balance.get('cash_flow') or {}
    assets = balance.get('assets') or []
    holdings = balance.get('holdings') or []
    liabilities = balance.get('liabilities') or []

    def figure(container, field, path):
        value = container.get(field)
        if value is None:
            unset.append(path)
        return value

    income = figure(flow, 'monthly_net_income', 'cash_flow.monthly_net_income')
    essential = figure(flow, 'monthly_essential_expenses', 'cash_flow.monthly_essential_expenses')
    discretionary = figure(flow, 'monthly_discretionary', 'cash_flow.monthly_discretionary')
    target_months = flow.get('emergency_fund_target_months')

    asset_total = sum(a['value_eur'] for a in assets if a.get('value_eur') is not None)
    liability_total = sum(l['balance_eur'] for l in liabilities if l.get('balance_eur') is not None)
    liquid = sum(a['value_eur'] for a in assets
                 if a.get('value_eur') is not None and a.get('liquidity_tier') in ('L0', 'L1'))
    payments = sum(l['monthly_payment_eur'] for l in liabilities
                   if l.get('monthly_payment_eur') is not None)

    net_worth = asset_total - liability_total
    emergency_months = (liquid / essential) if essential else None
    savings_capacity = (income - essential - discretionary
                        if None not in (income, essential, discretionary) else None)

    cash_like = sum(a['value_eur'] for a in assets
                    if a.get('value_eur') is not None and a.get('type') in CASH_LIKE)
    holding_value = sum(h['value_eur'] for h in holdings if h.get('value_eur') is not None)
    invested = cash_like + holding_value

    by_class: dict[str, float] = {'cash': cash_like} if cash_like else {}
    by_currency: dict[str, float] = {}
    by_issuer: dict[str, float] = {}
    by_sector: dict[str, float] = {}
    for holding in holdings:
        value = holding.get('value_eur')
        if value is None:
            continue
        by_class[holding.get('asset_class') or 'unknown'] = (
            by_class.get(holding.get('asset_class') or 'unknown', 0) + value)
        by_currency[holding.get('currency') or 'EUR'] = (
            by_currency.get(holding.get('currency') or 'EUR', 0) + value)
        if holding.get('issuer'):
            by_issuer[holding['issuer']] = by_issuer.get(holding['issuer'], 0) + value
        if holding.get('sector'):
            by_sector[holding['sector']] = by_sector.get(holding['sector'], 0) + value
    if cash_like:
        by_currency['EUR'] = by_currency.get('EUR', 0) + cash_like

    return {
        'net_worth_eur': _round(net_worth),
        'net_worth_band': net_worth_band(net_worth),
        'net_worth_is_negative': net_worth < 0,
        'total_assets_eur': _round(asset_total),
        'total_liabilities_eur': _round(liability_total),
        'liquid_assets_eur': _round(liquid),
        'invested_assets_eur': _round(invested),
        'emergency_months_covered': _round(emergency_months, 1),
        'emergency_fund_target_months': target_months,
        'emergency_fund_gap_months': (
            _round(max(0.0, target_months - emergency_months), 1)
            if target_months is not None and emergency_months is not None else None),
        'monthly_savings_capacity_eur': _round(savings_capacity),
        'savings_rate_pct': _share(savings_capacity, income) if savings_capacity is not None and income else None,
        'debt_service_ratio_pct': _share(payments, income) if income else None,
        'debt_to_income_pct': _share(liability_total, income * 12) if income else None,
        'allocation_pct': {k: _share(v, invested) for k, v in sorted(by_class.items())} if invested else {},
        'currency_exposure_pct': {k: _share(v, invested) for k, v in sorted(by_currency.items())} if invested else {},
        'largest_issuer_pct': _share(max(by_issuer.values()), invested) if by_issuer and invested else None,
        'largest_sector_pct': _share(max(by_sector.values()), invested) if by_sector and invested else None,
        'crypto_share_pct': _share(by_class.get('crypto', 0), invested) if invested else None,
        'unset_fields': unset,
        'stale_fields': profile.stale_fields(),
    }


def tolerance(profile: Profile) -> dict:
    """Sum the ten items; rescale over nine when the skippable history item is absent."""
    answers = profile.risk.get('questionnaire') or {}
    scored = {q: answers[q] for q in QUESTIONS if isinstance(answers.get(q), int)}
    missing = [q for q in QUESTIONS if q not in scored]
    if [q for q in missing if q not in SKIPPABLE_QUESTIONS]:
        return {'tolerance_score': None, 'tolerance_band': None, 'tolerance_missing': missing}
    total = sum(scored.values())
    if missing:
        total = round(total * len(QUESTIONS) / len(scored))
    band = 'low' if total <= 24 else ('moderate' if total <= 37 else 'high')
    return {'tolerance_score': total, 'tolerance_band': band, 'tolerance_missing': missing}


def capacity(profile: Profile, numbers: dict) -> dict:
    """Five factors, each 0/1/2. An unset input scores 0 and is named."""
    identity = profile.profile.get('identity') or {}
    career = profile.profile.get('career') or {}
    unknown: list[str] = []

    def score(name, value, thresholds) -> int:
        """thresholds: (low_max, mid_max) - above mid_max scores 2."""
        if value is None:
            unknown.append(name)
            return 0
        low_max, mid_max = thresholds
        return 0 if value < low_max else (1 if value <= mid_max else 2)

    horizon = nearest_hard_goal_years(profile)
    factors = {'horizon_years': score('horizon_years', horizon, (3, 7))}

    stability = career.get('income_stability') or (profile.balance.get('cash_flow') or {}).get('income_stability')
    if stability is None:
        unknown.append('income_stability')
        factors['income_stability'] = 0
    else:
        factors['income_stability'] = {'precarious': 0, 'variable': 1, 'stable': 2}[stability]

    factors['emergency_months'] = score('emergency_months',
                                        numbers.get('emergency_months_covered'), (3, 6))

    dti = numbers.get('debt_to_income_pct')
    if dti is None:
        unknown.append('debt_to_income')
        factors['debt_to_income'] = 0
    else:
        factors['debt_to_income'] = 0 if dti > 40 else (1 if dti >= 20 else 2)

    dependants = identity.get('dependants')
    if dependants is None:
        unknown.append('dependants')
        factors['dependants'] = 0
    else:
        factors['dependants'] = 2 if dependants == 0 else (1 if dependants == 1 else 0)

    total = sum(factors.values())
    band = 'low' if total <= 3 else ('moderate' if total <= 7 else 'high')
    vetoes = [name for name in ('emergency_months', 'income_stability') if factors[name] == 0]
    if vetoes:
        # No cash buffer, or no reliable income, means no risk capacity - whatever the total. A
        # points sum is exactly the arithmetic that would otherwise average this away.
        band = 'low'
    return {'capacity_band': band, 'capacity_score': total, 'capacity_factors': factors,
            'capacity_unknown_factors': unknown, 'capacity_vetoes': vetoes}


def nearest_hard_goal_years(profile: Profile) -> float | None:
    dates = []
    for goal in profile.profile.get('goals') or []:
        if goal.get('flexibility') != 'hard' or not goal.get('target_date'):
            continue
        when = _date(goal['target_date'], 'goal', [])
        if when:
            dates.append(when)
    if not dates:
        return None
    return round((min(dates) - profile.today).days / 365.25, 2)


def effective_limits(band: str, overrides: list[dict]) -> tuple[dict, list[str]]:
    """Apply the user's overrides. A tightening applies; a loosening is reported, not silent."""
    limits = dict(LIMITS[band])
    notes: list[str] = []
    for override in overrides or []:
        name, value = override.get('limit'), override.get('value')
        if name not in limits or not _is_number(value):
            continue
        tighter = value < limits[name]
        if override.get('direction') == 'tighten' and tighter:
            limits[name] = value
            notes.append(f'{name} tightened to {value} ({override.get("reason")})')
        elif override.get('direction') == 'tighten':
            notes.append(f'{name}: override claims to tighten but {value} is not stricter than '
                         f'{limits[name]}; ignored')
        else:
            limits[name] = value
            notes.append(f'{name} LOOSENED to {value} ({override.get("reason")}) - this requires a '
                         'dated deviation in ips/DEVIATIONS.md once an IPS is frozen')
    return limits, notes


def derive(profile: Profile) -> dict:
    numbers = balance_sheet_numbers(profile)
    tol = tolerance(profile)
    cap = capacity(profile, numbers)
    band = None
    if tol['tolerance_band'] and cap['capacity_band']:
        band = min(tol['tolerance_band'], cap['capacity_band'], key=BANDS.index)
    limits, notes = effective_limits(band, profile.risk.get('limit_overrides') or []) if band else ({}, [])
    return {
        'computed_on': profile.today.isoformat(),
        'balance_sheet': numbers,
        'nearest_hard_goal_years': nearest_hard_goal_years(profile),
        **tol, **cap,
        'effective_band': band,
        'limits': limits,
        'limit_notes': notes,
    }


def conflicts(profile: Profile, derived: dict) -> list[str]:
    """A computed field typed in by hand with a different value is a conflict, not a fact."""
    found = []
    for field in ('tolerance_score', 'tolerance_band', 'capacity_band', 'effective_band'):
        stored = profile.risk.get(field)
        if stored not in (None, '') and stored != derived.get(field):
            found.append(f'risk_profile.json {field}={stored!r} but the rule computes '
                         f'{derived.get(field)!r}. Derived fields are computed, never typed.')
    return found


# --- CLI --------------------------------------------------------------------------------------

def _report(title: str, items: list[str]) -> None:
    print(f'{title}: {len(items)}')
    for item in items:
        print(f'  - {item}')


def main(argv=None) -> int:
    safe_console()
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--root', type=Path, default=Path.cwd())
    parser.add_argument('--today', default=None, help='override the run date (tests, replay)')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('check')
    derive_parser = sub.add_parser('derive')
    derive_parser.add_argument('--json', action='store_true')
    derive_parser.add_argument('--write', action='store_true',
                               help='store the computed fields into profile/')
    args = parser.parse_args(argv)

    try:
        today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()
    except ValueError:
        print(f'profile_check: --today is not an ISO date: {args.today!r}', file=sys.stderr)
        return 4

    try:
        profile = Profile(args.root, today)
        problems = profile.validate()
        if args.command == 'check':
            stale = profile.stale_fields()
            if problems:
                _report('profile_check: problems', problems)
                return 1
            print('profile_check: OK (schemas, vocabularies, source-ledger coverage)')
            if stale:
                _report(f'stale (older than {STALE_DAYS} days; Gate 1 FLAGs on these)',
                        [f'{s["path"]} as of {s["as_of"]} ({s["days"]} days)' for s in stale])
            return 0

        derived = derive(profile)
        problems += conflicts(profile, derived)
        if problems:
            _report('profile_check: problems', problems)
            return 1
        if args.write:
            profile.risk.update({k: derived[k] for k in
                                 ('tolerance_score', 'tolerance_band', 'capacity_band',
                                  'capacity_factors', 'capacity_unknown_factors',
                                  'effective_band', 'limits')})
            write_json(profile.root / 'profile' / 'risk_profile.json', profile.risk)
            write_json(profile.root / 'profile' / 'derived.json', derived)
        if args.json:
            print(json.dumps(derived, indent=2, ensure_ascii=False))
        else:
            numbers = derived['balance_sheet']
            print(f"net worth: {numbers['net_worth_eur']} EUR (band {numbers['net_worth_band']})")
            print(f"emergency months covered: {numbers['emergency_months_covered']} "
                  f"(target {numbers['emergency_fund_target_months']})")
            print(f"savings rate: {numbers['savings_rate_pct']} % | "
                  f"debt service: {numbers['debt_service_ratio_pct']} % | "
                  f"DTI: {numbers['debt_to_income_pct']} %")
            print(f"tolerance {derived['tolerance_band']} ({derived['tolerance_score']}) | "
                  f"capacity {derived['capacity_band']} -> effective {derived['effective_band']}")
            if derived['capacity_vetoes']:
                print(f"capacity capped at low by: {', '.join(derived['capacity_vetoes'])}")
            if derived['capacity_unknown_factors']:
                print('unknown capacity inputs (scored 0): '
                      + ', '.join(derived['capacity_unknown_factors']))
            for note in derived['limit_notes']:
                print(f'limit note: {note}')
            if numbers['stale_fields']:
                _report(f'stale (older than {STALE_DAYS} days)',
                        [f'{s["path"]} as of {s["as_of"]}' for s in numbers['stale_fields']])
        return 0
    except StateError as exc:
        print(f'profile_check: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
