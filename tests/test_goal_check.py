"""Synthetic wealth-goal cases with hand-computed expected results. No real household data."""
import datetime as dt
import json

from support import WorkspaceTest
from tools import goal_check as gc

AS_OF = '2026-01-01'


def household(target=20000, due='2028-01-01', **goal):
    profile = {'goals': [{'goal_id': 'G1', 'target_amount_eur': target, 'target_date': due} | goal]}
    derived = {'computed_on': AS_OF,
               'balance_sheet': {'net_worth_eur': 10000, 'invested_assets_eur': 8000,
                                 'liquid_assets_eur': 3000, 'monthly_savings_capacity_eur': 100}}
    return profile, derived


def inputs(**extra):
    return {'goal_id': 'G1', 'start_basis': 'net_worth'} | extra


class ArithmeticTests(WorkspaceTest):
    def test_doubling_in_two_years_with_no_saving_needs_the_square_root_of_two(self):
        # 10 000 -> 20 000 over two years: (1 + r)^2 = 2, so r = 41.4 % a year (730 days, not
        # 730.5, puts the exact figure at 41.46).
        profile, derived = household()
        result = gc.check(profile, derived, inputs(monthly_saving_eur=0), as_of=AS_OF)
        self.assertEqual(result['status'], 'needs_growth')
        self.assertAlmostEqual(result['required_annual_return_pct'], 41.4, delta=0.15)
        self.assertIsNone(result['date_reached_without_growth'])

    def test_saving_alone_is_reported_before_any_return_is_demanded(self):
        # 24 months x 500 = 12 000 on top of 10 000 clears a 20 000 target with no growth.
        profile, derived = household()
        result = gc.check(profile, derived, inputs(monthly_saving_eur=500), as_of=AS_OF)
        self.assertEqual(result['status'], 'saving_alone_is_enough')
        self.assertEqual(result['required_annual_return_pct'], 0.0)
        self.assertAlmostEqual(result['saving_alone_reaches_eur'], 22000, delta=10)

    def test_the_gap_and_the_saving_that_closes_it_are_hand_computed(self):
        # Default saving 100 a month -> 12 400 after 24 months, 7 600 short; 10 000 / 24 = 416.67.
        profile, derived = household()
        result = gc.check(profile, derived, inputs(), as_of=AS_OF)
        self.assertAlmostEqual(result['gap_without_growth_eur'], 7600, delta=10)
        self.assertAlmostEqual(result['saving_needed_without_growth_eur'], 416.67, delta=0.5)
        # 100 months of saving 100 closes a 10 000 gap.
        reached = dt.date.fromisoformat(result['date_reached_without_growth'])
        self.assertAlmostEqual((reached - dt.date.fromisoformat(AS_OF)).days, 100 * 30.4375, delta=2)
        self.assertTrue(any('assumes all of it is saved' in w for w in result['warnings']))

    def test_the_required_return_really_reaches_the_target(self):
        profile, derived = household()
        result = gc.check(profile, derived, inputs(), as_of=AS_OF)
        reached = gc.future_value(10000, 100, result['required_annual_return_pct'] / 100,
                                  result['months_left'])
        self.assertAlmostEqual(reached, 20000, delta=40)

    def test_cash_that_does_not_grow_is_pointed_out(self):
        # Net worth 10 000 but only 8 000 invested: the quoted rate flatters the invested part.
        profile, derived = household()
        result = gc.check(profile, derived, inputs(), as_of=AS_OF)
        self.assertTrue(any('invested part alone is higher' in line for line in result['plain']))
        invested = gc.check(profile, derived, inputs(start_basis='invested_assets'), as_of=AS_OF)
        self.assertFalse(any('invested part alone' in line for line in invested['plain']))

    def test_an_impossible_gap_says_so_instead_of_inventing_a_rate(self):
        profile, derived = household(target=10 ** 12, due='2026-02-01')
        result = gc.check(profile, derived, inputs(), as_of=AS_OF)
        self.assertIsNone(result['required_annual_return_pct'])
        self.assertTrue(any('No yearly growth rate' in line for line in result['plain']))

    def test_what_if_rates_are_labelled_assumptions(self):
        profile, derived = household()
        result = gc.check(profile, derived, inputs(what_if_annual_returns_pct=[0, 5]), as_of=AS_OF)
        flat, five = result['what_if']
        self.assertAlmostEqual(flat['amount_on_target_date_eur'], 12400, delta=10)
        self.assertGreater(five['amount_on_target_date_eur'], flat['amount_on_target_date_eur'])
        self.assertLess(five['monthly_saving_needed_eur'], flat['monthly_saving_needed_eur'])
        self.assertIn('assumption', result['what_if_note'])


class StatusTests(WorkspaceTest):
    def test_each_edge_has_its_own_status(self):
        cases = [(household(target=None), 'unknown'), (household(due='2025-06-01'), 'past_due'),
                 (household(target=9000), 'already_reached')]
        for (profile, derived), expected in cases:
            self.assertEqual(gc.check(profile, derived, inputs(), as_of=AS_OF)['status'], expected)

    def test_the_start_basis_is_never_guessed(self):
        profile, derived = household()
        with self.assertRaisesRegex(gc.StateError, 'start_basis'):
            gc.check(profile, derived, {'goal_id': 'G1'}, as_of=AS_OF)
        explicit = gc.check(profile, derived, inputs(start_basis='explicit', start_amount_eur=5000),
                            as_of=AS_OF)
        self.assertEqual(explicit['start_eur'], 5000)

    def test_unknown_inputs_and_goals_are_refused(self):
        profile, derived = household()
        for bad in ({'goal_id': 'G9', 'start_basis': 'net_worth'}, inputs(expected_return=7),
                    inputs(what_if_annual_returns_pct=[500])):
            with self.assertRaises(gc.StateError):
                gc.check(profile, derived, bad, as_of=AS_OF)

    def test_stale_derived_figures_are_flagged(self):
        profile, derived = household()
        derived['computed_on'] = '2025-01-01'
        result = gc.check(profile, derived, inputs(), as_of=AS_OF)
        self.assertTrue(any('older than 90 days' in w for w in result['warnings']))

    def test_the_result_is_a_what_if_and_says_it_is_not_a_prediction(self):
        profile, derived = household()
        result = gc.check(profile, derived, inputs(), as_of=AS_OF)
        self.assertEqual(result['kind'], 'what_if')
        self.assertIn('not a prediction', result['plain'][-1])
        self.assertNotIn('probab', ' '.join(result['plain']).lower())


class HistoryTests(WorkspaceTest):
    def prices(self, values):
        rows = {}
        for index, value in enumerate(values):
            day = (dt.date(2015, 1, 1) + dt.timedelta(days=365 * index)).isoformat()
            rows[f'synthetic:SYN@{day}'] = {
                'key': f'synthetic:SYN@{day}', 'kind': 'price', 'symbol': 'SYN',
                'provider': 'synthetic', 'currency': 'EUR', 'unit': None, 'asof': day,
                'value': value, 'fetched_at': day + 'T00:00:00+00:00', 'published': day,
                'is_forecast': False}
        self.write('market/observations.json', rows)

    def test_past_periods_are_counted_not_turned_into_odds(self):
        # Yearly prices 100, 150, 150, 300: one-year changes of +50 %, 0 % and +100 %.
        self.prices([100, 150, 150, 300])
        profile, derived = household(target=16000, due='2027-01-01')     # needs +60 % in a year
        result = gc.check(profile, derived,
                          inputs(monthly_saving_eur=0, history={'symbol': 'SYN'}),
                          root=self.work, as_of=AS_OF)
        self.assertEqual(result['history']['n_windows'], 3)
        self.assertEqual(result['history']['n_reached'], 1)
        self.assertIn('not a probability', result['history']['note'])

    def test_missing_history_is_no_evidence_either_way(self):
        self.write('market/observations.json', {})
        profile, derived = household()
        result = gc.check(profile, derived, inputs(history={'symbol': 'SYN'}),
                          root=self.work, as_of=AS_OF)
        self.assertEqual(result['history']['status'], 'insufficient_history')
        self.assertTrue(any('no evidence either way' in line for line in result['plain']))


class CommandLineTests(WorkspaceTest):
    def test_the_tool_reads_the_profile_and_prints_json(self):
        profile, derived = household()
        self.write('profile/profile.json', profile)
        self.write('profile/derived.json', derived)
        path = self.write('inputs.json', inputs())
        result = self.run_tool('goal_check.py', '--root', self.work, 'check', '--inputs', path,
                               '--as-of', AS_OF)
        self.assertEqual(json.loads(result.stdout)['status'], 'needs_growth')
