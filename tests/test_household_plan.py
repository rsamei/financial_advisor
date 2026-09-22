"""Synthetic cash-budget cases with independently computed expected results."""
import copy
import json

from support import ROOT, WorkspaceTest
from tools import household_plan as hp


def inputs():
    return ({'cash_flow': {'monthly_net_income': 300, 'monthly_essential_expenses': 100,
                          'monthly_discretionary': 50, 'emergency_fund_target_months': 2,
                          'as_of': '2026-01-31'},
             'assets': [{'liquidity_tier': 'L0', 'value_eur': 1000, 'as_of': '2026-01-31'}],
             'holdings': [], 'liabilities': []},
            {'goals': []}, {'events': [], 'events_complete': True})


class PlanningTests(WorkspaceTest):
    def plan(self, balance, profile, assumptions, **kwargs):
        return hp.project(balance, profile, assumptions, months=1, as_of='2026-01-31', **kwargs)

    def test_cash_budget_and_reserve_have_hand_computed_values(self):
        b, p, a = inputs()
        result = self.plan(b, p, a)
        self.assertEqual(result['ending_cash_eur'], 1150)
        self.assertEqual(result['timeline'][0]['protected_reserve_eur'], 200)
        self.assertEqual(result['timeline'][0]['available_above_reserve_eur'], 950)

    def test_two_goals_cannot_spend_the_same_cash(self):
        b, p, a = inputs()
        p['goals'] = [{'goal_id': key, 'target_date': '2026-02-28', 'target_amount_eur': 600,
                       'priority': i} for i, key in enumerate(('first', 'second'))]
        a['goal_kinds'] = {'first': 'cash_outlay', 'second': 'cash_outlay'}
        result = self.plan(b, p, a)
        self.assertEqual([g['shortfall_eur'] for g in result['goals']], [0, 250])
        self.assertEqual(result['ending_cash_eur'], 200)

    def test_wealth_goal_is_not_subtracted_as_a_purchase(self):
        b, p, a = inputs()
        p['goals'] = [{'goal_id': 'wealth', 'target_date': '2026-02-28', 'target_amount_eur': 600}]
        result = self.plan(b, p, a)
        self.assertEqual(result['goals'][0]['status'], 'not_modelled')
        self.assertEqual(result['ending_cash_eur'], 1150)

    def test_irregular_bill_is_counted_once_on_its_date(self):
        b, p, a = inputs()
        a['events'] = [{'on': '2026-02-14', 'amount_eur': -250, 'source': 'synthetic bill'}]
        self.assertEqual(self.plan(b, p, a)['ending_cash_eur'], 900)

    def test_debt_requires_confirmation_and_is_not_counted_twice(self):
        b, p, a = inputs()
        b['liabilities'] = [{'monthly_payment_eur': 40}]
        with self.assertRaisesRegex(hp.StateError, 'debt_payments_in_expenses'):
            self.plan(b, p, a)
        a['debt_payments_in_expenses'] = True
        self.assertEqual(self.plan(b, p, a)['ending_cash_eur'], 1150)
        a['debt_payments_in_expenses'] = False
        self.assertEqual(self.plan(b, p, a)['ending_cash_eur'], 1110)

    def test_unknown_and_nonfinite_values_never_become_zero(self):
        for missing in (None, float('nan'), float('inf'), True):
            b, p, a = inputs()
            b['cash_flow']['monthly_net_income'] = missing
            with self.subTest(missing=missing), self.assertRaises(hp.StateError):
                self.plan(b, p, a)

    def test_combined_shocks_apply_together_without_spending_investments(self):
        b, p, a = inputs()
        b['holdings'] = [{'value_eur': 800}]
        a['stress'] = {'income_loss_pct': 100, 'income_loss_months': 1,
                       'expense_increase_pct': 20, 'investment_fall_pct': 25}
        result = self.plan(b, p, a, stress=True)
        self.assertEqual(result['ending_cash_eur'], 820)
        self.assertEqual(result['investment_stress_loss_eur'], 200)

    def test_comparison_adds_do_nothing_and_keeps_unknown_taxes_visible(self):
        b, p, a = inputs()
        original = copy.deepcopy((b, p, a))
        option = {'id': 'save', 'monthly_expense_change_eur': -20, 'fees_eur': 5,
                  'source': 'explicit assumption'}
        results = hp.compare(b, p, a, [option], 1, '2026-01-31')['results']
        self.assertEqual(len(results), 4)
        self.assertEqual(results[0]['option_id'], 'do_nothing')
        self.assertEqual(results[2]['ending_cash_eur'], 1165)
        self.assertEqual(results[2]['tax_status'], 'unverified')
        self.assertTrue(any('Tax treatment' in w for w in results[2]['warnings']))
        self.assertEqual((b, p, a), original)

    def test_a_pausable_contribution_stops_only_while_income_is_lost(self):
        # Income 300, essentials 100, discretionary 50 -> 150 a month of recorded outgoings.
        # The option adds 60 a month of contribution, all of it declared pausable.
        # Stressed: no income for the one month of the horizon (28 days in Feb 2026).
        #   paused    -> only the recorded 150 is spent -> 1000 - 150 = 850
        #   not paused-> the contribution keeps running -> 1000 - 210 = 790
        b, p, a = inputs()
        a['stress'] = {'income_loss_pct': 100, 'income_loss_months': 1,
                       'expense_increase_pct': 0, 'investment_fall_pct': 0}
        base = {'id': 'plan', 'monthly_expense_change_eur': 60, 'fees_eur': 0,
                'source': 'explicit assumption'}
        kept = self.plan(b, p, a, option=base, stress=True)
        paused = self.plan(b, p, a, option=base | {'pausable_monthly_expense_eur': 60}, stress=True)
        self.assertEqual(kept['ending_cash_eur'], 790)
        self.assertEqual(paused['ending_cash_eur'], 850)
        self.assertTrue(any('stated intention' in w for w in paused['warnings']))

    def test_pausing_never_applies_without_an_income_loss(self):
        # Same option, no stress: the contribution runs all month -> 1000 + 300 - 210 = 1090.
        b, p, a = inputs()
        option = {'id': 'plan', 'monthly_expense_change_eur': 60,
                  'pausable_monthly_expense_eur': 60, 'fees_eur': 0, 'source': 'assumption'}
        result = self.plan(b, p, a, option=option)
        self.assertEqual(result['ending_cash_eur'], 1090)
        self.assertTrue(any('nothing pauses it here' in w for w in result['warnings']))

    def test_recorded_essential_spending_can_never_be_declared_pausable(self):
        b, p, a = inputs()
        option = {'id': 'plan', 'monthly_expense_change_eur': 60,
                  'pausable_monthly_expense_eur': 100, 'fees_eur': 0, 'source': 'assumption'}
        with self.assertRaisesRegex(hp.StateError, 'cannot exceed'):
            self.plan(b, p, a, option=option)

    def test_omitting_the_field_leaves_results_unchanged(self):
        b, p, a = inputs()
        a['stress'] = {'income_loss_pct': 100, 'income_loss_months': 1,
                       'expense_increase_pct': 0, 'investment_fall_pct': 0}
        option = {'id': 'plan', 'monthly_expense_change_eur': 60, 'fees_eur': 0,
                  'source': 'assumption'}
        explicit_zero = self.plan(b, p, a, option=option | {'pausable_monthly_expense_eur': 0},
                                  stress=True)
        self.assertEqual(self.plan(b, p, a, option=option, stress=True)['ending_cash_eur'],
                         explicit_zero['ending_cash_eur'])

    def test_stale_inputs_warn_and_future_inputs_fail(self):
        b, p, a = inputs()
        b['cash_flow']['as_of'] = '2025-01-01'
        self.assertTrue(self.plan(b, p, a)['warnings'])
        b['cash_flow']['as_of'] = '2026-02-01'
        with self.assertRaisesRegex(hp.StateError, 'future-dated'):
            self.plan(b, p, a)

    def test_cli_uses_local_inputs_without_writing_profile(self):
        b, p, a = inputs()
        self.write('profile/balance_sheet.json', b)
        self.write('profile/profile.json', p)
        path = self.write('advice/assumptions.json', a)
        result = self.run_tool('household_plan.py', '--root', self.work, 'project',
                               '--assumptions', path, '--months', '1', '--as-of', '2026-01-31')
        self.assertEqual(json.loads(result.stdout)['ending_cash_eur'], 1150)
        self.assertEqual(json.loads((self.work / 'profile/balance_sheet.json').read_text()), b)

    def test_reference_routes_calculations_without_loosening_gates(self):
        reference = (ROOT / '.claude/skills/financial-advisor/13-household-planning.md').read_text()
        for phrase in ('No score weights change', 'Every candidate still follows `/decide`',
                       'Unknown tax', 'No execution'):
            self.assertIn(phrase, reference)
