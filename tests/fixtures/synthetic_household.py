"""A synthetic household. Invented for tests; it is nobody's balance sheet.

Hand-computed expectations live next to the assertions in test_profile_check.py, not here, so a
change to the fixture cannot quietly change what the tests claim the arithmetic should produce.
"""

TODAY = '2026-09-20'


def profile(**overrides):
    return {
        'schema_version': 1,
        'populated_on': TODAY,
        'identity': {
            'name': 'Synthetic Household', 'age_band': '35-44', 'residency_country': 'IT',
            'tax_residency': 'IT', 'citizenships': ['IT'], 'marital_status': 'married',
            'dependants': 1, 'sanctions_or_pep_self_declaration': 'none',
        },
        'education': [{'level': 'master', 'field': 'economics', 'institution': 'Synthetic U',
                       'year': 2014}],
        'career': {
            'role': 'analyst', 'employer_sector': 'manufacturing', 'contract_type': 'permanent',
            'tenure_years': 5, 'income_stability': 'stable', 'career_trajectory': 'flat',
            'expected_income_change': 'none expected in 24 months',
        },
        'goals': [
            {'goal_id': 'G1', 'description': 'house deposit', 'target_amount_eur': 60000,
             'target_date': '2031-09-01', 'priority': 1, 'flexibility': 'hard'},
            {'goal_id': 'G2', 'description': 'retirement', 'target_amount_eur': None,
             'target_date': '2051-01-01', 'priority': 2, 'flexibility': 'soft'},
        ],
        'liquidity_events': [
            {'description': 'annual bonus', 'amount_eur': 4000, 'expected_on': '2027-03-01',
             'certainty': 'likely'}],
        'constraints': {'esg_exclusions': ['tobacco'], 'refused_instruments': ['leveraged ETFs'],
                        'brokers': ['synthetic broker'], 'monthly_management_minutes': 60},
        'source_ledger': [
            {'claim': 'identity, career, goals', 'source': 'user statement',
             'confirmed_on': TODAY, 'uncertainty': 'low', 'covers': ['identity.*', 'career.*']}],
    } | overrides


def balance_sheet(**overrides):
    """Figures chosen so every derived number has a clean hand-computed value.

    assets 12 000 + 3 000 + 40 000 + 30 000 = 85 000; liabilities 25 000 -> net worth 60 000 (B2)
    liquid (L0 + L1) = 12 000 + 3 000 = 15 000; essential 2 500 -> 6.0 months
    income 4 000, essential 2 500, discretionary 500 -> capacity 1 000, savings rate 25 %
    debt service 450 / 4 000 = 11.25 %; DTI 25 000 / 48 000 = 52.08 %
    invested = cash-like 15 000 + holdings 40 000 = 55 000
    """
    return {
        'schema_version': 1,
        'as_of': TODAY,
        'cash_flow': {
            'monthly_net_income': 4000, 'monthly_essential_expenses': 2500,
            'monthly_discretionary': 500, 'income_stability': 'stable',
            'emergency_fund_target_months': 6, 'as_of': TODAY,
        },
        'assets': [
            {'account': 'current', 'institution': 'Synthetic Bank', 'type': 'cash',
             'liquidity_tier': 'L0', 'value_eur': 12000, 'currency': 'EUR',
             'custody_regime': 'not_applicable', 'as_of': TODAY, 'notes': None},
            {'account': 'savings', 'institution': 'Synthetic Bank', 'type': 'deposit',
             'liquidity_tier': 'L1', 'value_eur': 3000, 'currency': 'EUR',
             'custody_regime': 'not_applicable', 'as_of': TODAY, 'notes': None},
            {'account': 'brokerage', 'institution': 'Synthetic Broker', 'type': 'brokerage',
             'liquidity_tier': 'L2', 'value_eur': 40000, 'currency': 'EUR',
             'custody_regime': 'amministrato', 'as_of': TODAY, 'notes': None},
            {'account': 'pension', 'institution': 'Synthetic Fondo', 'type': 'pension',
             'liquidity_tier': 'L3', 'value_eur': 30000, 'currency': 'EUR',
             'custody_regime': 'not_applicable', 'as_of': TODAY, 'notes': None},
        ],
        'holdings': [
            {'isin_or_symbol': 'SYN0000001', 'name': 'Synthetic World Equity', 'asset_class':
             'equity', 'units': 300, 'avg_cost_eur': 90, 'value_eur': 30000,
             'account': 'brokerage', 'issuer': 'Synthetic Issuer A', 'sector': 'diversified',
             'domicile': 'IE', 'currency': 'EUR', 'ter_pct': 0.2,
             'distribution_policy': 'accumulating', 'as_of': TODAY},
            {'isin_or_symbol': 'SYN0000002', 'name': 'Synthetic Euro Bond', 'asset_class': 'bond',
             'units': 100, 'avg_cost_eur': 95, 'value_eur': 10000, 'account': 'brokerage',
             'issuer': 'Synthetic Issuer B', 'sector': 'government', 'domicile': 'IE',
             'currency': 'EUR', 'ter_pct': 0.15, 'distribution_policy': 'accumulating',
             'as_of': TODAY},
        ],
        'liabilities': [
            {'type': 'mortgage', 'lender': 'Synthetic Bank', 'balance_eur': 25000,
             'rate_pct': 2.1, 'remaining_term_months': 72, 'monthly_payment_eur': 450,
             'prepayment_penalty': False, 'tax_deductible': True, 'as_of': TODAY},
        ],
        'pension': {
            'public_pension_estimate_eur_year': None, 'public_pension_source': None,
            'fondo_pensione_balance_eur': 30000, 'fondo_pensione_annual_contribution_eur': 2400,
            'employer_match_pct': 1.5, 'tfr_destination': 'fondo_pensione', 'as_of': TODAY,
        },
        'insurance': {'health': 'present', 'life': 'absent', 'disability': 'unknown',
                      'home': 'present', 'liability': 'unknown'},
        'source_ledger': [
            {'claim': 'cash flow from payslip and budget', 'source': 'documents/payslip.pdf',
             'confirmed_on': TODAY, 'uncertainty': 'low', 'covers': ['cash_flow.*']},
            {'claim': 'account balances', 'source': 'documents/statement.pdf',
             'confirmed_on': TODAY, 'uncertainty': 'low', 'covers': ['assets[*].value_eur']},
            {'claim': 'holdings', 'source': 'documents/broker_export.csv', 'confirmed_on': TODAY,
             'uncertainty': 'low', 'covers': ['holdings[*].*']},
            {'claim': 'mortgage terms', 'source': 'documents/mortgage.pdf', 'confirmed_on': TODAY,
             'uncertainty': 'low', 'covers': ['liabilities[*].*']},
            {'claim': 'pension', 'source': 'user statement', 'confirmed_on': TODAY,
             'uncertainty': 'medium', 'covers': ['pension.*']},
        ],
    } | overrides


def risk_profile(**overrides):
    """Answers sum to 33 -> moderate tolerance."""
    return {
        'schema_version': 1,
        'as_of': TODAY,
        'questionnaire': {'Q1': 3, 'Q2': 4, 'Q3': 3, 'Q4': 3, 'Q5': 3, 'Q6': 3, 'Q7': 4,
                          'Q8': 4, 'Q9': 3, 'Q10': 3},
        'tolerance_score': None, 'tolerance_band': None, 'capacity_band': None,
        'capacity_factors': {}, 'capacity_unknown_factors': [], 'effective_band': None,
        'limits': {}, 'limit_overrides': [], 'behavioural_notes': [],
        'source_ledger': [],
    } | overrides


def write_all(folder, profile_data=None, balance_data=None, risk_data=None):
    """Write the three instances into <folder>/profile/ and return that directory."""
    import json
    from pathlib import Path
    target = Path(folder) / 'profile'
    target.mkdir(parents=True, exist_ok=True)
    for name, value in (('profile.json', profile_data if profile_data is not None else profile()),
                        ('balance_sheet.json',
                         balance_data if balance_data is not None else balance_sheet()),
                        ('risk_profile.json',
                         risk_data if risk_data is not None else risk_profile())):
        (target / name).write_text(json.dumps(value, indent=2), encoding='utf-8')
    return target
