"""The ledger is append-only, the snapshot's reach is small, and a missing price stops the review."""
import datetime as dt
import json
import sys
from pathlib import Path

from support import ROOT, WorkspaceTest
from tools import track_actions as ta

sys.path.insert(0, str(Path(__file__).resolve().parent / 'fixtures'))
import synthetic_household as fx  # noqa: E402

TRACK = (ROOT / '.claude/commands/track.md').read_text(encoding='utf-8')
REVIEW_REF = (ROOT / '.claude/skills/financial-advisor/11-outcome-review.md').read_text(
    encoding='utf-8')


def flat(text):
    return ' '.join(text.split())


def action(**overrides):
    return {'decision_id': 'D-001', 'executed_on': '2026-09-21', 'what': 'buy',
            'instrument': 'SYN0000001', 'units': 10, 'price': 100.0, 'fees': 2.0,
            'currency': 'EUR', 'account': 'brokerage'} | overrides


class LedgerTests(WorkspaceTest):
    def test_actions_chain_and_ids_increment_within_a_day(self):
        first = ta.record(self.work, action())
        second = ta.record(self.work, action(instrument='SYN0000002'))
        self.assertEqual(first['payload']['action_id'], 'A-20260921-01')
        self.assertEqual(second['payload']['action_id'], 'A-20260921-02')
        self.assertEqual(second['previous'], first['hash'])

    def test_tampering_is_detected(self):
        ta.record(self.work, action())
        path = self.work / ta.ACTIONS
        path.write_text(path.read_text(encoding='utf-8').replace('"units":10', '"units":100'),
                        encoding='utf-8')
        with self.assertRaisesRegex(ta.StateError, 'breaks the hash chain'):
            ta.read_chain(path, 'actions')

    def test_a_correction_is_a_new_action_never_an_edit(self):
        ta.record(self.work, action())
        with self.assertRaisesRegex(ta.StateError, 'already recorded'):
            ta.record(self.work, action(action_id='A-20260921-01'))
        corrected = ta.record(self.work, action(units=12, note='corrects A-20260921-01'))
        self.assertEqual(corrected['payload']['units'], 12)
        self.assertEqual(len(ta.read_chain(self.work / ta.ACTIONS, 'actions')), 2)

    def test_an_action_with_no_decision_is_still_recorded(self):
        event = ta.record(self.work, action(decision_id=None))
        self.assertIsNone(event['payload']['decision_id'])

    def test_closed_vocabulary_and_date_validation(self):
        with self.assertRaisesRegex(ta.StateError, 'is not one of'):
            ta.record(self.work, action(what='yolo'))
        with self.assertRaisesRegex(ta.StateError, 'not an ISO date'):
            ta.record(self.work, action(executed_on='last tuesday'))

    def test_record_is_not_in_the_permission_allowlist(self):
        from tools import security_guards as guard
        for entry in guard.ALLOWED_PERMISSIONS:
            with self.subTest(entry=entry):
                self.assertNotIn('track_actions.py', entry)
        settings = json.loads((ROOT / '.claude/settings.json').read_text(encoding='utf-8'))
        for entry in settings['permissions']['allow']:
            self.assertNotIn('track_actions.py', entry)


class SnapshotTests(WorkspaceTest):
    def setUp(self):
        super().setUp()
        fx.write_all(self.work)

    def test_a_snapshot_refreshes_holdings_and_stamps_them(self):
        result = ta.snapshot(self.work, {'holdings': [
            {'isin_or_symbol': 'SYN0000001', 'name': 'Synthetic World Equity',
             'asset_class': 'equity', 'units': 300, 'avg_cost_eur': 90, 'value_eur': 33000,
             'account': 'brokerage', 'currency': 'EUR'}]}, as_of='2026-10-01')
        balance = ta.read_json(self.work / ta.BALANCE, 'balance')
        self.assertEqual(balance['holdings'][0]['value_eur'], 33000)
        self.assertEqual(balance['holdings'][0]['as_of'], '2026-10-01')
        self.assertEqual(balance['as_of'], '2026-10-01')
        self.assertEqual(result['holdings_after'], 1)
        self.assertTrue((self.work / ta.SNAPSHOTS / '2026-10-01.json').is_file())

    def test_a_snapshot_may_not_touch_anything_else(self):
        for field in ('cash_flow', 'liabilities', 'pension', 'insurance', 'source_ledger'):
            with self.subTest(field=field):
                with self.assertRaisesRegex(ta.StateError, 'may only refresh'):
                    ta.snapshot(self.work, {field: []})

    def test_cash_flow_and_liabilities_survive_a_snapshot(self):
        before = ta.read_json(self.work / ta.BALANCE, 'balance')
        ta.snapshot(self.work, {'holdings': []}, as_of='2026-10-01')
        after = ta.read_json(self.work / ta.BALANCE, 'balance')
        for field in ('cash_flow', 'liabilities', 'pension', 'insurance', 'source_ledger'):
            with self.subTest(field=field):
                self.assertEqual(before[field], after[field])

    def test_a_bad_date_is_refused(self):
        with self.assertRaisesRegex(ta.StateError, 'not an ISO date'):
            ta.snapshot(self.work, {'holdings': []}, as_of='soon')


class RecordedPriceTests(WorkspaceTest):
    def corpus(self, *records):
        path = self.work / ta.OBSERVATIONS
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({r['key']: r for r in records}), encoding='utf-8')

    def observation(self, symbol, asof, value, key=None):
        return {'key': key or f'{symbol}@{asof}', 'symbol': symbol, 'asof': asof, 'value': value,
                'kind': 'price', 'provider': 'stooq', 'tier': 'A'}

    def test_the_latest_price_on_or_before_the_date_is_used(self):
        self.corpus(self.observation('SYN', '2026-09-01', 90.0),
                    self.observation('SYN', '2026-09-15', 100.0),
                    self.observation('SYN', '2026-10-01', 110.0))
        found = ta.recorded_price(self.work, 'SYN', '2026-09-20')
        self.assertEqual(found['value'], 100.0)
        self.assertEqual(found['asof'], '2026-09-15')
        self.assertEqual(found['days_before_target'], 5)

    def test_a_missing_price_raises_rather_than_estimating(self):
        self.corpus(self.observation('OTHER', '2026-09-15', 100.0))
        with self.assertRaisesRegex(ta.MissingPrice, 'will not estimate'):
            ta.recorded_price(self.work, 'SYN', '2026-09-20')

    def test_no_corpus_at_all_is_a_clean_refusal(self):
        with self.assertRaisesRegex(ta.MissingPrice, 'no recorded observations'):
            ta.recorded_price(self.work, 'SYN', '2026-09-20')

    def test_a_price_after_the_target_date_is_never_used(self):
        self.corpus(self.observation('SYN', '2026-10-01', 110.0))
        with self.assertRaises(ta.MissingPrice):
            ta.recorded_price(self.work, 'SYN', '2026-09-20')


class ReviewArithmeticTests(RecordedPriceTests):
    def seed(self):
        self.corpus(
            self.observation('SYN', '2026-01-02', 100.0, key='stooq:SYN@2026-01-02'),
            self.observation('SYN', '2026-09-20', 120.0, key='stooq:SYN@2026-09-20'),
            self.observation('BENCH', '2026-01-02', 50.0, key='stooq:BENCH@2026-01-02'),
            self.observation('BENCH', '2026-09-20', 55.0, key='stooq:BENCH@2026-09-20'))

    def test_outcome_against_benchmark_and_do_nothing(self):
        self.seed()
        result = ta.review(self.work, decision_id='D-001', symbol='SYN', bought_on='2026-01-02',
                           as_of='2026-09-20', units=100, price_paid=100.0, fees=10.0,
                           benchmark_symbol='BENCH')
        # invested 100 x 100 + 10 = 10 010; now worth 100 x 120 = 12 000 -> +1 990 after costs.
        self.assertEqual(result['invested_eur'], 10010.0)
        self.assertEqual(result['value_now_eur'], 12000.0)
        self.assertEqual(result['action_result_eur'], 1990.0)
        # benchmark +10 % on the same money = 1 001.
        self.assertEqual(result['benchmark']['return_pct'], 10.0)
        self.assertEqual(result['benchmark']['result_eur'], 1001.0)
        self.assertEqual(result['vs_benchmark_eur'], 989.0)
        self.assertEqual(result['do_nothing_result_eur'], 0.0)
        self.assertEqual(result['vs_do_nothing_eur'], 1990.0)

    def test_every_price_used_is_named_with_its_key(self):
        self.seed()
        result = ta.review(self.work, decision_id='D-001', symbol='SYN', bought_on='2026-01-02',
                           as_of='2026-09-20', units=100, price_paid=100.0, fees=0.0,
                           benchmark_symbol='BENCH')
        self.assertEqual(len(result['prices_used']), 4)
        for price in result['prices_used']:
            self.assertTrue(price['ev_key'])

    def test_fees_are_part_of_the_result(self):
        self.seed()
        without = ta.review(self.work, decision_id='D-001', symbol='SYN', bought_on='2026-01-02',
                            as_of='2026-09-20', units=100, price_paid=100.0, fees=0.0,
                            benchmark_symbol='BENCH')['action_result_eur']
        with_fees = ta.review(self.work, decision_id='D-001', symbol='SYN',
                              bought_on='2026-01-02', as_of='2026-09-20', units=100,
                              price_paid=100.0, fees=250.0,
                              benchmark_symbol='BENCH')['action_result_eur']
        self.assertEqual(without - with_fees, 250.0)

    def test_a_missing_benchmark_price_exits_eight(self):
        self.corpus(self.observation('SYN', '2026-01-02', 100.0),
                    self.observation('SYN', '2026-09-20', 120.0))
        result = self.run_tool('track_actions.py', '--root', self.work, 'review',
                               '--decision', 'D-001', '--symbol', 'SYN', '--benchmark', 'BENCH',
                               '--bought-on', '2026-01-02', '--as-of', '2026-09-20',
                               '--units', '100', '--price-paid', '100', ok=False)
        self.assertIn('no recorded price for BENCH', result.stderr)
        self.assertNotIn('Traceback', result.stderr)


class ReferenceContractTests(WorkspaceTest):
    def test_the_four_verdict_pairings_are_in_both_places(self):
        for verdict in ta.VERDICTS:
            with self.subTest(verdict=verdict):
                self.assertIn(f'`{verdict}`', REVIEW_REF)
        self.assertEqual(len(ta.VERDICTS), 4)

    def test_good_process_bad_outcome_says_change_nothing(self):
        self.assertIn('**Change nothing.**', REVIEW_REF)

    def test_luck_is_called_luck(self):
        self.assertIn('| `bad_process/good_outcome` | Luck |', REVIEW_REF)
        self.assertIn('an unexamined win teaches the wrong lesson', REVIEW_REF)

    def test_a_lesson_is_never_a_ranking_input(self):
        self.assertIn('It is never a ranking input', REVIEW_REF)
        self.assertIn('Never turn a lesson into a preference', TRACK)

    def test_a_review_never_rewrites_the_advice(self):
        self.assertIn('never rewrites the advice it\nreviews', REVIEW_REF)
        self.assertIn('It is a\n**new file**', TRACK)

    def test_the_missing_price_rule_is_stated_in_both_places(self):
        self.assertIn('never interpolates, never assumes flat', flat(REVIEW_REF))
        self.assertIn('Do not estimate it', TRACK)

    def test_the_snapshot_boundary_is_stated_in_the_command(self):
        self.assertIn('Only `assets` and `holdings` may change', TRACK)
        self.assertIn("user's **life** changes", TRACK)

    def test_the_system_admits_it_has_no_track_record_yet(self):
        self.assertIn('too early to say', REVIEW_REF)
        self.assertIn('too early to say', TRACK)
