"""Retrieval, merge and evidence: the three places a market claim can quietly become untrue."""
import datetime as dt
import json

from support import WorkspaceTest
from providers import base
from tools import evidence_memory as ev
from tools import market_merge as mm
from tools import market_retrieve as mr

TODAY = dt.date(2026, 9, 20)


def record(**overrides):
    return {'key': 'ecb:X@2026-09-19', 'kind': 'series', 'provider': 'ecb', 'tier': 'M',
            'symbol': None, 'series_id': 'X', 'title': 'synthetic series', 'asof': '2026-09-19',
            'value': 2.15, 'unit': 'percent', 'currency': None, 'url': 'https://example.invalid',
            'published': None, 'text_digest': None, 'fetched_at': '2026-09-20T08:00:00+00:00',
            'cache': False, 'sources': ['ecb'], 'first_seen': '2026-09-20', 'raw_ref': None,
            'is_forecast': False} | overrides


def pull_record(provider='ecb', operator='ecb', status='ok', records=1, **overrides):
    return {'provider': provider, 'operator': operator, 'tier': 'M', 'argv': [], 'mode': 'use',
            'status': status, 'records': records, 'fetched_at': '2026-09-20T08:00:00+00:00'
            } | overrides


class FreshnessTests(WorkspaceTest):
    def test_windows_come_from_the_tier_and_prices_get_the_tight_one(self):
        self.assertEqual(mr.FRESHNESS_DAYS, {'M': 35, 'A': 1, 'S': 7, 'I': 14, 'P': 7})
        self.assertEqual(mr.window_days('M'), 35)                  # monthly statistics
        self.assertEqual(mr.window_days('M', 'price'), 5)          # a daily rate or FX fix
        self.assertEqual(mr.window_days('A', 'price'), 5)          # 1 trading day + weekend grace
        self.assertEqual(mr.window_days('I'), 14)


class PeriodTests(WorkspaceTest):
    """A statistical release is dated by its period. Found live: Eurostat returns 'YYYY-MM'."""

    def test_days_months_quarters_and_years_all_resolve_to_a_period_end(self):
        self.assertEqual(mr.period_end('2026-09-19'), dt.date(2026, 9, 19))
        self.assertEqual(mr.period_end('2026-09'), dt.date(2026, 9, 30))
        self.assertEqual(mr.period_end('2026-12'), dt.date(2026, 12, 31))
        self.assertEqual(mr.period_end('2026Q3'), dt.date(2026, 9, 30))
        self.assertEqual(mr.period_end('2026-Q1'), dt.date(2026, 3, 31))
        self.assertEqual(mr.period_end('2026'), dt.date(2026, 12, 31))

    def test_something_that_is_not_a_period_stays_unknown(self):
        self.assertIsNone(mr.period_end('last tuesday'))

    def test_a_monthly_print_is_judged_on_its_period_end_not_its_first_day(self):
        # A September print is inside the 35-day window on 20 October; dating it 2026-09-01 would
        # make it stale a fortnight early.
        pulls = [pull_record('ecb', 'ecb'), pull_record('fred', 'stlouisfed'),
                 pull_record('eurostat', 'eurostat')]
        records = [record(key='eurostat:hicp@2026-09', provider='eurostat', asof='2026-09')]
        result = mr.coverage(records, pulls, 'M', dt.date(2026, 10, 20))
        self.assertTrue(result['c2_within_freshness'], result['stale'])
        self.assertEqual(result['verdict'], 'SUPPORTED')

    def test_an_old_monthly_print_is_still_caught(self):
        pulls = [pull_record('ecb', 'ecb'), pull_record('fred', 'stlouisfed'),
                 pull_record('eurostat', 'eurostat')]
        records = [record(key='eurostat:hicp@2025-12', provider='eurostat', asof='2025-12')]
        result = mr.coverage(records, pulls, 'M', dt.date(2026, 9, 20))
        self.assertFalse(result['c2_within_freshness'])
        self.assertIn('263 days old', result['stale'][0])


class CacheTests(WorkspaceTest):
    def canned(self, text):
        return lambda url, headers: text

    CSV = ('KEY,TIME_PERIOD,OBS_VALUE\nX.Y,2026-09-19,2.15\n')

    def test_a_live_pull_writes_body_and_metadata_separately(self):
        records, pull = mr.pull(self.work, 'ecb', {'series': 'X.Y'}, mode='live', today=TODAY,
                                transport=self.canned(self.CSV))
        self.assertEqual(pull['status'], 'ok')
        self.assertFalse(records[0]['cache'])
        body, meta = mr._paths(self.work, pull['cache_key'])
        self.assertTrue(body.is_file() and meta.is_file())

    def test_a_cache_hit_never_looks_fresh(self):
        _, first = mr.pull(self.work, 'ecb', {'series': 'X.Y'}, mode='live', today=TODAY,
                           transport=self.canned(self.CSV))
        records, second = mr.pull(self.work, 'ecb', {'series': 'X.Y'}, mode='use', today=TODAY,
                                  transport=self.canned('NEVER FETCHED'))
        self.assertEqual(second['status'], 'cached')
        self.assertTrue(all(item['cache'] for item in records))
        self.assertEqual(second['fetched_at'], first['fetched_at'])   # the original moment stands

    def test_a_stale_cache_entry_is_refetched_in_use_mode(self):
        _, first = mr.pull(self.work, 'ecb', {'series': 'X.Y'}, mode='live', today=TODAY,
                           transport=self.canned(self.CSV))
        much_later = TODAY + dt.timedelta(days=90)
        _, second = mr.pull(self.work, 'ecb', {'series': 'X.Y'}, mode='use', today=much_later,
                            transport=self.canned(self.CSV))
        self.assertEqual(second['status'], 'ok')

    def test_offline_mode_uses_a_stale_entry_but_refuses_a_missing_one(self):
        mr.pull(self.work, 'ecb', {'series': 'X.Y'}, mode='live', today=TODAY,
                transport=self.canned(self.CSV))
        _, pull = mr.pull(self.work, 'ecb', {'series': 'X.Y'}, mode='offline',
                          today=TODAY + dt.timedelta(days=400), transport=None)
        self.assertEqual(pull['status'], 'cached')
        with self.assertRaisesRegex(base.ProviderError, 'no cached response'):
            mr.pull(self.work, 'ecb', {'series': 'OTHER.KEY'}, mode='offline', today=TODAY)

    def test_an_unknown_provider_is_refused(self):
        with self.assertRaisesRegex(mr.StateError, 'unknown provider'):
            mr.pull(self.work, 'definitely_not_a_provider', {}, mode='live')


class QueryRecordTests(WorkspaceTest):
    def test_a_failed_pull_is_recorded_with_its_reason(self):
        failure = mr.failed_pull('stooq', {'symbol': 'x.us'}, 'live',
                                 base.ProviderError('boom', 'throttled'))
        mr.record_query(self.work, 'Q-test', 'A', failure)
        queries = json.loads((self.work / 'market' / 'queries.json').read_text(encoding='utf-8'))
        recorded = queries['Q-test']['pulls'][0]
        self.assertEqual(recorded['status'], 'throttled')
        self.assertEqual(recorded['records'], 0)
        self.assertIn('boom', recorded['reason'])

    def test_argv_is_recorded_so_the_pull_can_be_replayed(self):
        _, pull = mr.pull(self.work, 'ecb', {'series': 'X.Y', 'last': 2}, mode='live', today=TODAY,
                          transport=lambda url, headers: 'KEY,TIME_PERIOD,OBS_VALUE\nX,2026-09-19,1\n')
        mr.record_query(self.work, 'Q-test', 'M', pull)
        queries = json.loads((self.work / 'market' / 'queries.json').read_text(encoding='utf-8'))
        self.assertEqual(queries['Q-test']['pulls'][0]['argv'],
                         ['--last', '2', '--series', 'X.Y'])

    def test_a_query_id_must_be_a_query_id(self):
        with self.assertRaisesRegex(mr.StateError, 'must start with'):
            mr.record_query(self.work, 'macro-stuff', 'M', pull_record())


class CoverageTests(WorkspaceTest):
    def test_three_independent_primaries_inside_the_window_are_supported(self):
        records = [record(), record(key='fred:X@2026-09-19', provider='fred'),
                   record(key='eurostat:X@2026-09-19', provider='eurostat')]
        pulls = [pull_record('ecb', 'ecb'), pull_record('fred', 'stlouisfed'),
                 pull_record('eurostat', 'eurostat')]
        result = mr.coverage(records, pulls, 'M', TODAY)
        self.assertEqual(result['verdict'], 'SUPPORTED')
        self.assertEqual(result['c1_independent_operators'], 3)
        self.assertEqual(result['why'], [])

    def test_two_mirrors_of_one_operator_count_once(self):
        pulls = [pull_record('rss', 'ecb'), pull_record('ecb', 'ecb'),
                 pull_record('fred', 'stlouisfed')]
        result = mr.coverage([record()], pulls, 'M', TODAY)
        self.assertEqual(result['c1_independent_operators'], 2)
        self.assertEqual(result['verdict'], 'UNDETERMINED')

    def test_a_failed_source_counts_nothing_however_loudly_it_failed(self):
        pulls = [pull_record('ecb', 'ecb'), pull_record('fred', 'stlouisfed'),
                 pull_record('stooq', 'stooq', status='failed', records=0, reason='timeout'),
                 pull_record('coingecko', 'coingecko', status='throttled', records=0)]
        result = mr.coverage([record()], pulls, 'M', TODAY)
        self.assertEqual(result['c1_independent_operators'], 2)
        self.assertEqual(result['verdict'], 'UNDETERMINED')
        self.assertEqual({f['provider'] for f in result['failed_sources']}, {'stooq', 'coingecko'})

    def test_a_source_that_returned_zero_records_is_not_a_source(self):
        pulls = [pull_record('ecb', 'ecb'), pull_record('fred', 'stlouisfed'),
                 pull_record('eurostat', 'eurostat', records=0)]
        self.assertEqual(mr.coverage([record()], pulls, 'M', TODAY)['c1_independent_operators'], 2)

    def test_an_observation_outside_its_window_fails_c2(self):
        old = record(asof='2026-01-01')
        pulls = [pull_record('ecb', 'ecb'), pull_record('fred', 'stlouisfed'),
                 pull_record('eurostat', 'eurostat')]
        result = mr.coverage([old], pulls, 'M', TODAY)
        self.assertFalse(result['c2_within_freshness'])
        self.assertEqual(result['verdict'], 'UNDETERMINED')
        self.assertIn('window is 35', result['stale'][0])

    def test_without_a_primary_source_the_claim_is_undetermined(self):
        pulls = [pull_record('rss', 'ecb'), pull_record('rss', 'consob'),
                 pull_record('coingecko', 'coingecko')]
        result = mr.coverage([record(tier='P', kind='news')], pulls, 'P', TODAY)
        self.assertEqual(result['c1_independent_operators'], 3)
        self.assertFalse(result['c3_primary_source'])
        self.assertEqual(result['verdict'], 'UNDETERMINED')
        self.assertTrue(any('not the fact' in reason for reason in result['why']))

    def test_a_forecast_can_never_satisfy_c4(self):
        records = [record(), record(key='fred:F', provider='fred', is_forecast=True),
                   record(key='eurostat:X', provider='eurostat')]
        pulls = [pull_record('ecb', 'ecb'), pull_record('fred', 'stlouisfed'),
                 pull_record('eurostat', 'eurostat')]
        result = mr.coverage(records, pulls, 'M', TODAY)
        self.assertFalse(result['c4_observation_not_forecast'])
        self.assertEqual(result['verdict'], 'UNDETERMINED')

    def test_a_missing_asof_is_not_silently_fresh(self):
        pulls = [pull_record('ecb', 'ecb'), pull_record('fred', 'stlouisfed'),
                 pull_record('eurostat', 'eurostat')]
        result = mr.coverage([record(asof=None)], pulls, 'M', TODAY)
        self.assertFalse(result['c2_within_freshness'])


class MergeTests(WorkspaceTest):
    def merge(self, *batches):
        """merge() is pure; the corpus only exists on disk once it is saved, as the CLI does."""
        corpus, report = mm.merge(self.work, list(batches))
        mm.save(self.work, corpus)
        return corpus, report

    def test_identical_keys_merge_and_sources_union(self):
        corpus, report = self.merge([record()], [record(sources=['fred'])])
        self.assertEqual(len(corpus), 1)
        self.assertEqual(report['merged_exact'], 1)
        self.assertEqual(corpus['ecb:X@2026-09-19']['sources'], ['ecb', 'fred'])

    def test_a_fuzzy_merge_is_always_reported(self):
        twin = record(key='ecb:X@2026-09-19#duplicate')
        corpus, report = self.merge([record()], [twin])
        self.assertEqual(len(corpus), 1)
        self.assertEqual(len(report['merged_fuzzy']), 1)
        self.assertEqual(report['merged_fuzzy'][0]['into'], 'ecb:X@2026-09-19')

    def test_a_null_field_is_filled_and_a_known_value_is_never_overwritten(self):
        corpus, report = self.merge([record(unit=None)], [record(unit='percent')])
        self.assertEqual(corpus['ecb:X@2026-09-19']['unit'], 'percent')
        corpus, report = self.merge([record(value=9.99)])
        self.assertEqual(corpus['ecb:X@2026-09-19']['value'], 2.15)      # first value stands
        self.assertEqual(report['conflicts'][0]['field'], 'value')
        self.assertEqual(report['conflicts'][0]['rejected'], 9.99)

    def test_a_known_empty_string_is_not_treated_as_unknown(self):
        self.merge([record(unit='')])
        corpus, report = self.merge([record(unit='percent')])
        self.assertEqual(corpus['ecb:X@2026-09-19']['unit'], '')
        self.assertTrue(report['conflicts'])

    def test_protected_fields_survive_a_merge(self):
        self.merge([record()])
        corpus = mm.load(self.work)
        corpus['ecb:X@2026-09-19']['cited_in'] = ['EV-abc123']
        mm.save(self.work, corpus)
        corpus, _ = self.merge([record(first_seen='2030-01-01', cited_in=[])])
        self.assertEqual(corpus['ecb:X@2026-09-19']['cited_in'], ['EV-abc123'])
        self.assertEqual(corpus['ecb:X@2026-09-19']['first_seen'], '2026-09-20')

    def test_two_news_items_from_one_feed_on_one_day_stay_two_items(self):
        """Found live on 2026-09-20: the fuzzy rule collapsed two distinct ECB press releases.

        They share provider, kind and date and name no instrument, so the fuzzy key matched and
        one of them disappeared. A dedup rule that merges two different facts is worse than none.
        """
        first = record(key='rss:ecb_press:aaaa', provider='rss', tier='P', kind='news',
                       series_id=None, symbol=None, title='Monetary policy decisions',
                       asof='2026-09-18')
        second = record(key='rss:ecb_press:bbbb', provider='rss', tier='P', kind='news',
                        series_id=None, symbol=None, title='Annual report published',
                        asof='2026-09-18')
        corpus, report = self.merge([first, second])
        self.assertEqual(len(corpus), 2)
        self.assertEqual(report['merged_fuzzy'], [])

    def test_the_fuzzy_rule_still_applies_where_an_instrument_is_named(self):
        corpus, report = self.merge([record(), record(key='ecb:X@2026-09-19#dup')])
        self.assertEqual(len(corpus), 1)
        self.assertEqual(len(report['merged_fuzzy']), 1)

    def test_a_record_without_a_key_is_skipped_not_guessed(self):
        _, report = self.merge([{'provider': 'ecb', 'value': 1}])
        self.assertEqual(report['skipped'][0]['reason'], 'record has no key')


class EvidenceTests(WorkspaceTest):
    SOURCE = ('The Governing Council today decided to keep the three key ECB interest rates '
              'unchanged.\nThe deposit facility rate stands at 2.00 %.')

    def candidate(self, **overrides):
        digest = ev.write_snapshot(self.work, self.SOURCE)
        return {'claim': 'the ECB left rates unchanged in September 2026',
                'topic': 'euro-rates', 'tier': 'M', 'provider': 'ecb',
                'record_keys': ['ecb:X@2026-09-19'],
                'quote': 'decided to keep the three key ECB interest rates unchanged',
                'snapshot_digest': digest, 'asof': '2026-09-18',
                'url': 'https://www.ecb.europa.eu/synthetic', 'direction': 'neutral',
                'horizon': 'spot', 'confidence': 'medium', 'is_forecast': False,
                'mnpi_screen': 'clear'} | overrides

    def test_a_card_stores_and_is_content_addressed(self):
        card, created = ev.add(self.work, self.candidate())
        self.assertTrue(created)
        self.assertTrue(ev.KEY_RE.fullmatch(card['key']))
        again, created_again = ev.add(self.work, self.candidate())
        self.assertFalse(created_again)
        self.assertEqual(again['key'], card['key'])

    def test_changed_content_is_a_new_card_not_an_edit(self):
        first, _ = ev.add(self.work, self.candidate())
        second, _ = ev.add(self.work, self.candidate(
            claim='the ECB held rates in September 2026', supersedes=first['key']))
        self.assertNotEqual(first['key'], second['key'])
        self.assertEqual(second['supersedes'], first['key'])
        self.assertTrue(ev.find_card(self.work, first['key']).is_file())   # the old card survives

    def test_a_quote_not_in_the_snapshot_is_refused(self):
        with self.assertRaisesRegex(ev.StateError, 'does not occur in the snapshot'):
            ev.add(self.work, self.candidate(quote='decided to cut rates by 50 basis points'))

    def test_line_wrapping_is_not_a_difference_of_substance(self):
        card, _ = ev.add(self.work, self.candidate(
            quote='keep the three key ECB\n   interest rates unchanged'))
        self.assertTrue(card['key'])

    def test_a_card_without_an_mnpi_screen_cannot_exist(self):
        for value in (None, 'pending', 'mnpi_suspected'):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ev.StateError, 'mnpi_screen must be'):
                    ev.add(self.work, self.candidate(mnpi_screen=value))

    def test_a_forecast_card_is_marked_as_one(self):
        card, _ = ev.add(self.work, self.candidate(is_forecast=True))
        self.assertTrue(card['is_forecast'])

    def test_check_detects_an_edited_card(self):
        card, _ = ev.add(self.work, self.candidate())
        path = ev.find_card(self.work, card['key'])
        tampered = json.loads(path.read_text(encoding='utf-8'))
        tampered['claim'] = 'the ECB cut rates'
        path.write_text(json.dumps(tampered), encoding='utf-8')
        result = ev.check(self.work, card['key'])[0]
        self.assertFalse(result['ok'])
        self.assertTrue(any('immutable' in problem for problem in result['problems']))

    def test_check_detects_a_missing_snapshot(self):
        card, _ = ev.add(self.work, self.candidate())
        ev.snapshot_path(self.work, card['snapshot_digest']).unlink()
        result = ev.check(self.work, card['key'])[0]
        self.assertFalse(result['ok'])
        self.assertTrue(any('no snapshot' in problem for problem in result['problems']))

    def test_a_discovered_url_is_refetched_before_it_counts(self):
        digest, text = ev.fetch_and_snapshot(self.work, 'https://example.invalid/pr',
                                             transport=lambda url, headers: self.SOURCE)
        self.assertEqual(text, self.SOURCE)
        self.assertTrue(ev.snapshot_path(self.work, digest).is_file())

    def test_snapshots_are_stored_once_per_content(self):
        first = ev.write_snapshot(self.work, self.SOURCE)
        second = ev.write_snapshot(self.work, self.SOURCE)
        self.assertEqual(first, second)
        self.assertEqual(len(list((self.work / ev.SNAPSHOTS).glob('*.txt'))), 1)


class CliTests(WorkspaceTest):
    def test_a_failed_pull_exits_with_the_provider_code_and_is_recorded(self):
        result = self.run_tool('market_retrieve.py', '--root', self.work, 'pull',
                               '--provider', 'stooq', '--arg', 'symbol=nosuffix',
                               '--query', 'Q-test', '--mode', 'live', ok=False)
        self.assertNotIn('Traceback', result.stderr)
        self.assertIn('counts nothing toward coverage', result.stderr)
        queries = json.loads((self.work / 'market' / 'queries.json').read_text(encoding='utf-8'))
        self.assertEqual(queries['Q-test']['pulls'][0]['status'], 'failed')

    def test_list_names_every_registered_provider_with_its_operator(self):
        result = self.run_tool('market_retrieve.py', 'list')
        for name in mr.REGISTRY:
            self.assertIn(name, result.stdout)
        self.assertIn('primary', result.stdout)
