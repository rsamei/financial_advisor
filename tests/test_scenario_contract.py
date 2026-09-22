"""The opinion layer: quarantined from the machinery, immutable, and scored against reality."""
import json

from support import ROOT, WorkspaceTest
from tools import decision_score as ds
from tools import report_check as rc
from tools import views

REFERENCE = (ROOT / '.claude/skills/financial-advisor/12-scenario-view.md').read_text(
    encoding='utf-8')


def flat(text):
    return ' '.join(text.split())


def view(**overrides):
    return {
        'view_id': 'VW-20260920-01',
        'kind': 'view',
        'subject': 'SYN',
        'asset_class': 'equity',
        'created_on': '2026-03-20',
        'horizon_end': '2026-09-20',
        'market_query_id': 'Q-2026-03-20-asset-syn',
        'supersedes': None,
        'base_rate': {'window': '6m', 'n_windows': 1200, 'p10': -0.18, 'p50': 0.04, 'p90': 0.25,
                      'ev_key': 'EV-1111aaaa'},
        'scenarios': [
            {'name': 'bear', 'probability': 30, 'range': [-0.5, -0.1],
             'assumptions': ['margins compress', 'rates stay high'],
             'evidence_keys': ['EV-1111aaaa'], 'signposts': ['a negative revision in the next print']},
            {'name': 'base', 'probability': 50, 'range': [-0.1, 0.15],
             'assumptions': ['earnings grow with nominal GDP', 'no rate shock'],
             'evidence_keys': ['EV-2222bbbb'], 'signposts': ['revisions stay flat']},
            {'name': 'bull', 'probability': 20, 'range': [0.15, 0.6],
             'assumptions': ['multiple expansion resumes', 'rates fall'],
             'evidence_keys': ['EV-3333cccc'], 'signposts': ['a cut priced before June']},
        ],
        'what_would_change_this': 'a print above 3 % on the next HICP release',
        'confidence': 'low',
        'reviewer_kind': 'self_review',
        'disclosure': views.DISCLOSURE,
    } | overrides


class ValidationTests(WorkspaceTest):
    def test_a_well_formed_view_validates(self):
        self.assertEqual(views.validate(view())['view_id'], 'VW-20260920-01')

    def test_probabilities_must_sum_to_exactly_one_hundred(self):
        broken = view()
        broken['scenarios'][0]['probability'] = 31
        with self.assertRaisesRegex(views.StateError, 'sum to 101, not 100'):
            views.validate(broken)

    def test_exactly_three_scenarios_named_bear_base_bull(self):
        two = view()
        two['scenarios'] = two['scenarios'][:2]
        with self.assertRaisesRegex(views.StateError, 'exactly three scenarios'):
            views.validate(two)
        renamed = view()
        renamed['scenarios'][0]['name'] = 'catastrophe'
        with self.assertRaisesRegex(views.StateError, 'scenario names must be'):
            views.validate(renamed)

    def test_the_disclosure_must_be_present_verbatim(self):
        with self.assertRaisesRegex(views.StateError, 'disclosure line must be present'):
            views.validate(view(disclosure='this is just my opinion'))
        with self.assertRaisesRegex(views.StateError, 'disclosure line must be present'):
            views.validate(view(disclosure=None))

    def test_every_scenario_needs_signposts(self):
        no_signposts = view()
        no_signposts['scenarios'][1]['signposts'] = []
        with self.assertRaisesRegex(views.StateError, 'signposts are required'):
            views.validate(no_signposts)

    def test_an_assumption_with_no_evidence_must_be_declared_unsupported(self):
        guess = view()
        guess['scenarios'][2]['evidence_keys'] = []
        with self.assertRaisesRegex(views.StateError, 'unsupported_assumptions'):
            views.validate(guess)
        guess['scenarios'][2]['unsupported_assumptions'] = ['multiple expansion resumes']
        self.assertTrue(views.validate(guess))

    def test_a_base_rate_is_required(self):
        with self.assertRaisesRegex(views.StateError, 'base_rate is required'):
            views.validate(view(base_rate=None))

    def test_long_horizons_and_crypto_are_low_confidence_by_rule(self):
        with self.assertRaisesRegex(views.StateError, 'beyond\nsix months is low confidence'
                                                      .replace('\n', ' ')):
            views.validate(view(confidence='high', created_on='2026-01-01',
                                horizon_end='2027-01-01'))
        with self.assertRaisesRegex(views.StateError, 'crypto views are'):
            views.validate(view(confidence='medium', asset_class='crypto'))
        # Six months or less, non-crypto: medium is allowed.
        self.assertTrue(views.validate(view(confidence='medium')))


class ImmutabilityTests(WorkspaceTest):
    def test_a_view_is_written_once(self):
        views.record(self.work, view())
        with self.assertRaisesRegex(views.StateError, 'already exists'):
            views.record(self.work, view(confidence='medium'))

    def test_a_changed_mind_is_a_new_view_that_supersedes(self):
        views.record(self.work, view())
        second = views.record(self.work, view(view_id='VW-20260921-01',
                                              supersedes='VW-20260920-01'))
        self.assertTrue(second.is_file())
        stored = json.loads(second.read_text(encoding='utf-8'))
        self.assertEqual(stored['supersedes'], 'VW-20260920-01')
        self.assertTrue(views.view_path(self.work, 'VW-20260920-01').is_file())


class QuarantineTests(WorkspaceTest):
    """The whole point: an opinion cannot become evidence by being cited."""

    def test_a_view_key_in_gate_evidence_is_refused_by_the_scorer(self):
        findings = {
            'decision_id': 'D-001', 'ips_version': '1.0',
            'gates': {
                'gate1_affordability': {'verdict': 'PASS', 'evidence': ['ledger:cash_flow.*']},
                'gate2_suitability': {'verdict': 'PASS', 'evidence': ['VW-20260920-01']},
                'gate3_legal_tax': {'verdict': 'PASS', 'evidence': ['07:x']},
                'gate4_evidence_coverage': {'verdict': 'SUPPORTED', 'evidence': ['Q-x']},
            },
            'dimensions': {k: 80 for k in ds.WEIGHTS},
        }
        with self.assertRaisesRegex(ds.StateError, 'cites a scenario view'):
            ds.score(findings)

    def test_a_view_on_a_gate_line_fails_the_report_check(self):
        report = ('- **D-001 Buy** - 1 000\n'
                  '  G1 PASS | G2 PASS | G3 PASS | G4 SUPPORTED | VW-20260920-01\n\n'
                  'This is not licensed financial advice.\n')
        result = rc.check(self.work, report)
        self.assertIn('views_cited_as_gate_evidence', result['hard_failures'])

    def test_a_view_may_appear_in_a_report_away_from_the_gate_line(self):
        report = ('- **D-001 Buy** - 1 000\n'
                  '  G1 PASS | G2 PASS | G3 PASS | G4 SUPPORTED | score 80\n\n'
                  '## View (model opinion - not evidence)\n'
                  'VW-20260920-01: base case 50 %.\n\n'
                  'This is not licensed financial advice.\n')
        result = rc.check(self.work, report)
        self.assertEqual(result['problems']['views_cited_as_gate_evidence'], [])

    def test_a_view_key_traces_the_numbers_in_its_own_block(self):
        """A scenario probability comes from the recorded view, and from nowhere else.

        Found by the end-to-end smoke: with `VW-` rejected as provenance entirely, a correctly
        written view block failed --strict for quoting its own probabilities. Accepting it here
        while still refusing it on a gate line is what keeps "traceable" and "evidence" distinct.
        """
        report = ('## View (model opinion - not evidence)\n'
                  'VW-20260920-01: base case 50 %.\n\n'
                  'This is not licensed financial advice.\n')
        result = rc.check(self.work, report)
        self.assertEqual(result['problems']['unreferenced_numbers'], [])
        self.assertTrue(result['ok'])

    def test_a_number_with_no_provenance_at_all_still_fails(self):
        report = ('Some asset will return 12 % next year.\n\n'
                  'This is not licensed financial advice.\n')
        result = rc.check(self.work, report)
        self.assertTrue(result['problems']['unreferenced_numbers'])

    def test_the_quarantine_is_stated_in_the_reference(self):
        for rule in ('never satisfies a coverage condition', 'never enters a gate record',
                     'never moves a score dimension', 'never originates an action'):
            with self.subTest(rule=rule):
                self.assertIn(rule, REFERENCE)


class ScoringTests(WorkspaceTest):
    def test_late_review_uses_original_endpoint_and_repeats_do_not_add_samples(self):
        views.record(self.work, view())
        self.corpus(self.price('2026-03-20', 100.0), self.price('2026-09-20', 105.0),
                    self.price('2026-10-20', 150.0))
        first = views.score(self.work, 'VW-20260920-01', as_of='2026-10-20')
        second = views.score(self.work, 'VW-20260920-01', as_of='2026-10-21')
        self.assertEqual(first['realised_return'], .05)
        self.assertEqual(first, second)
        self.assertEqual(views.calibration(self.work)['scored_views'], 1)

    def test_existing_score_repairs_a_missing_log_without_changing_the_score(self):
        views.record(self.work, view())
        self.corpus(self.price('2026-03-20', 100.0), self.price('2026-09-20', 105.0))
        first = views.score(self.work, 'VW-20260920-01', as_of='2026-09-20')
        (self.work / views.SCORES_LOG).unlink()
        second = views.score(self.work, 'VW-20260920-01', as_of='2026-10-20')
        self.assertEqual(first, second)
        self.assertEqual(views.calibration(self.work)['scored_views'], 1)

    def test_shared_boundary_has_one_order_independent_result(self):
        scenarios = view()['scenarios']
        self.assertEqual(views.which_scenario(scenarios, -.1), 'base')
        self.assertEqual(views.which_scenario(list(reversed(scenarios)), -.1), 'base')

    def test_overlapping_ranges_and_nonfinite_values_fail(self):
        broken = view()
        broken['scenarios'][0]['range'][1] = .1
        with self.assertRaisesRegex(views.StateError, 'overlap'):
            views.validate(broken)
        broken = view()
        broken['scenarios'][0]['range'][0] = float('nan')
        with self.assertRaisesRegex(views.StateError, 'range'):
            views.validate(broken)

    def corpus(self, *records):
        path = self.work / 'market' / 'observations.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({r['key']: r for r in records}), encoding='utf-8')

    def price(self, asof, value):
        return {'key': f'stooq:SYN@{asof}', 'symbol': 'SYN', 'asof': asof, 'value': value,
                'kind': 'price', 'provider': 'stooq', 'tier': 'A'}

    def test_brier_is_computed_correctly(self):
        scenarios = view()['scenarios']          # 30 / 50 / 20
        # base materialises: (0.3-0)^2 + (0.5-1)^2 + (0.2-0)^2 = 0.09 + 0.25 + 0.04 = 0.38, /3
        self.assertEqual(views.brier(scenarios, 'base'), round(0.38 / 3, 4))
        # a perfect, certain view scores 0
        certain = [dict(s, probability=100 if s['name'] == 'bear' else 0) for s in scenarios]
        self.assertEqual(views.brier(certain, 'bear'), 0.0)
        # a uniform guess sits near 0.22
        uniform = [dict(s, probability=p) for s, p in zip(scenarios, (33, 33, 34))]
        self.assertAlmostEqual(views.brier(uniform, 'base'), 0.2245, places=3)

    def test_scoring_uses_recorded_prices_only(self):
        views.record(self.work, view())
        self.corpus(self.price('2026-03-20', 100.0), self.price('2026-09-20', 105.0))
        result = views.score(self.work, 'VW-20260920-01', as_of='2026-09-20')
        self.assertEqual(result['realised_return'], 0.05)
        self.assertEqual(result['scenario_materialised'], 'base')
        self.assertEqual(result['realised_ev_keys'],
                         ['stooq:SYN@2026-03-20', 'stooq:SYN@2026-09-20'])
        self.assertEqual(result['brier'], round(0.38 / 3, 4))

    def test_a_missing_price_stops_the_score(self):
        views.record(self.work, view())
        self.corpus(self.price('2026-03-20', 100.0))
        result = self.run_tool('views.py', '--root', self.work, 'score',
                               '--view', 'VW-20260920-01', '--as-of', '2026-09-20', ok=False)
        self.assertIn('Using it would report a stale price', result.stderr)

    def test_a_stale_end_price_is_never_used_as_the_price_on_the_day(self):
        """Found by this test: without a staleness bound the score silently read +0.00 %.

        `recorded_price` returns the newest observation on or before the date, so with no end price
        recorded it fell back to the START price - and the view scored as if the subject had not
        moved at all, which is the one result that would always flatter the forecast.
        """
        views.record(self.work, view())
        self.corpus(self.price('2026-03-20', 100.0))
        with self.assertRaisesRegex(views.MissingPrice, 'stale price as the price on the day'):
            views.score(self.work, 'VW-20260920-01', as_of='2026-09-20')

    def test_a_realised_return_outside_every_range_is_a_worse_miss(self):
        views.record(self.work, view())
        self.corpus(self.price('2026-03-20', 100.0), self.price('2026-09-20', 300.0))
        result = views.score(self.work, 'VW-20260920-01', as_of='2026-09-20')
        self.assertIsNone(result['scenario_materialised'])
        self.assertIsNone(result['brier'])
        self.assertIn('did not consider', result['note'])

    def test_a_view_cannot_be_scored_before_its_horizon(self):
        views.record(self.work, view())
        self.corpus(self.price('2026-03-20', 100.0), self.price('2026-06-01', 105.0))
        with self.assertRaisesRegex(views.StateError, 'does not expire until'):
            views.score(self.work, 'VW-20260920-01', as_of='2026-06-01')

    def test_expired_views_are_listed_by_horizon(self):
        views.record(self.work, view())
        views.record(self.work, view(view_id='VW-20260921-01', horizon_end='2027-09-20'))
        found = views.expired(views.load_all(self.work), as_of='2026-09-20')
        self.assertEqual([v['view_id'] for v in found], ['VW-20260920-01'])

    def test_calibration_says_there_is_no_evidence_before_the_first_score(self):
        result = views.calibration(self.work)
        self.assertEqual(result['scored_views'], 0)
        self.assertIn('no evidence either way', result['note'])

    def test_calibration_reports_mean_brier_with_its_sample_size(self):
        views.record(self.work, view())
        self.corpus(self.price('2026-03-20', 100.0), self.price('2026-09-20', 105.0))
        views.score(self.work, 'VW-20260920-01', as_of='2026-09-20')
        result = views.calibration(self.work)
        self.assertEqual(result['scored_views'], 1)
        self.assertEqual(result['by_asset_class']['equity']['n'], 1)
        self.assertIn('never used to rank anything', result['note'])


class ReferenceContractTests(WorkspaceTest):
    def test_low_confidence_is_the_default_and_mandatory_beyond_six_months(self):
        self.assertIn('`low` is the default', REFERENCE)
        self.assertIn('mandatory for anything beyond six\nmonths', REFERENCE)
        self.assertEqual(views.LONG_HORIZON_DAYS, 186)

    def test_the_brier_definition_matches_the_tool(self):
        self.assertIn('mean((p_i - o_i)^2)', REFERENCE)
        self.assertIn('0.22', REFERENCE)

    def test_the_disclosure_wording_matches(self):
        self.assertIn(views.DISCLOSURE.split(';')[0], REFERENCE)

    def test_the_reference_admits_there_is_no_track_record_yet(self):
        self.assertIn('no evidence either way', REFERENCE)
