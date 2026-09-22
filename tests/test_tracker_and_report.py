"""The tracker's frozen header and status transitions, and the report's last-gate checks."""
import json

from support import ROOT, WorkspaceTest, decision_row
from tools import decision_score as ds
from tools import report_check as rc
from tools import tracker as tr


class TrackerTests(WorkspaceTest):
    def seed(self, **overrides):
        return tr.add(self.work, decision_row(**overrides))

    def test_the_tracker_is_created_from_the_tracked_template(self):
        self.seed()
        header = (self.work / 'decision_tracker.csv').read_text(encoding='utf-8').splitlines()[0]
        template = (ROOT / 'decision_tracker.template.csv').read_text(encoding='utf-8').strip()
        self.assertEqual(header, template)

    def test_an_extra_column_is_refused_not_absorbed(self):
        with self.assertRaisesRegex(tr.StateError, 'unknown column'):
            tr.add(self.work, decision_row(conviction='high'))

    def test_ids_are_allocated_in_sequence_and_never_reused(self):
        self.seed(decision_id='D-001')
        self.assertEqual(tr.next_id(tr.load(self.work)), 'D-002')
        second = tr.add(self.work, decision_row(decision_id=''))
        self.assertEqual(second['decision_id'], 'D-002')
        with self.assertRaisesRegex(tr.StateError, 'already exists'):
            tr.add(self.work, decision_row(decision_id='D-002'))

    def test_a_duplicate_id_exits_six(self):
        self.seed(decision_id='D-001')
        path = self.write('row.json', decision_row(decision_id='D-001'))
        result = self.run_tool('tracker.py', '--root', self.work, 'add', '--row', path, ok=False)
        self.assertIn('already exists', result.stderr)

    def test_status_transitions_are_enforced(self):
        self.seed(decision_id='D-001', status='draft')
        with self.assertRaisesRegex(tr.StateError, 'not a valid transition'):
            tr.update(self.work, 'D-001', status='executed')
        tr.update(self.work, 'D-001', status='vetted')
        tr.update(self.work, 'D-001', status='accepted')
        row = tr.update(self.work, 'D-001', status='executed')
        self.assertEqual(row['status'], 'executed')

    def test_a_terminal_row_is_never_revived(self):
        self.seed(decision_id='D-001', status='draft')
        tr.update(self.work, 'D-001', status='declined')
        with self.assertRaisesRegex(tr.StateError, 'terminal'):
            tr.update(self.work, 'D-001', status='vetted')

    def test_a_parked_row_stays_visible_in_the_listing(self):
        self.seed(decision_id='D-001', status='draft')
        tr.update(self.work, 'D-001', status='parked')
        rows = tr.listing(tr.load(self.work), open_only=True)
        self.assertEqual([row['decision_id'] for row in rows], ['D-001'])

    def test_a_gated_row_carries_no_score(self):
        with self.assertRaisesRegex(tr.StateError, 'empty score'):
            tr.add(self.work, decision_row(gate1='FAIL', band='gated', score='70'))
        row = tr.add(self.work, decision_row(gate1='FAIL', band='gated', score=''))
        self.assertEqual(row['band'], 'gated')

    def test_notes_accumulate_tokens_rather_than_columns(self):
        self.seed(decision_id='D-001', notes='cap:55 coverage=UNDETERMINED')
        row = tr.update(self.work, 'D-001', note='advice:AR-20260920-01')
        self.assertEqual(row['notes'], 'cap:55 coverage=UNDETERMINED advice:AR-20260920-01')


class GateRecordToRowTests(WorkspaceTest):
    """A tracker row is built from the gate record, so the two can never disagree."""

    def record(self, **overrides):
        findings = {
            'decision_id': 'D-007', 'ips_version': '1.0',
            'gates': {
                'gate1_affordability': {'verdict': 'PASS', 'evidence': ['ledger:cash_flow.*']},
                'gate2_suitability': {'verdict': 'FLAG', 'evidence': ['ips:max_equity_pct']},
                'gate3_legal_tax': {'verdict': 'PASS', 'evidence': ['07:kid']},
                'gate4_evidence_coverage': {'verdict': 'UNDETERMINED', 'evidence': ['Q-x']},
            },
            'dimensions': {k: 80 for k in ds.WEIGHTS},
        } | overrides
        return ds.score(findings)

    def test_the_row_mirrors_the_gate_record(self):
        row = tr.from_gate_record(self.record(), question='Move idle cash?',
                                  decision_type='cash', amount_eur=10000)
        self.assertEqual(row['gate2'], 'FLAG')
        self.assertEqual(row['gate4'], 'UNDETERMINED')
        self.assertEqual(row['score'], '55')                 # capped by coverage
        self.assertEqual(row['band'], 'park')
        self.assertEqual(row['amount_eur'], '10000.00')
        self.assertIn('cap:55 coverage=UNDETERMINED', row['notes'])
        tr.add(self.work, row)                                # and it validates

    def test_a_gated_record_becomes_a_draft_row_with_no_score(self):
        record = self.record(gates={
            'gate1_affordability': {'verdict': 'FAIL', 'evidence': ['ledger:emergency_months 1.2']},
            'gate2_suitability': {'verdict': 'PASS', 'evidence': ['ips:x']},
            'gate3_legal_tax': {'verdict': 'PASS', 'evidence': ['07:x']},
            'gate4_evidence_coverage': {'verdict': 'SUPPORTED', 'evidence': ['Q-x']},
        })
        row = tr.from_gate_record(record, question='Buy now?', decision_type='buy')
        self.assertEqual(row['band'], 'gated')
        self.assertEqual(row['score'], '')
        self.assertEqual(row['status'], 'draft')
        tr.add(self.work, row)


class ReportCheckTests(WorkspaceTest):
    GOOD = """# Action list AR-20260920-01

## Your situation
| Net worth | 60000 | 2026-09-20 | profile_check.py |

## Actions

### HIGH - this week
- **D-001 Top up the emergency fund** - 3000 from cash
  G1 PASS | G2 PASS | G3 PASS | G4 SUPPORTED | score 80
  Why: essential expenses 2500 per month, ledger:cash_flow.monthly_essential_expenses

This is not licensed financial advice. Nothing here has been executed; you decide and you act.
"""

    def report(self, text):
        path = self.work / 'report.md'
        path.write_text(text, encoding='utf-8')
        return path

    def test_a_well_cited_report_passes(self):
        result = rc.check(self.work, self.GOOD)
        self.assertEqual(result['hard_failures'], {})
        self.assertTrue(result['ok'], result['problems'])

    def test_an_unreferenced_number_is_caught(self):
        text = self.GOOD.replace('| profile_check.py |', '| |')
        result = rc.check(self.work, text)
        self.assertTrue(result['problems']['unreferenced_numbers'])
        self.assertFalse(result['ok'])
        self.assertTrue(result['ok_non_strict'])      # a draft may be incomplete; --strict may not

    def test_a_missing_disclosure_is_a_hard_failure(self):
        text = self.GOOD.replace(
            'This is not licensed financial advice. Nothing here has been executed; you decide '
            'and you act.\n', '')
        result = rc.check(self.work, text)
        self.assertIn('missing_disclosure', result['hard_failures'])
        self.assertFalse(result['ok_non_strict'])

    def test_an_action_without_a_gate_line_is_a_hard_failure(self):
        text = self.GOOD.replace('  G1 PASS | G2 PASS | G3 PASS | G4 SUPPORTED | score 80\n', '')
        result = rc.check(self.work, text)
        self.assertIn('actions_without_a_gate_record', result['hard_failures'])

    def test_a_view_on_a_gate_line_is_a_hard_failure(self):
        text = self.GOOD.replace('G4 SUPPORTED | score 80',
                                 'G4 SUPPORTED | score 80 | VW-20260920-01')
        result = rc.check(self.work, text)
        self.assertIn('views_cited_as_gate_evidence', result['hard_failures'])

    def test_an_evidence_key_that_is_not_a_card_is_caught(self):
        (self.work / 'market' / 'evidence' / 'rates').mkdir(parents=True)
        (self.work / 'market' / 'evidence' / 'rates' / 'EV-1111aaaa.json').write_text(
            '{}', encoding='utf-8')
        text = self.GOOD.replace('profile_check.py', 'EV-2222bbbb')
        result = rc.check(self.work, text)
        self.assertEqual(result['problems']['evidence_keys_not_found'][0]['key'], 'EV-2222bbbb')

    def test_a_query_id_that_is_not_recorded_is_caught(self):
        (self.work / 'market').mkdir(parents=True, exist_ok=True)
        (self.work / 'market' / 'queries.json').write_text(
            json.dumps({'Q-2026-09-20-macro': {}}), encoding='utf-8')
        text = self.GOOD.replace('profile_check.py', 'Q-not-a-real-query')
        result = rc.check(self.work, text)
        self.assertEqual(result['problems']['query_ids_not_found'][0]['key'], 'Q-not-a-real-query')

    def test_strict_mode_fails_on_an_unreferenced_number_and_normal_mode_does_not(self):
        path = self.report(self.GOOD.replace('| profile_check.py |', '| |'))
        self.run_tool('report_check.py', '--root', self.work, 'check', '--report', path)
        self.run_tool('report_check.py', '--root', self.work, 'check', '--report', path,
                      '--strict', ok=False)

    def test_the_disclosure_wording_matches_the_guardrails_file(self):
        guardrails = (ROOT / '.claude/skills/financial-advisor/08-behavioural-guardrails.md'
                      ).read_text(encoding='utf-8')
        self.assertIn(rc.DISCLOSURE, guardrails)


class AdviseContractTests(WorkspaceTest):
    def setUp(self):
        super().setUp()
        self.text = (ROOT / '.claude/commands/advise.md').read_text(encoding='utf-8')

    def flat(self):
        return ' '.join(self.text.split())

    def test_advise_delegates_gating_to_decide_and_says_so(self):
        self.assertIn('/advise` reimplements none of it', self.text)
        self.assertIn('origin: advise', self.text)

    def test_all_six_candidate_passes_are_named(self):
        for pass_name in ('Household hygiene', 'IPS drift and rebalancing', 'Cash deployment',
                          'Goal funding', 'Opportunities and threats', 'Do nothing'):
            with self.subTest(pass_name=pass_name):
                self.assertIn(pass_name, self.text)

    def test_the_do_nothing_pass_is_mandatory_and_reported_either_way(self):
        self.assertIn('appears in the report whether it wins or loses', self.flat())

    def test_agents_never_receive_a_balance(self):
        self.assertIn('Agents receive ratios, never balances', self.text)
        self.assertIn('12 % of investable assets", not "this is 12 000 EUR', self.flat())

    def test_it_stops_when_the_profile_is_incomplete(self):
        self.assertIn('Require a Profile', self.text)
        self.assertIn('point to `/setup`', self.text)

    def test_no_ips_mode_makes_writing_one_the_first_action(self):
        self.assertIn('cap:65 ips=missing', self.text)
        self.assertIn('write an IPS" becomes the first HIGH action', self.text)

    def test_a_quick_run_is_never_presented_as_a_full_one(self):
        self.assertIn('Never present a `--quick` run as a full one', self.text)

    def test_the_report_check_is_run_strictly(self):
        self.assertIn('report_check.py check --report advice/', self.text)
        self.assertIn('--strict', self.text)
        self.assertIn('never by deleting the number', self.text)
