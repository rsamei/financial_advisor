"""Synthetic cash-allocation cases with hand-computed expected results."""
import copy

from support import WorkspaceTest
from tools import cash_plan as cp, decision_score as ds


def base():
    balance = {'cash_flow': {'monthly_net_income': 3000, 'monthly_essential_expenses': 1000,
                             'monthly_discretionary': 500, 'emergency_fund_target_months': 3,
                             'as_of': '2026-09-01'},
               'assets': [{'liquidity_tier': 'L0', 'value_eur': 10000, 'as_of': '2026-09-01'},
                          {'liquidity_tier': 'L2', 'value_eur': 5000, 'as_of': '2026-09-01'}],
               'holdings': [], 'liabilities': []}
    profile = {'goals': [{'goal_id': 'G1', 'description': 'Car', 'target_amount_eur': 4000,
                          'target_date': '2027-09-01', 'priority': 1},
                         {'goal_id': 'G2', 'description': 'Retirement', 'target_amount_eur': 500000,
                          'target_date': '2056-01-01', 'priority': 2}]}
    question = {'as_of': '2026-09-21', 'arrears': False, 'ips_status': 'frozen',
                'goal_kinds': {'G1': 'cash_outlay', 'G2': 'wealth'}}
    return balance, profile, question


def findings(key, gate_verdict='PASS'):
    return {'decision_id': key, 'ips_version': '1.0',
            'gates': {name: {'verdict': 'SUPPORTED' if i == 3 else gate_verdict,
                             'evidence': ['ledger:cash_flow.*']} for i, name in enumerate(ds.GATES)},
            'dimensions': {k: 85 for k in ds.WEIGHTS}}


class CashPlanTests(WorkspaceTest):
    def test_fully_eligible_split_reconciles(self):
        r = cp.plan(*base())
        # 10000 L0 only; reserve 3 x 1000; car goal 4000 inside three years; 3000 free.
        self.assertEqual(r['usable_cash_eur'], 10000)
        self.assertEqual(r['spoken_for_eur'], 7000)
        self.assertEqual(r['free_eur'], 3000)
        self.assertEqual(r['stage'], 'free_cash')
        self.assertEqual(r['spoken_for_eur'] + r['free_eur'], r['usable_cash_eur'])
        self.assertIn('€3,000 free', r['plain']['answer'])
        self.assertEqual(r['long_term_goals'][0]['goal_id'], 'G2')

    def test_reserve_shortfall_keeps_everything(self):
        b, p, q = base()
        b['assets'][0]['value_eur'] = 5000
        r = cp.plan(b, p, q)
        self.assertEqual(r['stage'], 'short')
        self.assertEqual(r['free_eur'], 0)
        self.assertEqual(r['shortfall_eur'], 2000)
        self.assertTrue(r['plain']['answer'].startswith('Keep all of it'))

    def test_competing_goals_cannot_reuse_cash(self):
        b, p, q = base()
        p['goals'].append({'goal_id': 'G3', 'description': 'Wedding', 'target_amount_eur': 5000,
                           'target_date': '2027-06-01', 'priority': 1})
        q['goal_kinds']['G3'] = 'cash_outlay'
        r = cp.plan(b, p, q)
        # Wedding is due first: 5000 then car gets the 2000 left, 2000 short.
        goal_lines = [l for l in r['allocations'] if l['kind'] == 'goal']
        self.assertEqual([l['goal_id'] for l in goal_lines], ['G3', 'G1'])
        self.assertEqual(goal_lines[1]['allocated_eur'], 2000)
        self.assertEqual(r['spoken_for_eur'], 10000)

    def test_earmark_for_goal_is_not_counted_twice(self):
        b, p, q = base()
        q['earmarks'] = [{'label': 'Car savings', 'amount_eur': 1500, 'goal_id': 'G1', 'source': 'user'}]
        r = cp.plan(b, p, q)
        self.assertEqual(r['spoken_for_eur'], 7000)
        self.assertEqual(r['free_eur'], 3000)

    def test_expensive_debt_comes_first_and_blocks_investing(self):
        b, p, q = base()
        b['liabilities'] = [{'type': 'credit_card', 'balance_eur': 2000, 'rate_pct': 18,
                             'monthly_payment_eur': 100}]
        q['debt_payments_in_expenses'] = True
        r = cp.plan(b, p, q)
        statuses = {c['id']: c['status'] for c in r['candidates']}
        self.assertEqual(statuses['repay_credit_card'], 'first_under_existing_rule')
        self.assertEqual(statuses['invest_long_term'], 'blocked')
        self.assertIn('18% a year', r['plain']['answer'])

    def test_debt_payments_must_be_confirmed(self):
        b, p, q = base()
        b['liabilities'] = [{'type': 'car_loan', 'rate_pct': 3, 'monthly_payment_eur': 200}]
        with self.assertRaisesRegex(cp.StateError, 'twice'):
            cp.plan(b, p, q)

    def test_restricted_cash_and_no_surplus(self):
        b, p, q = base()
        q['restricted'] = [{'label': 'Partner share', 'amount_eur': 4000, 'source': 'user'}]
        b['cash_flow']['monthly_discretionary'] = 2500
        r = cp.plan(b, p, q)
        self.assertEqual(r['usable_cash_eur'], 6000)
        self.assertEqual(r['stage'], 'stabilise')
        self.assertIn('Keep your cash where it is', r['plain']['answer'])

    def test_missing_policy_blocks_investing_only(self):
        b, p, q = base()
        q['ips_status'] = 'missing'
        r = cp.plan(b, p, q)
        self.assertEqual(r['free_eur'], 3000)
        invest = next(c for c in r['candidates'] if c['id'] == 'invest_long_term')
        self.assertEqual(invest['status'], 'blocked')

    def test_unknown_reserve_target_and_stale_inputs(self):
        b, p, q = base()
        b['cash_flow']['emergency_fund_target_months'] = None
        b['assets'][0]['as_of'] = '2026-01-01'
        r = cp.plan(b, p, q)
        self.assertEqual(r['stage'], 'reserve_unknown')
        self.assertTrue(r['stale_inputs'])
        self.assertTrue(any('safety cushion' in u for u in r['plain']['unknowns']))

    def test_blocked_component_returns_to_unallocated(self):
        b, p, q = base()
        r = cp.plan(b, p, q)
        out = cp.check(r, [{'use': 'invest_long_term', 'amount_eur': 2000,
                            'findings': findings('D-001', 'FAIL')}])
        self.assertEqual(out['counted_eur'], 0)
        self.assertEqual(out['unallocated_eur'], 3000)
        self.assertNotIn('Consider', out['opening'])

    def test_vetted_components_cannot_exceed_free_cash(self):
        r = cp.plan(*base())
        with self.assertRaisesRegex(cp.StateError, 'twice'):
            cp.check(r, [{'use': 'invest_long_term', 'amount_eur': 3500, 'findings': findings('D-001')}])
        ok = cp.check(r, [{'use': 'invest_long_term', 'amount_eur': 2000, 'findings': findings('D-001')}])
        self.assertEqual(ok['unallocated_eur'], 1000)
        self.assertIn('€1,000 as cash', ok['opening'])

    def test_cli_reads_profile(self):
        b, p, q = base()
        self.write('profile/balance_sheet.json', b)
        self.write('profile/profile.json', p)
        path = self.write('advice/q.json', q)
        out = self.run_tool('cash_plan.py', '--root', self.work, 'plan', '--inputs', path)
        self.assertIn('"free_eur": 3000.0', out.stdout)
