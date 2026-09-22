"""Triggers, alerts, and the four operational commands.

The rule this file exists to protect: "not evaluated" is not "not fired".
"""
import json

from support import ROOT, WorkspaceTest
from tools import watchlist as wl

COMMANDS = ROOT / '.claude' / 'commands'
WATCH = (COMMANDS / 'watch.md').read_text(encoding='utf-8')
STATUS = (COMMANDS / 'status.md').read_text(encoding='utf-8')
RESUME = (COMMANDS / 'resume.md').read_text(encoding='utf-8')
RESET = (COMMANDS / 'reset.md').read_text(encoding='utf-8')
CHECKPOINTS = (ROOT / '.claude/skills/financial-advisor/10-workflow-checkpoints.md').read_text(
    encoding='utf-8')


def flat(text):
    return ' '.join(text.split())


class TriggerTests(WorkspaceTest):
    def trigger(self, **overrides):
        return wl.add(self.work, **({'subject': 'SYN', 'metric': 'price', 'op': 'below',
                                     'value': 90.0, 'then': 'revisit D-004 before buying more',
                                     'expires': '2027-01-01'} | overrides))

    def test_a_trigger_records_the_users_own_then_text(self):
        entry = self.trigger()
        self.assertEqual(entry['watch_id'], 'W-001')
        self.assertEqual(entry['then'], 'revisit D-004 before buying more')
        self.assertIsNone(entry['fired_on'])

    def test_a_trigger_without_a_then_is_refused(self):
        with self.assertRaisesRegex(wl.StateError, 'answered in the moment'):
            self.trigger(then='   ')

    def test_ids_increment_and_removal_works(self):
        self.trigger()
        self.assertEqual(self.trigger(subject='OTHER')['watch_id'], 'W-002')
        wl.remove(self.work, 'W-001')
        self.assertEqual([e['watch_id'] for e in wl.load(self.work)], ['W-002'])
        with self.assertRaisesRegex(wl.StateError, 'no such trigger'):
            wl.remove(self.work, 'W-001')

    def test_a_bad_operator_or_metric_is_refused(self):
        with self.assertRaisesRegex(wl.StateError, 'op must be one of'):
            self.trigger(op='vibes')
        with self.assertRaisesRegex(wl.StateError, 'metric must be one of'):
            self.trigger(metric='mood')


class EvaluationTests(WorkspaceTest):
    def corpus(self, *records):
        path = self.work / wl.OBSERVATIONS
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({r['key']: r for r in records}), encoding='utf-8')

    def price(self, asof, value, symbol='SYN'):
        return {'key': f'stooq:{symbol}@{asof}', 'symbol': symbol, 'asof': asof, 'value': value,
                'kind': 'price', 'provider': 'stooq', 'tier': 'A'}

    def trigger(self, **overrides):
        return wl.add(self.work, **({'subject': 'SYN', 'metric': 'price', 'op': 'below',
                                     'value': 90.0, 'then': 'revisit D-004',
                                     'expires': '2027-01-01'} | overrides))

    def test_a_met_condition_fires_with_its_evidence_key(self):
        self.trigger()
        self.corpus(self.price('2026-09-19', 85.0))
        result = wl.evaluate(self.work, as_of='2026-09-20')
        self.assertEqual(len(result['fired']), 1)
        self.assertEqual(result['fired'][0]['observed'], 85.0)
        self.assertEqual(result['fired'][0]['ev_key'], 'stooq:SYN@2026-09-19')

    def test_an_unmet_condition_is_not_fired(self):
        self.trigger()
        self.corpus(self.price('2026-09-19', 95.0))
        result = wl.evaluate(self.work, as_of='2026-09-20')
        self.assertEqual(result['fired'], [])
        self.assertEqual(len(result['not_fired']), 1)

    def test_a_missing_observation_is_not_evaluated_never_not_fired(self):
        self.trigger()
        result = wl.evaluate(self.work, as_of='2026-09-20')
        self.assertEqual(result['fired'], [])
        self.assertEqual(result['not_fired'], [])
        self.assertEqual(len(result['not_evaluated']), 1)
        self.assertIn('no recorded observation', result['not_evaluated'][0]['reason'])

    def test_a_stale_observation_is_not_evaluated_either(self):
        self.trigger()
        self.corpus(self.price('2026-08-01', 85.0))       # would have fired, but it is 50 days old
        result = wl.evaluate(self.work, as_of='2026-09-20')
        self.assertEqual(result['fired'], [])
        self.assertEqual(len(result['not_evaluated']), 1)
        self.assertIn('days old', result['not_evaluated'][0]['reason'])

    def test_an_expired_trigger_is_reported_separately(self):
        self.trigger(expires='2026-01-01')
        self.corpus(self.price('2026-09-19', 85.0))
        result = wl.evaluate(self.work, as_of='2026-09-20')
        self.assertEqual(result['fired'], [])
        self.assertEqual(len(result['expired']), 1)

    def test_every_operator_compares_the_way_it_reads(self):
        self.corpus(self.price('2026-09-19', 90.0))
        cases = {'below': False, 'at_or_below': True, 'above': False, 'at_or_above': True}
        for op, expected in cases.items():
            with self.subTest(op=op):
                wl.save(self.work, [])
                self.trigger(op=op)
                result = wl.evaluate(self.work, as_of='2026-09-20')
                self.assertEqual(bool(result['fired']), expected)


class AlertTests(EvaluationTests):
    def test_an_alert_quotes_the_user_and_recommends_nothing(self):
        self.trigger(then='sell half and stop looking at it')
        self.corpus(self.price('2026-09-19', 85.0))
        result = wl.evaluate(self.work, as_of='2026-09-20')
        written = wl.write_alerts(self.work, result)
        text = written[0].read_text(encoding='utf-8')
        self.assertIn('sell half and stop looking at it', text)
        self.assertIn('This alert is not a recommendation', text)
        self.assertIn('has not been vetted', text)
        self.assertIn('stooq:SYN@2026-09-19', text)

    def test_a_fired_trigger_is_marked_and_does_not_fire_twice(self):
        self.trigger()
        self.corpus(self.price('2026-09-19', 85.0))
        wl.write_alerts(self.work, wl.evaluate(self.work, as_of='2026-09-20'))
        self.assertEqual(wl.load(self.work)[0]['fired_on'], '2026-09-20')
        again = wl.evaluate(self.work, as_of='2026-09-21')
        self.assertEqual(again['fired'], [])

    def test_the_alert_matches_the_template_in_09(self):
        templates = (ROOT / '.claude/skills/financial-advisor/09-reporting-templates.md').read_text(
            encoding='utf-8')
        for fragment in ('**Trigger**', '**Observed**', '**You said then**',
                         'This alert is not a recommendation'):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, templates)


class CommandContractTests(WorkspaceTest):
    def test_watch_states_the_three_outcomes(self):
        self.assertIn('Three Outcomes, Not Two', WATCH)
        self.assertIn('Never report "not evaluated" as "not fired"', WATCH)

    def test_watch_forbids_advice_in_an_alert(self):
        self.assertIn('Never let an alert carry a recommendation', WATCH)
        self.assertIn('Do not attach fresh analysis', WATCH)

    def test_a_park_must_create_a_trigger(self):
        self.assertIn('what makes a park\ndifferent from forgetting', WATCH)

    def test_status_writes_nothing_at_all(self):
        self.assertIn('writes **nothing at all**', STATUS)
        self.assertIn('Never write anything', STATUS)

    def test_status_reports_broken_invariants_before_stale_data(self):
        self.assertLess(STATUS.index('Broken invariants'), STATUS.index('Stale figures'))
        self.assertIn('Never report a broken invariant as a stale figure', STATUS)

    def test_resume_never_reimplements_a_step(self):
        self.assertIn('never reimplements a step', RESUME)
        self.assertIn('Never resume past an invalidated step', RESUME)
        self.assertIn('Never delete another process', RESUME)

    def test_reset_names_what_each_scope_costs(self):
        from tools import reset_repo
        for scope in reset_repo.SCOPES:
            with self.subTest(scope=scope):
                self.assertIn(f'| `{scope}` |', RESET)
        self.assertIn('erases the evidence about\nwhether this system has been any good', RESET)

    def test_reset_never_includes_compliance(self):
        self.assertIn('`compliance` is deliberately not a scope', RESET)
        self.assertIn('Never include `compliance/` in any scope', RESET)

    def test_reset_moves_and_never_deletes(self):
        self.assertIn('Never delete. Everything moves', RESET)
        self.assertIn("Never empty `.reset-trash/`", RESET)


class CheckpointReferenceTests(WorkspaceTest):
    def test_receipts_are_written_last_and_the_reference_says_why(self):
        self.assertIn('Receipts are written **last**', CHECKPOINTS)
        self.assertIn('a receipt on disk therefore means', flat(CHECKPOINTS).lower())

    def test_invalidation_cascades_downstream(self):
        self.assertIn('everything downstream of it', CHECKPOINTS)
        self.assertIn('confident, wrong answer', CHECKPOINTS)

    def test_the_workflow_types_match_the_tool(self):
        from tools import workflow_state
        for name in workflow_state.WORKFLOW_TYPES:
            with self.subTest(name=name):
                self.assertIn(f'`{name}`', CHECKPOINTS)

    def test_the_tool_writes_operational_metadata_only(self):
        self.assertIn('operational metadata only', CHECKPOINTS)
        self.assertIn('never executes a stored\ncommand', CHECKPOINTS)


class CliTests(WorkspaceTest):
    def test_add_warns_when_no_expiry_is_set(self):
        result = self.run_tool('watchlist.py', '--root', self.work, 'add', '--subject', 'SYN',
                               '--op', 'below', '--value', '90', '--then', 'revisit D-004')
        self.assertIn('haunting, not a plan', result.stdout)

    def test_evaluate_prints_not_evaluated_prominently(self):
        wl.add(self.work, subject='SYN', metric='price', op='below', value=90.0,
               then='revisit', expires=None)
        result = self.run_tool('watchlist.py', '--root', self.work, 'evaluate',
                               '--as-of', '2026-09-20')
        self.assertIn('NOT EVALUATED', result.stdout)
