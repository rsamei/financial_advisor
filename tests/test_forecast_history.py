"""Recorded synthetic series; checks prevent look-ahead and fabricated track records."""
import datetime as dt
import json

from support import WorkspaceTest
from tools import forecast_history as fh


class HistoricalTests(WorkspaceTest):
    def corpus(self, n=13, late=False):
        start = dt.date(2025, 1, 1)
        rows = {}
        for i in range(n):
            day = (start + dt.timedelta(days=i * 30)).isoformat()
            rows[str(i)] = {'key': f'synthetic:{i}', 'provider': 'synthetic', 'symbol': 'SYN',
                            'kind': 'price', 'asof': day, 'value': 100 * 1.1 ** i,
                            'currency': 'EUR', 'unit': 'close',
                            'fetched_at': '2026-01-01' if late else day}
        self.write('market/observations.json', rows)
        return rows

    def test_baseline_uses_nonoverlapping_horizons_and_excludes_future(self):
        self.corpus()
        result = fh.base_rate(self.work, 'SYN', '30d', '2025-04-01')
        self.assertEqual(result['n_windows'], 3)
        self.assertAlmostEqual(result['p50'], .1)
        self.assertEqual(result['last_observation'], '2025-04-01')

    def test_past_only_benchmark_compares_identical_periods(self):
        self.corpus()
        result = fh.benchmark(self.work, 'SYN', '30d', '2025-12-31')
        self.assertEqual(result['n_trials'], 7)
        self.assertAlmostEqual(result['median_mean_absolute_error'], 0)
        self.assertAlmostEqual(result['no_change_mean_absolute_error'], .1)
        self.assertTrue(result['median_better_in_this_sample'])

    def test_late_download_is_not_historical_availability(self):
        self.corpus(late=True)
        with self.assertRaisesRegex(fh.StateError, 'availability'):
            fh.base_rate(self.work, 'SYN', '30d', '2025-12-31')
        result = fh.benchmark(self.work, 'SYN', '30d', '2025-12-31')
        self.assertEqual(result['status'], 'insufficient_history')

    def test_future_outcome_does_not_change_an_earlier_prediction(self):
        rows = self.corpus()
        before = fh.benchmark(self.work, 'SYN', '30d', '2025-12-31')
        rows['6']['value'] *= 2
        self.write('market/observations.json', rows)
        after = fh.benchmark(self.work, 'SYN', '30d', '2025-12-31')
        self.assertEqual(before['trials'][0]['median_prediction'], after['trials'][0]['median_prediction'])
        self.assertNotEqual(before['trials'][0]['return'], after['trials'][0]['return'])

    def test_mixed_series_and_nonfinite_prices_fail(self):
        rows = self.corpus()
        rows['1']['currency'] = 'USD'
        self.write('market/observations.json', rows)
        with self.assertRaisesRegex(fh.StateError, 'mixed'):
            fh.base_rate(self.work, 'SYN', '30d', '2025-12-31')
        rows['1']['currency'] = 'EUR'
        rows['1']['value'] = float('nan')
        self.write('market/observations.json', rows)
        with self.assertRaisesRegex(fh.StateError, 'finite'):
            fh.base_rate(self.work, 'SYN', '30d', '2025-12-31')

    def test_base_rate_cli_does_not_require_a_household_profile(self):
        self.corpus()
        result = self.run_tool('portfolio_math.py', '--root', self.work, 'base-rate',
                               '--symbol', 'SYN', '--horizon', '30d', '--as-of', '2025-12-31')
        self.assertEqual(json.loads(result.stdout)['n_windows'], 12)
