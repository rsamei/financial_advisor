"""The rubric in 04 and the tool that executes it must never drift apart.

Everything here is arithmetic stated twice - once in prose a human reads, once in code a machine
runs - and these tests are what keeps the two honest.
"""
import unittest

from support import ROOT, WorkspaceTest
from tools import decision_score as ds

RUBRIC = (ROOT / '.claude/skills/financial-advisor/04-decision-evaluation.md').read_text(
    encoding='utf-8')


def flat(text):
    return ' '.join(text.split())


def findings(**overrides):
    """A clean survivor: every gate passes, every dimension scored, an IPS exists."""
    return {
        'decision_id': 'D-001',
        'ips_version': '1.0',
        'gates': {
            'gate1_affordability': {'verdict': 'PASS', 'evidence': ['ledger:cash_flow.*']},
            'gate2_suitability': {'verdict': 'PASS', 'evidence': ['ips:max_equity_pct']},
            'gate3_legal_tax': {'verdict': 'PASS', 'evidence': ['07:capital-gains']},
            'gate4_evidence_coverage': {'verdict': 'SUPPORTED', 'evidence': ['Q-2026-09-20-macro']},
        },
        'dimensions': {'goal_contribution': 80, 'risk_adjusted_robustness': 80,
                       'cost_and_tax_efficiency': 80, 'evidence_strength': 80,
                       'simplicity': 80, 'reversibility': 80},
    } | overrides


def gates(**overrides):
    return findings()['gates'] | overrides


class WeightContractTests(unittest.TestCase):
    def test_the_weights_sum_to_one_hundred(self):
        self.assertEqual(sum(ds.WEIGHTS.values()), 100)

    def test_every_weight_in_the_rubric_matches_the_tool(self):
        for name, weight in (('Contribution to stated goals', 25),
                             ('Risk-adjusted robustness', 25),
                             ('Cost and tax efficiency', 15),
                             ('Evidence strength', 15),
                             ('Simplicity and maintainability', 10),
                             ('Reversibility and optionality', 10)):
            with self.subTest(dimension=name):
                self.assertIn(f'| {name} | {weight} |', RUBRIC)
        self.assertEqual(sorted(ds.WEIGHTS.values(), reverse=True), [25, 25, 15, 15, 10, 10])

    def test_the_six_dimensions_are_exactly_these(self):
        self.assertEqual(set(ds.WEIGHTS), {
            'goal_contribution', 'risk_adjusted_robustness', 'cost_and_tax_efficiency',
            'evidence_strength', 'simplicity', 'reversibility'})


class GateOrderTests(unittest.TestCase):
    def test_the_four_gates_are_named_in_order_in_both_places(self):
        self.assertEqual(ds.GATES, ('gate1_affordability', 'gate2_suitability',
                                    'gate3_legal_tax', 'gate4_evidence_coverage'))
        positions = [RUBRIC.index(f'**{n}**') for n in
                     ('1 Affordability & liquidity', '2 Suitability', '3 Legal, tax & compliance',
                      '4 Evidence coverage & freshness')]
        self.assertEqual(positions, sorted(positions), 'the gates are out of order in 04')

    def test_gates_run_before_scoring_in_the_rubric_text(self):
        self.assertLess(RUBRIC.index('## Gate-before-score'), RUBRIC.index('## Weighted score'))


class GatedTests(WorkspaceTest):
    def test_a_failed_gate_gives_band_gated_and_an_empty_score(self):
        result = ds.score(findings(gates=gates(
            gate1_affordability={'verdict': 'FAIL',
                                 'evidence': ['ledger:cash_flow.monthly_net_income as_of 2026-09-20']})))
        self.assertEqual(result['band'], 'gated')
        self.assertIsNone(result['score'])
        self.assertIsNone(result['raw_score'])
        self.assertEqual(result['failed_gates'], ['gate1_affordability'])

    def test_a_fail_without_evidence_is_refused(self):
        with self.assertRaisesRegex(ds.StateError, 'must quote its evidence'):
            ds.score(findings(gates=gates(
                gate2_suitability={'verdict': 'FAIL', 'evidence': []})))

    def test_gate_four_can_never_fail(self):
        with self.assertRaisesRegex(ds.StateError, 'gate4 never FAILs'):
            ds.score(findings(gates=gates(
                gate4_evidence_coverage={'verdict': 'FAIL', 'evidence': ['x']})))
        self.assertIn('**never** - absence of evidence is `UNDETERMINED`', RUBRIC)

    def test_every_gate_must_be_reported(self):
        incomplete = findings()
        del incomplete['gates']['gate3_legal_tax']
        with self.assertRaisesRegex(ds.StateError, 'gate3_legal_tax: missing'):
            ds.score(incomplete)


class ScoreTests(WorkspaceTest):
    def test_a_clean_survivor_scores_the_weighted_sum(self):
        result = ds.score(findings())
        self.assertEqual(result['raw_score'], 80)       # every dimension 80, weights sum to 100
        self.assertEqual(result['score'], 80)
        self.assertEqual(result['band'], 'do_now')
        self.assertFalse(result['capped'])

    def test_the_weighted_sum_actually_weights(self):
        # 100 on the two 25-weight dimensions, 0 elsewhere -> 50.
        result = ds.score(findings(dimensions={
            'goal_contribution': 100, 'risk_adjusted_robustness': 100,
            'cost_and_tax_efficiency': 0, 'evidence_strength': 0,
            'simplicity': 0, 'reversibility': 0}))
        self.assertEqual(result['raw_score'], 50)
        self.assertEqual(result['band'], 'park')

    def test_bands_match_the_rubric_thresholds(self):
        self.assertEqual(ds.band_for(72), 'do_now')
        self.assertEqual(ds.band_for(71), 'do_scoped')
        self.assertEqual(ds.band_for(58), 'do_scoped')
        self.assertEqual(ds.band_for(57), 'park')
        self.assertEqual(ds.band_for(45), 'park')
        self.assertEqual(ds.band_for(44), 'reject')
        for fragment in ('| 72-100 | `do_now` |', '| 58-71 | `do_scoped` |',
                         '| 45-57 | `park` |', '| 0-44 | `reject` |'):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, RUBRIC)

    def test_a_park_demands_a_watch_trigger_and_a_scoped_demands_staging(self):
        parked = ds.score(findings(dimensions={k: 50 for k in ds.WEIGHTS}))
        self.assertEqual(parked['band'], 'park')
        self.assertTrue(parked['requires_watch_trigger'])
        scoped = ds.score(findings(dimensions={k: 65 for k in ds.WEIGHTS}))
        self.assertEqual(scoped['band'], 'do_scoped')
        self.assertTrue(scoped['staging_required'])


class CapTests(WorkspaceTest):
    def test_the_cap_table_matches_the_rubric(self):
        for token, ceiling in (('coverage=UNDETERMINED', 55), ('liquidity=FLAG', 60),
                               ('concentration=FLAG', 65), ('ips=missing', 65),
                               ('tax=FLAG', 70)):
            with self.subTest(token=token):
                self.assertEqual(ds.CAPS[token], ceiling)
                self.assertIn(f'`cap:{ceiling} {token}`', RUBRIC)
        self.assertEqual(ds.CAPS['behaviour'], 60)

    def test_undetermined_coverage_caps_at_fifty_five_and_the_raw_score_survives(self):
        result = ds.score(findings(gates=gates(
            gate4_evidence_coverage={'verdict': 'UNDETERMINED', 'evidence': ['Q-x']})))
        self.assertEqual(result['raw_score'], 80)
        self.assertEqual(result['score'], 55)
        self.assertEqual(result['band'], 'park')
        self.assertTrue(result['capped'])
        self.assertIn('cap:55 coverage=UNDETERMINED', result['caps'])

    def test_the_lowest_cap_wins(self):
        result = ds.score(findings(
            ips_version=None,                                    # cap 65
            increases_largest_concentration=True,                # cap 65
            gates=gates(gate1_affordability={'verdict': 'FLAG',  # cap 60
                                             'evidence': ['ledger:cash_flow.*']})))
        self.assertEqual(result['ceiling'], 60)
        self.assertEqual(result['score'], 60)

    def test_a_missing_ips_caps_at_sixty_five(self):
        result = ds.score(findings(ips_version=None))
        self.assertEqual(result['score'], 65)
        self.assertIn('cap:65 ips=missing', result['caps'])

    def test_a_behavioural_cap_needs_a_recorded_card(self):
        with self.assertRaisesRegex(ds.StateError, 'needs a recorded evidence card'):
            ds.score(findings(behavioural_flags=[{'tendency': 'chasing'}]))
        result = ds.score(findings(behavioural_flags=[
            {'tendency': 'chasing', 'evidence_key': 'EV-1234abcd'}]))
        self.assertEqual(result['score'], 60)
        self.assertIn('cap:60 behaviour=chasing', result['caps'])

    def test_an_unknown_tendency_is_refused(self):
        with self.assertRaisesRegex(ds.StateError, 'is not one of'):
            ds.score(findings(behavioural_flags=[
                {'tendency': 'greed', 'evidence_key': 'EV-1234abcd'}]))


class ViewQuarantineTests(WorkspaceTest):
    """A scenario view is opinion. It must not reach the machinery through a citation field."""

    def test_a_view_key_in_gate_evidence_is_refused(self):
        with self.assertRaisesRegex(ds.StateError, 'cites a scenario view'):
            ds.score(findings(gates=gates(
                gate2_suitability={'verdict': 'PASS', 'evidence': ['VW-20260920-01']})))

    def test_a_view_key_is_refused_even_on_a_failing_gate(self):
        with self.assertRaisesRegex(ds.StateError, 'cites a scenario view'):
            ds.score(findings(gates=gates(
                gate1_affordability={'verdict': 'FAIL', 'evidence': ['VW-20260920-01']})))


class BlockerMappingTests(WorkspaceTest):
    def test_the_mapping_table_matches_the_rubric(self):
        self.assertEqual(ds.BLOCKER_MAP, {
            'cannot_afford': 'gate1_affordability',
            'breaches_limit': 'gate2_suitability',
            'not_purchasable': 'gate3_legal_tax',
            'mnpi': 'gate3_legal_tax',
            'evidence_absent': 'gate4_evidence_coverage',
            'behavioural': None})
        for category in ds.BLOCKER_MAP:
            with self.subTest(category=category):
                self.assertIn(f'`{category}`', RUBRIC)

    def test_a_carded_blocker_in_the_table_applies(self):
        result = ds.score(findings(skeptic_objections=[
            {'severity': 'BLOCKER', 'category': 'cannot_afford', 'evidence_key': 'EV-1234abcd'}]))
        self.assertTrue(result['blockers'][0]['applies'])
        self.assertEqual(result['blockers'][0]['gate'], 'gate1_affordability')

    def test_a_blocker_backed_only_by_reasoning_is_shown_not_applied(self):
        result = ds.score(findings(skeptic_objections=[
            {'severity': 'BLOCKER', 'category': 'cannot_afford'}]))
        self.assertFalse(result['blockers'][0]['applies'])
        self.assertIn('not a recorded card', result['unapplied_blockers'][0]['note'])

    def test_an_unmapped_blocker_has_no_mechanical_effect(self):
        result = ds.score(findings(skeptic_objections=[
            {'severity': 'BLOCKER', 'category': 'i_have_a_bad_feeling',
             'evidence_key': 'EV-1234abcd'}]))
        self.assertFalse(result['blockers'][0]['applies'])
        self.assertEqual(result['score'], 80)


class RubricTextTests(unittest.TestCase):
    def test_the_do_nothing_option_is_mandatory(self):
        self.assertIn('"Do nothing" is evaluated as a candidate every time', flat(RUBRIC))

    def test_the_stress_scenarios_are_listed(self):
        for fragment in ('equities -30 %', 'rates +2 pp', 'crypto -70 %',
                         'no income for 6 months'):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, RUBRIC)

    def test_insider_signal_is_confined_to_one_dimension(self):
        self.assertIn('may move the **Evidence\nstrength** dimension and nothing else', RUBRIC)

    def test_the_brief_template_carries_the_disclosure_and_the_first_check(self):
        self.assertIn('Decisive first check', RUBRIC)
        self.assertIn('This is not licensed financial advice', RUBRIC)

    def test_the_rubric_is_no_longer_a_stub(self):
        self.assertNotIn('Not yet populated', RUBRIC)


class CliTests(WorkspaceTest):
    def test_score_writes_a_gate_record(self):
        path = self.write('findings.json', findings())
        out = self.work / 'gates.json'
        result = self.run_tool('decision_score.py', 'score', '--findings', path, '--out', out)
        self.assertIn('G1 PASS', result.stdout)
        self.assertIn('band: do_now', result.stdout)
        import json
        self.assertEqual(json.loads(out.read_text(encoding='utf-8'))['score'], 80)

    def test_a_gated_decision_prints_no_number(self):
        path = self.write('findings.json', findings(gates=gates(
            gate3_legal_tax={'verdict': 'FAIL', 'evidence': ['07:priips no KID']})))
        result = self.run_tool('decision_score.py', 'score', '--findings', path)
        self.assertIn('band: gated (no score', result.stdout)
        self.assertNotIn('score: ', result.stdout)

    def test_a_bad_findings_file_fails_without_a_traceback(self):
        path = self.write('findings.json', {'decision_id': 'D-001'})
        result = self.run_tool('decision_score.py', 'score', '--findings', path, ok=False)
        self.assertNotIn('Traceback', result.stderr)
        self.assertIn('gate1_affordability: missing', result.stderr)
