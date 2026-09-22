"""Reader summaries must retain computed restrictions and the do-nothing comparison."""
from support import WorkspaceTest
from tools import plain_report as pr, decision_score as ds, report_check


def item(key='D-001', nothing=False):
    return {'title': 'Keep the current plan' if nothing else 'Proposed change',
            'why': 'Protect the goal', 'next_check': 'Confirm the missing information',
            'is_do_nothing': nothing,
            'findings': {'decision_id': key, 'ips_version': '1.0',
                         'gates': {name: {'verdict': 'SUPPORTED' if i == 3 else 'PASS',
                                          'evidence': ['ledger:cash_flow.*']}
                                   for i, name in enumerate(ds.GATES)},
                         'dimensions': {k: 85 for k in ds.WEIGHTS}}}


class SummaryTests(WorkspaceTest):
    def test_summary_recomputes_restrictions_and_passes_strict_check(self):
        action = item()
        action['findings']['gates'][ds.GATES[3]]['verdict'] = 'UNDETERMINED'
        rendered = pr.render({'decisions': [action, item('D-002', True)]})
        self.assertIn('Wait; do not act yet', rendered)
        self.assertIn('Whether the evidence is sufficient: unresolved', rendered)
        self.assertIn('If you change nothing', rendered)
        self.assertIn('<details>', rendered)
        self.assertTrue(report_check.check(self.work, rendered)['ok'])

    def test_failed_check_cannot_be_summarised_as_approval(self):
        action = item()
        action['findings']['gates'][ds.GATES[0]]['verdict'] = 'FAIL'
        rendered = pr.render({'decisions': [action, item('D-002', True)]})
        self.assertIn('Do not proceed; a required check failed', rendered)
        self.assertNotIn('D-001: score', rendered)

    def test_restrictions_on_fourth_card_remain_visible(self):
        actions = [item(f'D-00{i}', i == 1) for i in range(1, 5)]
        actions[3]['findings']['gates'][ds.GATES[2]]['verdict'] = 'FLAG'
        text = pr.render({'decisions': actions})
        front = text.split('## Supporting checks')[0]
        self.assertNotIn('D-00', front)
        self.assertIn('Legal and tax requirements: unresolved', front)

    def test_missing_do_nothing_is_rejected(self):
        with self.assertRaisesRegex(pr.StateError, 'do-nothing'):
            pr.render({'decisions': [item()]})

    def test_embedded_markup_cannot_hide_the_verdict(self):
        action = item()
        action['title'] = '</details><script>hide()</script>'
        text = pr.render({'decisions': [action, item('D-002', True)]})
        self.assertNotIn('<script>', text)


class CashAnswerTests(WorkspaceTest):
    def bundle(self, **cash):
        from tests.test_cash_plan import base
        from tools import cash_plan as cp
        return cp, {'decisions': [item(), item('D-002', True)], 'cash': cash or {'plan': cp.plan(*base())}}

    def test_cash_answer_leads_and_passes_strict_check(self):
        cp, bundle = self.bundle()
        text = pr.render(bundle)
        self.assertLess(text.index('## Your answer'), text.index('## What needs attention'))
        self.assertIn('€3,000 free', text)
        self.assertTrue(report_check.check(self.work, text)['ok'])

    def test_counted_blocked_component_is_refused(self):
        from tests.test_cash_plan import base
        from tools import cash_plan as cp
        plan = cp.plan(*base())
        forged = {'kind': 'cash_plan_check', 'counted_eur': 1000, 'opening': 'Consider investing',
                  'components': [{'band': 'gated', 'counted_eur': 1000}], 'waiting': []}
        with self.assertRaisesRegex(pr.StateError, 'blocked'):
            pr.render({'decisions': [item(), item('D-002', True)], 'cash': {'plan': plan, 'check': forged}})
