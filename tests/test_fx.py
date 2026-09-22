"""A conversion must trace to a recorded rate. Knowing roughly what EUR/USD is worth is not data."""
import json

from support import WorkspaceTest
from tools import fx

TODAY = __import__('datetime').date(2026, 9, 20)

RATES = {
    'as_of': '2026-09-18',
    'base': 'EUR',
    'rates': {
        'USD': {'rate': 1.25, 'ev_key': 'EV-aaaa1111', 'as_of': '2026-09-18'},
        'GBP': {'rate': 0.80, 'ev_key': 'EV-bbbb2222', 'as_of': '2026-09-18'},
        'CHF': {'rate': 0.95, 'ev_key': 'EV-cccc3333', 'as_of': '2026-01-05'},
    },
}


class ConversionTests(WorkspaceTest):
    def rates(self, value=None):
        path = self.work / 'fx_rates.json'
        path.write_text(json.dumps(value if value is not None else RATES), encoding='utf-8')
        return fx.load_rates(path)

    def test_to_eur_divides_by_the_published_rate(self):
        result = fx.convert(1250, 'USD', 'EUR', self.rates(), TODAY)
        self.assertEqual(result['converted'], 1000.0)      # 1250 / 1.25
        self.assertEqual(result['ev_keys'], ['EV-aaaa1111'])
        self.assertFalse(result['stale'])

    def test_from_eur_multiplies(self):
        self.assertEqual(fx.convert(1000, 'EUR', 'USD', self.rates(), TODAY)['converted'], 1250.0)

    def test_a_cross_rate_goes_through_eur_and_names_both_legs(self):
        result = fx.convert(1250, 'USD', 'GBP', self.rates(), TODAY)
        self.assertEqual(result['converted'], 800.0)       # 1250 / 1.25 * 0.80
        self.assertEqual(result['via'], 'EUR')
        self.assertEqual(result['ev_keys'], ['EV-aaaa1111', 'EV-bbbb2222'])

    def test_an_unrecorded_currency_is_refused(self):
        with self.assertRaisesRegex(fx.RateError, 'no recorded rate for JPY'):
            fx.convert(100, 'JPY', 'EUR', self.rates(), TODAY)

    def test_a_stale_rate_is_refused_unless_explicitly_allowed(self):
        with self.assertRaisesRegex(fx.RateError, 'days old'):
            fx.convert(100, 'CHF', 'EUR', self.rates(), TODAY)
        result = fx.convert(95, 'CHF', 'EUR', self.rates(), TODAY, allow_stale=True)
        self.assertEqual(result['converted'], 100.0)
        self.assertTrue(result['stale'])

    def test_a_rate_without_provenance_is_a_schema_error(self):
        rates = json.loads(json.dumps(RATES))
        rates['rates']['USD']['ev_key'] = None
        with self.assertRaisesRegex(fx.StateError, "missing 'ev_key'"):
            fx.convert(100, 'USD', 'EUR', self.rates(rates), TODAY)

    def test_rates_must_be_quoted_against_eur(self):
        rates = json.loads(json.dumps(RATES))
        rates['base'] = 'USD'
        with self.assertRaisesRegex(fx.StateError, 'quoted against EUR'):
            self.rates(rates)


class CliTests(WorkspaceTest):
    def setUp(self):
        super().setUp()
        (self.work / 'fx_rates.json').write_text(json.dumps(RATES), encoding='utf-8')

    def test_convert_reports_the_evidence_key(self):
        result = self.run_tool('fx.py', '--rates', self.work / 'fx_rates.json', '--on',
                               '2026-09-20', 'convert', '--amount', '1250', '--from', 'USD')
        self.assertIn('1000.0 EUR', result.stdout)
        self.assertIn('EV-aaaa1111', result.stdout)

    def test_a_missing_rate_exits_eight(self):
        result = self.run_tool('fx.py', '--rates', self.work / 'fx_rates.json', '--on',
                               '2026-09-20', 'convert', '--amount', '1', '--from', 'JPY', ok=False)
        self.assertNotIn('Traceback', result.stderr)
        self.assertIn('never invents', result.stderr)

    def test_exit_codes_follow_the_convention(self):
        import subprocess
        import sys
        from support import ROOT
        run = lambda *a: subprocess.run([sys.executable, str(ROOT / 'tools' / 'fx.py'), *a],
                                        capture_output=True, text=True, cwd=ROOT).returncode
        rates = str(self.work / 'fx_rates.json')
        self.assertEqual(run('--rates', rates, '--on', '2026-09-20', 'convert',
                             '--amount', '1', '--from', 'JPY'), 8)      # missing recorded price
        self.assertEqual(run('--rates', rates, '--on', '2026-09-20', 'convert',
                             '--amount', '1', '--from', 'DOLLARS'), 4)  # bad arguments
        self.assertEqual(run('--rates', str(self.work / 'nope.json'), 'rates'), 1)  # state error
