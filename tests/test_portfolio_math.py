"""Portfolio arithmetic, checked against numbers computed by hand from the fixture.

The fixture household (tests/fixtures/synthetic_household.py):
  cash 12 000 (L0) + deposit 3 000 (L1) + brokerage 40 000 (L2) + pension 30 000 (L3) = 85 000
  holdings: equity 30 000 (issuer A, diversified) + bond 10 000 (issuer B, government) = 40 000
  invested = cash-like 15 000 + holdings 40 000 = 55 000
  liabilities: mortgage 25 000 at 2.1 %, 450/month
  cash flow: income 4 000, essential 2 500, discretionary 500
"""
import sys
from pathlib import Path

from support import WorkspaceTest
from tools import portfolio_math as pm

sys.path.insert(0, str(Path(__file__).resolve().parent / 'fixtures'))
import synthetic_household as fx  # noqa: E402


class AllocationTests(WorkspaceTest):
    def setUp(self):
        super().setUp()
        self.balance = fx.balance_sheet()

    def test_allocation_shares_are_of_invested_assets(self):
        result = pm.allocation(self.balance)
        self.assertEqual(result['invested_eur'], 55000)
        self.assertEqual(result['by_class_eur'], {'bond': 10000, 'cash': 15000, 'equity': 30000})
        self.assertEqual(result['by_class_pct'],
                         {'bond': 18.18, 'cash': 27.27, 'equity': 54.55})

    def test_concentration_names_the_largest_issuer_and_sector(self):
        result = pm.concentration(self.balance)
        self.assertEqual(result['largest_issuer'], 'Synthetic Issuer A')
        self.assertEqual(result['largest_issuer_pct'], 54.55)     # 30 000 / 55 000
        self.assertEqual(result['largest_sector_pct'], 54.55)
        self.assertEqual(result['crypto_share_pct'], 0.0)

    def test_a_holding_with_no_issuer_is_named_not_ignored(self):
        balance = fx.balance_sheet()
        balance['holdings'][0].pop('issuer')
        result = pm.concentration(balance)
        self.assertEqual(result['holdings_without_issuer'], ['SYN0000001'])
        # The largest issuer is now the bond - which is only true because a holding is unlabelled.
        self.assertEqual(result['largest_issuer'], 'Synthetic Issuer B')

    def test_currency_exposure_is_all_euro_here(self):
        self.assertEqual(pm.currency_exposure(self.balance)['non_eur_pct'], 0.0)

    def test_cost_drag_is_value_weighted_and_reports_its_coverage(self):
        result = pm.cost_drag(self.balance)
        # (30 000 x 0.20 + 10 000 x 0.15) / 40 000 = 0.1875 %
        self.assertEqual(result['weighted_ter_pct'], 0.1875)
        self.assertEqual(result['annual_cost_eur'], 75.0)         # 40 000 x 0.1875 %
        self.assertEqual(result['coverage_pct'], 72.73)           # 40 000 of 55 000 invested
        self.assertEqual(result['holdings_without_ter'], [])

    def test_a_missing_ter_is_named_so_the_headline_is_not_mistaken_for_the_portfolio(self):
        balance = fx.balance_sheet()
        balance['holdings'][0]['ter_pct'] = None
        result = pm.cost_drag(balance)
        self.assertEqual(result['weighted_ter_pct'], 0.15)        # only the bond is covered
        self.assertEqual(result['holdings_without_ter'], ['SYN0000001'])
        self.assertLess(result['coverage_pct'], 20)


class DriftTests(WorkspaceTest):
    TARGETS = {'equity': 60, 'bond': 30, 'cash': 10}

    def test_drift_is_current_minus_target_in_points(self):
        rows = {row['asset_class']: row for row in
                pm.drift(fx.balance_sheet(), self.TARGETS)['rows']}
        self.assertEqual(rows['equity']['drift_pp'], -5.45)       # 54.55 - 60
        self.assertEqual(rows['bond']['drift_pp'], -11.82)        # 18.18 - 30
        self.assertEqual(rows['cash']['drift_pp'], 17.27)         # 27.27 - 10

    def test_targets_that_do_not_sum_to_a_hundred_are_refused(self):
        with self.assertRaisesRegex(pm.StateError, 'sum to 90'):
            pm.drift(fx.balance_sheet(), {'equity': 60, 'bond': 30})


class RebalanceTests(WorkspaceTest):
    def test_only_classes_outside_their_band_generate_a_trade(self):
        result = pm.rebalance(fx.balance_sheet(), {'equity': 60, 'bond': 30, 'cash': 10})
        moved = {t['asset_class']: t for t in result['trades']}
        # equity drifts -5.45 pp, just outside the ±5 pp band; bond -11.82; cash +17.27.
        self.assertEqual(set(moved), {'equity', 'bond', 'cash'})
        self.assertEqual(moved['bond']['direction'], 'buy')
        self.assertEqual(moved['bond']['amount_eur'], 6501.0)     # 11.82 % of 55 000
        self.assertEqual(moved['cash']['direction'], 'sell')

    def test_nothing_is_proposed_when_every_class_is_inside_its_band(self):
        targets = {'equity': 54.55, 'bond': 18.18, 'cash': 27.27}
        result = pm.rebalance(fx.balance_sheet(), targets)
        self.assertEqual(result['trades'], [])
        self.assertIn('would be a cost, not a strategy', result['note'])

    def test_a_small_class_uses_the_relative_band(self):
        # crypto target 2 %, actual 0 % -> relative drift -100 %, far outside the 25 % band.
        balance = fx.balance_sheet()
        result = pm.rebalance(balance, {'equity': 54, 'bond': 18, 'cash': 26, 'crypto': 2})
        crypto = next(t for t in result['trades'] if t['asset_class'] == 'crypto')
        self.assertEqual(crypto['direction'], 'buy')
        self.assertIn('relative', crypto['band_rule'])

    def test_a_proposal_warns_that_a_sale_may_be_taxable(self):
        result = pm.rebalance(fx.balance_sheet(), {'equity': 60, 'bond': 30, 'cash': 10})
        self.assertIn('taxable gain', result['note'])


class TaxLotTests(WorkspaceTest):
    def test_average_cost_is_the_italian_method(self):
        # 300 units at avg cost 90, valued 30 000 -> price 100. Selling 100 units:
        # proceeds 10 000, basis 9 000, gain 1 000.
        result = pm.average_cost_lots(fx.balance_sheet(), 'SYN0000001', 100)
        self.assertEqual(result['average_cost_eur'], 90.0)
        self.assertEqual(result['price_eur'], 100.0)
        self.assertEqual(result['proceeds_eur'], 10000.0)
        self.assertEqual(result['cost_basis_eur'], 9000.0)
        self.assertEqual(result['gain_eur'], 1000.0)
        self.assertIn('costo medio ponderato', result['method'])

    def test_no_tax_rate_is_applied_until_07_is_verified(self):
        result = pm.average_cost_lots(fx.balance_sheet(), 'SYN0000001', 100)
        self.assertIn('no rate is applied here', result['tax_note'])
        self.assertNotIn('tax_eur', result)

    def test_selling_more_than_is_held_is_refused(self):
        with self.assertRaisesRegex(pm.StateError, 'asked to sell'):
            pm.average_cost_lots(fx.balance_sheet(), 'SYN0000001', 10000)

    def test_an_unknown_symbol_is_refused(self):
        with self.assertRaisesRegex(pm.StateError, 'no holding with'):
            pm.average_cost_lots(fx.balance_sheet(), 'NOT-HELD', 1)


class StressTests(WorkspaceTest):
    def test_each_scenario_hits_the_class_it_names(self):
        result = pm.stress(fx.balance_sheet())['scenarios']
        self.assertEqual(result['equity_drawdown']['loss_eur'], 9000.0)    # 30 000 x 30 %
        self.assertEqual(result['crypto_drawdown']['loss_eur'], 0.0)       # no crypto held
        self.assertEqual(result['currency_move']['loss_eur'], 0.0)         # all EUR
        # bonds 10 000, default duration 6y, +2 pp -> 10 000 x 6 x 0.02 = 1 200
        self.assertEqual(result['rate_shock']['loss_eur'], 1200.0)

    def test_the_income_loss_scenario_measures_the_buffer(self):
        income = pm.stress(fx.balance_sheet())['scenarios']['income_loss']
        # liquid 15 000 against 6 x 2 500 = 15 000 of essential spending: exactly survives.
        self.assertEqual(income['liquid_after_eur'], 0.0)
        self.assertTrue(income['survives'])
        self.assertEqual(income['emergency_months_covered'], 6.0)

    def test_a_declared_duration_is_used_instead_of_the_default(self):
        balance = fx.balance_sheet()
        balance['holdings'][1]['duration_years'] = 2.0
        result = pm.stress(balance)['scenarios']['rate_shock']
        self.assertEqual(result['loss_eur'], 400.0)                        # 10 000 x 2 x 0.02
        self.assertIn('duration 2y', result['label'])

    def test_stress_runs_on_the_post_action_balance_sheet(self):
        action = {'kind': 'buy', 'asset_class': 'equity', 'amount_eur': 10000}
        after = pm.stress(fx.balance_sheet(), action)
        self.assertEqual(after['applied_to'], 'post-action balance sheet')
        # equity is now 40 000, so a 30 % drawdown costs 12 000 rather than 9 000.
        self.assertEqual(after['scenarios']['equity_drawdown']['loss_eur'], 12000.0)
        # and the cash that funded it is no longer in the emergency buffer.
        self.assertEqual(after['scenarios']['income_loss']['liquid_after_eur'], -10000.0)
        self.assertFalse(after['scenarios']['income_loss']['survives'])

    def test_an_unaffordable_action_is_refused_rather_than_modelled(self):
        with self.assertRaisesRegex(pm.StateError, 'only'):
            pm.apply_action(fx.balance_sheet(),
                            {'kind': 'buy', 'asset_class': 'equity', 'amount_eur': 100000})

    def test_an_unsupported_action_is_refused_rather_than_approximated(self):
        with self.assertRaisesRegex(pm.StateError, 'unsupported action kind'):
            pm.apply_action(fx.balance_sheet(), {'kind': 'short_the_market', 'amount_eur': 1})

    def test_applying_an_action_never_mutates_the_instance(self):
        balance = fx.balance_sheet()
        before = balance['assets'][0]['value_eur']
        pm.apply_action(balance, {'kind': 'buy', 'asset_class': 'equity', 'amount_eur': 5000})
        self.assertEqual(balance['assets'][0]['value_eur'], before)

    def test_debt_funded_buying_adds_a_liability_and_keeps_the_cash(self):
        after = pm.apply_action(fx.balance_sheet(), {
            'kind': 'buy', 'asset_class': 'equity', 'amount_eur': 10000, 'from': 'debt',
            'rate_pct': 8.0})
        self.assertEqual(after['assets'][0]['value_eur'], 12000)          # cash untouched
        self.assertEqual(len(after['liabilities']), 2)
        self.assertEqual(after['liabilities'][1]['balance_eur'], 10000)

    def test_repaying_debt_moves_money_from_cash_to_the_liability(self):
        after = pm.apply_action(fx.balance_sheet(), {'kind': 'repay_debt', 'amount_eur': 5000})
        self.assertEqual(after['liabilities'][0]['balance_eur'], 20000)
        self.assertEqual(after['assets'][0]['value_eur'], 7000)


class CliTests(WorkspaceTest):
    def setUp(self):
        super().setUp()
        fx.write_all(self.work)

    def test_allocation_prints_json(self):
        import json
        result = self.run_tool('portfolio_math.py', '--root', self.work, 'allocation')
        self.assertEqual(json.loads(result.stdout)['invested_eur'], 55000)

    def test_a_missing_balance_sheet_is_a_clean_refusal(self):
        result = self.run_tool('portfolio_math.py', '--root', self.tmp.name + '/nowhere',
                               'allocation', ok=False)
        self.assertNotIn('Traceback', result.stderr)
        self.assertIn('not found', result.stderr)
