"""Phase 1 contract: derived arithmetic, provenance, staleness and the min-band rule.

Every expectation below is hand-computed from the fixture's docstring, not read back from the
tool. A test that asks the implementation what the answer is tests nothing.
"""
import datetime as dt
import sys
from pathlib import Path

from support import ROOT, WorkspaceTest
from tools import profile_check as pc

sys.path.insert(0, str(Path(__file__).resolve().parent / 'fixtures'))
import synthetic_household as fx  # noqa: E402

TODAY = dt.date.fromisoformat(fx.TODAY)


class ProfileTestCase(WorkspaceTest):
    def load(self, profile=None, balance=None, risk=None) -> pc.Profile:
        fx.write_all(self.work, profile, balance, risk)
        return pc.Profile(self.work, TODAY)


class DerivedArithmeticTests(ProfileTestCase):
    def test_balance_sheet_numbers_match_hand_computation(self):
        numbers = pc.balance_sheet_numbers(self.load())
        self.assertEqual(numbers['total_assets_eur'], 85000)
        self.assertEqual(numbers['total_liabilities_eur'], 25000)
        self.assertEqual(numbers['net_worth_eur'], 60000)
        self.assertEqual(numbers['net_worth_band'], 'B2')          # 25 000 - 99 999
        self.assertEqual(numbers['liquid_assets_eur'], 15000)      # L0 + L1 only
        self.assertEqual(numbers['emergency_months_covered'], 6.0)  # 15 000 / 2 500
        self.assertEqual(numbers['emergency_fund_gap_months'], 0)
        self.assertEqual(numbers['monthly_savings_capacity_eur'], 1000)
        self.assertEqual(numbers['savings_rate_pct'], 25.0)
        self.assertEqual(numbers['debt_service_ratio_pct'], 11.25)  # 450 / 4 000
        self.assertEqual(numbers['debt_to_income_pct'], 52.08)      # 25 000 / 48 000

    def test_allocation_and_concentration_use_invested_assets_not_net_worth(self):
        numbers = pc.balance_sheet_numbers(self.load())
        # invested = cash-like 15 000 + holdings 40 000 = 55 000. The pension and the L2 brokerage
        # account row are not double-counted on top of the holdings they contain.
        self.assertEqual(numbers['invested_assets_eur'], 55000)
        self.assertEqual(numbers['allocation_pct'],
                         {'bond': 18.18, 'cash': 27.27, 'equity': 54.55})
        self.assertEqual(numbers['largest_issuer_pct'], 54.55)   # 30 000 / 55 000
        self.assertEqual(numbers['crypto_share_pct'], 0)
        self.assertEqual(numbers['currency_exposure_pct'], {'EUR': 100.0})

    def test_a_negative_net_worth_is_reported_not_hidden_in_the_lowest_band(self):
        balance = fx.balance_sheet()
        balance['liabilities'][0]['balance_eur'] = 200000
        numbers = pc.balance_sheet_numbers(self.load(balance=balance))
        self.assertEqual(numbers['net_worth_eur'], -115000)
        self.assertEqual(numbers['net_worth_band'], 'B1')
        self.assertTrue(numbers['net_worth_is_negative'])

    def test_null_is_not_zero(self):
        balance = fx.balance_sheet()
        balance['cash_flow']['monthly_net_income'] = None
        numbers = pc.balance_sheet_numbers(self.load(balance=balance))
        self.assertIn('cash_flow.monthly_net_income', numbers['unset_fields'])
        self.assertIsNone(numbers['savings_rate_pct'])
        self.assertIsNone(numbers['debt_to_income_pct'])


class BandTests(ProfileTestCase):
    def test_tolerance_score_and_band(self):
        derived = pc.derive(self.load())
        self.assertEqual(derived['tolerance_score'], 33)   # 3+4+3+3+3+3+4+4+3+3
        self.assertEqual(derived['tolerance_band'], 'moderate')

    def test_a_skipped_history_item_is_rescaled_not_penalised(self):
        risk = fx.risk_profile()
        risk['questionnaire']['Q2'] = None                  # 29 over nine items
        derived = pc.derive(self.load(risk=risk))
        self.assertEqual(derived['tolerance_score'], round(29 * 10 / 9))  # 32
        self.assertEqual(derived['tolerance_band'], 'moderate')

    def test_capacity_factors_match_the_table(self):
        derived = pc.derive(self.load())
        # horizon 2031-09-01 is ~4.95 y -> 1; stable -> 2; 6.0 months -> 1; DTI 52 % -> 0;
        # one dependant -> 1. Sum 5 -> moderate.
        self.assertEqual(derived['capacity_factors'],
                         {'horizon_years': 1, 'income_stability': 2, 'emergency_months': 1,
                          'debt_to_income': 0, 'dependants': 1})
        self.assertEqual(derived['capacity_score'], 5)
        self.assertEqual(derived['capacity_band'], 'moderate')

    def test_effective_band_is_the_minimum(self):
        risk = fx.risk_profile(questionnaire={q: 5 for q in pc.QUESTIONS})  # tolerance high
        derived = pc.derive(self.load(risk=risk))
        self.assertEqual(derived['tolerance_band'], 'high')
        self.assertEqual(derived['capacity_band'], 'moderate')
        self.assertEqual(derived['effective_band'], 'moderate')

    def test_an_empty_emergency_fund_caps_capacity_at_low_whatever_the_total(self):
        balance = fx.balance_sheet()
        for asset in balance['assets']:                     # no L0/L1 money at all
            if asset['liquidity_tier'] in ('L0', 'L1'):
                asset['value_eur'] = 0
        balance['liabilities'] = []                         # DTI 0 -> 2, everything else high
        profile = fx.profile()
        profile['identity']['dependants'] = 0
        profile['goals'][0]['target_date'] = '2046-01-01'
        derived = pc.derive(self.load(profile=profile, balance=balance))
        self.assertEqual(derived['capacity_factors']['emergency_months'], 0)
        self.assertGreaterEqual(derived['capacity_score'], 6)
        self.assertEqual(derived['capacity_band'], 'low')
        self.assertEqual(derived['capacity_vetoes'], ['emergency_months'])

    def test_precarious_income_caps_capacity_at_low(self):
        profile = fx.profile()
        profile['career']['income_stability'] = 'precarious'
        balance = fx.balance_sheet()
        balance['cash_flow']['income_stability'] = 'precarious'
        derived = pc.derive(self.load(profile=profile, balance=balance))
        self.assertEqual(derived['capacity_band'], 'low')
        self.assertIn('income_stability', derived['capacity_vetoes'])

    def test_an_unset_input_scores_zero_and_is_named(self):
        profile = fx.profile()
        profile['identity']['dependants'] = None
        derived = pc.derive(self.load(profile=profile))
        self.assertEqual(derived['capacity_factors']['dependants'], 0)
        self.assertIn('dependants', derived['capacity_unknown_factors'])

    def test_limits_follow_the_effective_band_and_overrides_are_visible(self):
        risk = fx.risk_profile(limit_overrides=[
            {'limit': 'max_crypto_pct', 'value': 0, 'direction': 'tighten',
             'reason': 'user will not hold crypto', 'recorded_on': fx.TODAY}])
        derived = pc.derive(self.load(risk=risk))
        self.assertEqual(derived['limits']['max_equity_pct'], 60)      # moderate
        self.assertEqual(derived['limits']['max_crypto_pct'], 0)       # tightened from 5
        self.assertEqual(derived['limits']['max_single_issuer_pct'], 10)
        self.assertTrue(any('tightened' in note for note in derived['limit_notes']))

    def test_loosening_a_limit_is_recorded_as_requiring_a_deviation(self):
        risk = fx.risk_profile(limit_overrides=[
            {'limit': 'max_crypto_pct', 'value': 25, 'direction': 'loosen',
             'reason': 'user insists', 'recorded_on': fx.TODAY}])
        derived = pc.derive(self.load(risk=risk))
        self.assertEqual(derived['limits']['max_crypto_pct'], 25)
        self.assertTrue(any('DEVIATIONS' in note for note in derived['limit_notes']))

    def test_single_issuer_and_sector_limits_do_not_widen_with_the_band(self):
        for band in pc.BANDS:
            with self.subTest(band=band):
                self.assertEqual(pc.LIMITS[band]['max_single_issuer_pct'], 10)
                self.assertEqual(pc.LIMITS[band]['max_single_sector_pct'], 25)


class ProvenanceTests(ProfileTestCase):
    def test_a_populated_number_without_a_ledger_row_fails(self):
        balance = fx.balance_sheet()
        balance['source_ledger'] = [row for row in balance['source_ledger']
                                    if 'assets' not in str(row['covers'])]
        problems = self.load(balance=balance).validate()
        self.assertTrue(any('assets[0].value_eur' in p and 'source_ledger' in p
                            for p in problems), problems)

    def test_wildcards_cover_every_row_of_an_array(self):
        balance = fx.balance_sheet()
        balance['assets'].append(dict(balance['assets'][0], account='another'))
        self.assertEqual(self.load(balance=balance).validate(), [])

    def test_an_unpopulated_number_needs_no_row(self):
        balance = fx.balance_sheet()
        balance['pension']['fondo_pensione_balance_eur'] = None
        balance['pension']['fondo_pensione_annual_contribution_eur'] = None
        balance['pension']['employer_match_pct'] = None
        balance['source_ledger'] = [row for row in balance['source_ledger']
                                    if not str(row['covers']).startswith("['pension")]
        self.assertEqual(self.load(balance=balance).validate(), [])

    def test_a_ledger_row_needs_a_source_and_a_date(self):
        balance = fx.balance_sheet()
        balance['source_ledger'][0]['source'] = ''
        balance['source_ledger'][0]['confirmed_on'] = 'last tuesday'
        problems = self.load(balance=balance).validate()
        self.assertTrue(any('.source: required' in p for p in problems))
        self.assertTrue(any('not an ISO date' in p for p in problems))


class StalenessTests(ProfileTestCase):
    def test_a_figure_older_than_ninety_days_is_listed_with_its_age(self):
        balance = fx.balance_sheet()
        balance['assets'][0]['as_of'] = '2026-01-01'        # 262 days before TODAY
        stale = self.load(balance=balance).stale_fields()
        self.assertEqual([s['path'] for s in stale], ['assets[0].as_of'])
        self.assertEqual(stale[0]['days'], 262)

    def test_a_fresh_figure_is_not_stale(self):
        self.assertEqual(self.load().stale_fields(), [])


class SchemaTests(ProfileTestCase):
    def test_a_string_amount_is_an_error_not_a_parse_job(self):
        balance = fx.balance_sheet()
        balance['assets'][0]['value_eur'] = '12.000 EUR'
        problems = self.load(balance=balance).validate()
        self.assertTrue(any('bare numbers in EUR' in p for p in problems))

    def test_closed_vocabularies(self):
        balance = fx.balance_sheet()
        balance['assets'][0]['liquidity_tier'] = 'L9'
        balance['holdings'][0]['asset_class'] = 'magic_beans'
        profile = fx.profile()
        profile['goals'][0]['flexibility'] = 'ish'
        problems = self.load(profile=profile, balance=balance).validate()
        self.assertEqual(len([p for p in problems if 'is not one of' in p]), 3, problems)

    def test_insurance_defaults_to_unknown_never_absent(self):
        balance = fx.balance_sheet()
        del balance['insurance']['disability']
        self.assertEqual([p for p in self.load(balance=balance).validate()
                          if 'insurance' in p], [])

    def test_a_duplicate_goal_id_is_refused(self):
        profile = fx.profile()
        profile['goals'][1]['goal_id'] = 'G1'
        self.assertTrue(any('duplicate' in p for p in self.load(profile=profile).validate()))

    def test_a_hand_typed_derived_field_is_a_conflict(self):
        risk = fx.risk_profile(effective_band='high')
        loaded = self.load(risk=risk)
        self.assertTrue(any('computed, never typed' in c
                            for c in pc.conflicts(loaded, pc.derive(loaded))))


class CliTests(ProfileTestCase):
    def test_check_then_derive_write_stores_the_computed_fields(self):
        self.load()
        self.run_tool('profile_check.py', '--root', self.work, '--today', fx.TODAY, 'check')
        self.run_tool('profile_check.py', '--root', self.work, '--today', fx.TODAY,
                      'derive', '--write')
        import json
        risk = json.loads((self.work / 'profile' / 'risk_profile.json').read_text(encoding='utf-8'))
        derived = json.loads((self.work / 'profile' / 'derived.json').read_text(encoding='utf-8'))
        self.assertEqual(risk['effective_band'], 'moderate')
        self.assertEqual(risk['limits']['max_equity_pct'], 60)
        self.assertEqual(derived['balance_sheet']['net_worth_eur'], 60000)

    def test_check_fails_loudly_without_a_traceback(self):
        balance = fx.balance_sheet()
        balance['source_ledger'] = []
        self.load(balance=balance)
        result = self.run_tool('profile_check.py', '--root', self.work, '--today', fx.TODAY,
                               'check', ok=False)
        self.assertNotIn('Traceback', result.stderr + result.stdout)
        self.assertIn('no source_ledger row covers it', result.stdout)

    def test_a_missing_profile_is_a_clean_refusal(self):
        result = self.run_tool('profile_check.py', '--root', self.work, 'check', ok=False)
        self.assertIn('not found', result.stderr)
        self.assertNotIn('Traceback', result.stderr)


class ReferenceContractTests(WorkspaceTest):
    """The references and the tool must not drift apart."""

    def reference(self, name):
        return (ROOT / '.claude/skills/financial-advisor' / name).read_text(encoding='utf-8')

    def test_the_min_band_rule_is_stated_in_03(self):
        text = self.reference('03-risk-profile.md')
        self.assertIn('min(tolerance_band, capacity_band)', text)
        self.assertIn('capacity_band', text)

    def test_the_limits_table_agrees_with_the_tool(self):
        text = self.reference('03-risk-profile.md')
        for band, limits in pc.LIMITS.items():
            with self.subTest(band=band):
                self.assertIn(band, text)
        # The crypto row: low 0 %, moderate 5 %, high 10 %.
        self.assertIn('| Max crypto share | 0 % | 5 % | 10 % |', text)
        self.assertEqual([pc.LIMITS[b]['max_crypto_pct'] for b in ('low', 'moderate', 'high')],
                         [0, 5, 10])

    def test_the_staleness_window_agrees_with_the_tool(self):
        self.assertEqual(pc.STALE_DAYS, 90)
        self.assertIn('**90 days**', self.reference('02-balance-sheet-and-cashflow.md'))

    def test_the_net_worth_bands_agree_with_the_tool(self):
        text = self.reference('02-balance-sheet-and-cashflow.md')
        for label, _ in pc.NET_WORTH_BANDS:
            with self.subTest(label=label):
                self.assertIn(f'| `{label}` |', text)

    def test_the_emergency_fund_is_l0_plus_l1_in_both_places(self):
        self.assertIn('L0 + L1', self.reference('02-balance-sheet-and-cashflow.md'))
        self.assertEqual(pc.LIQUIDITY_TIERS, ('L0', 'L1', 'L2', 'L3'))

    def test_the_references_are_no_longer_stubs(self):
        for name in ('01-financial-profile.md', '02-balance-sheet-and-cashflow.md',
                     '03-risk-profile.md'):
            with self.subTest(name=name):
                self.assertNotIn('Not yet populated', self.reference(name))
