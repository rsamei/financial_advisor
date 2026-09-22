"""The 05 reference, the retrieval tool and the /market command must state the same rules.

Drift between a rubric and the tool that implements it is silent: the file still reads correctly
and the verdicts quietly change. These tests are the alarm.
"""
import re
import unittest

from support import ROOT
from tools import market_retrieve as mr

def flat(text):
    """Line wrapping is not a difference of substance; compare prose unwrapped."""
    return ' '.join(text.split())


REFERENCE = (ROOT / '.claude/skills/financial-advisor/05-market-intelligence.md').read_text(
    encoding='utf-8')
COMMANDS = sorted((ROOT / '.claude/commands').glob('*.md'))
MARKET = (ROOT / '.claude/commands/market.md').read_text(encoding='utf-8')


class TierContractTests(unittest.TestCase):
    def test_the_five_tiers_appear_in_both_places(self):
        self.assertEqual(set(mr.FRESHNESS_DAYS), {'M', 'A', 'S', 'I', 'P'})
        for tier, label in (('M', 'Macro & rates'), ('A', 'Asset-specific'),
                            ('S', 'Sentiment & positioning'), ('I', 'Insider & institutional'),
                            ('P', 'Political & regulatory')):
            with self.subTest(tier=tier):
                self.assertIn(label, REFERENCE)

    def test_the_freshness_windows_agree(self):
        self.assertEqual(mr.FRESHNESS_DAYS['M'], 35)
        self.assertIn('35 days for monthly statistics', REFERENCE)
        self.assertIn('**5 days for rates and FX**', REFERENCE)
        self.assertEqual(mr.window_days('M', 'price'), 5)
        self.assertEqual(mr.window_days('A', 'price'), 5)
        for tier in ('S', 'P'):
            self.assertEqual(mr.FRESHNESS_DAYS[tier], 7)
        self.assertEqual(mr.FRESHNESS_DAYS['I'], 14)

    def test_every_registered_provider_is_named_in_the_reference(self):
        for name in mr.REGISTRY:
            with self.subTest(provider=name):
                self.assertIn(f'`{name}`', REFERENCE)

    def test_every_provider_in_the_reference_is_registered(self):
        named = set(re.findall(r'`([a-z_]+)`', REFERENCE))
        for name in named & {'ecb', 'fred', 'eurostat', 'stooq', 'coingecko', 'sec_edgar', 'rss',
                             'congress_trades', 'finra_short', 'cnn_fear_greed', 'reddit_json'}:
            with self.subTest(provider=name):
                self.assertIn(name, mr.REGISTRY)

    def test_sentiment_and_news_sources_are_never_primary(self):
        for name in ('rss', 'coingecko', 'cnn_fear_greed', 'reddit_json'):
            with self.subTest(provider=name):
                self.assertFalse(mr.REGISTRY[name]['primary'])
        self.assertIn('are never primary', REFERENCE)


class CoverageContractTests(unittest.TestCase):
    def test_c1_to_c4_are_defined_in_the_reference(self):
        for condition in ('**c1**', '**c2**', '**c3**', '**c4**'):
            self.assertIn(condition, REFERENCE)
        self.assertIn('3 independent operators', REFERENCE)

    def test_the_verdict_vocabulary_is_exactly_two_values(self):
        result = mr.coverage([], [], 'M')
        self.assertIn(result['verdict'], ('SUPPORTED', 'UNDETERMINED'))
        self.assertIn('SUPPORTED', REFERENCE)
        self.assertIn('UNDETERMINED', REFERENCE)
        # There is no third state. A claim is supported or it is not; "probably" is not a verdict.
        self.assertNotIn('PARTIALLY_SUPPORTED', REFERENCE)

    def test_the_reference_states_that_failed_sources_never_count(self):
        self.assertIn('Failed and throttled sources count zero', flat(REFERENCE))
        self.assertIn('never counts toward coverage', flat(MARKET))

    def test_a_forecast_can_never_satisfy_c4_in_either_place(self):
        self.assertIn('can never satisfy c4', flat(REFERENCE))
        self.assertIn('never satisfy c4', flat(MARKET))

    def test_undetermined_is_a_flag_not_an_error_and_caps_at_55(self):
        self.assertIn('cap:55', REFERENCE)


class WebToolContractTests(unittest.TestCase):
    """No command may require a web tool, an MCP server or a scraping plugin."""

    MANDATORY = re.compile(
        r'(must|always|required to|you have to)\s+(use|call|run)\s+'
        r'(WebSearch|WebFetch|Bright ?Data|MCP|bdata)', re.I)

    def test_no_command_makes_a_web_tool_mandatory(self):
        for path in COMMANDS:
            with self.subTest(command=path.stem):
                self.assertIsNone(self.MANDATORY.search(path.read_text(encoding='utf-8')))

    def test_discovery_only_is_stated_in_both_places(self):
        self.assertIn('discovery only', flat(MARKET).lower())
        self.assertIn('**discovery only**', flat(REFERENCE).lower())
        self.assertIn('add --url', REFERENCE)
        self.assertIn('add --url', MARKET)


class InsiderSignalContractTests(unittest.TestCase):
    def test_the_reference_states_each_dataset_lag(self):
        for fragment in ('45 days after quarter end', '30-45 days', 'Twice monthly',
                         '2 business days'):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, REFERENCE)

    def test_insider_evidence_is_confined_to_the_evidence_dimension(self):
        self.assertIn('only the *Evidence strength* dimension', flat(REFERENCE))
        self.assertIn('never lifts a cap', flat(REFERENCE))

    def test_congressional_amounts_are_ranges_not_figures(self):
        from providers import congress_trades
        self.assertIn('a midpoint is a number nobody disclosed', flat(REFERENCE))
        self.assertIn('never converts it to a midpoint', flat(congress_trades.__doc__))


class MarketCommandContractTests(unittest.TestCase):
    def test_coverage_is_computed_before_the_narrative(self):
        coverage_step = MARKET.index('## Step 4: Coverage')
        lens_step = MARKET.index('## Step 5: Five Lenses')
        self.assertLess(coverage_step, lens_step)
        self.assertIn('before** writing a word', flat(MARKET))

    def test_the_brief_opens_with_coverage_and_failures(self):
        self.assertIn('coverage table', MARKET)
        self.assertIn('failed-source list', MARKET)
        self.assertIn('Narrative comes after, never before', flat(MARKET))

    def test_the_five_lens_names_are_present(self):
        for lens in ('Macro & rates', 'Sector & asset', 'Sentiment &\npositioning',
                     'Insider & institutional signal', 'Political & regulatory'):
            with self.subTest(lens=lens):
                self.assertIn(lens, MARKET)

    def test_market_recommends_nothing(self):
        self.assertIn('It recommends nothing and executes nothing', MARKET)
        self.assertIn('Never write a recommendation here', MARKET)

    def test_no_agent_prompt_receives_an_amount(self):
        self.assertIn('No agent\nreceives an amount from the user', MARKET)
