"""The MNPI screen: it must catch the tip, and it must not refuse ordinary research.

The second half matters at least as much as the first. A guard that flags "the Form 4 shows three
insiders bought" is a guard the user learns to work around, and a guard nobody runs protects
nothing.
"""
from support import ROOT, WorkspaceTest
from tools import compliance_guard as cg

GUARDRAILS = (ROOT / '.claude/skills/financial-advisor/08-behavioural-guardrails.md').read_text(
    encoding='utf-8')


class CatchesMnpiTests(WorkspaceTest):
    REFUSED = [
        ('insider_tip', 'Someone at Synthetic Corp told me the quarter is going to be terrible.'),
        ('insider_tip', 'My friend who works at the company said the deal is signed.'),
        ('insider_tip', 'A client of mine mentioned they are about to lose the contract.'),
        ('unpublished', 'The figures are not public yet but they are very strong.'),
        ('unpublished', 'They have not announced it, so this is still an edge.'),
        ('unpublished', 'I have the internal memo; it is confidential.'),
        ('pre_announcement', 'We should buy before the announcement next week.'),
        ('pre_announcement', 'There is an unannounced merger coming.'),
        ('private_document', 'I saw the leaked deck with their real numbers.'),
    ]

    def test_every_shape_in_the_guardrails_is_refused(self):
        for expected, text in self.REFUSED:
            with self.subTest(text=text):
                result = cg.screen(text)
                self.assertEqual(result['verdict'], 'mnpi_suspected')
                self.assertIn(expected, result['categories'])

    def test_the_refusal_names_what_matched_without_guessing(self):
        result = cg.screen('Someone at Synthetic Corp told me the quarter is terrible.')
        self.assertTrue(result['hits'][0]['matched'])
        self.assertIn('cannot be used', result['advice'])

    def test_a_suspect_sentence_inside_a_long_innocent_text_is_still_caught(self):
        text = ('I have been reviewing my allocation. Equities are 55 % of the portfolio. '
                'My cousin who works at Synthetic Corp told me their results are strong. '
                'Should I rebalance?')
        self.assertEqual(cg.screen(text)['verdict'], 'mnpi_suspected')


class DoesNotRefuseResearchTests(WorkspaceTest):
    ALLOWED = [
        'The Form 4 shows three insiders bought shares before the announcement.',
        'According to the 13F filing, the fund increased its position last quarter.',
        'A congressional disclosure under the STOCK Act reports a purchase in that range.',
        'FINRA short interest rose at the last settlement date.',
        'The ECB statement said rates were left unchanged.',
        'Their press release published the quarterly results this morning.',
        'The prospectus and KID confirm this UCITS is available to EU retail investors.',
        'Eurostat published the September HICP print.',
        'I want to move some cash into a money market fund before the end of the year.',
        'My employer is in manufacturing, so I am wary of adding sector exposure.',
    ]

    def test_ordinary_research_language_is_cleared(self):
        for text in self.ALLOWED:
            with self.subTest(text=text):
                result = cg.screen(text)
                self.assertEqual(result['verdict'], 'clear',
                                 f'refused research language: {result["hits"]}')

    def test_a_public_filing_sentence_is_cleared_and_says_why(self):
        result = cg.screen('The Form 4 shows the CEO bought before the announcement.')
        self.assertEqual(result['verdict'], 'clear')
        self.assertTrue(result['cleared_by_public_source'])
        self.assertEqual(result['cleared_by_public_source'][0]['category'], 'pre_announcement')

    def test_clearing_is_per_sentence_not_per_document(self):
        # A public citation in one sentence does not launder a tip in the next.
        text = ('The Form 4 shows insider buying. '
                'Also, someone at the company told me the next quarter is not public yet.')
        self.assertEqual(cg.screen(text)['verdict'], 'mnpi_suspected')

    def test_an_empty_claim_is_clear(self):
        self.assertEqual(cg.screen('')['verdict'], 'clear')


class RefusalLogTests(WorkspaceTest):
    def test_a_refusal_is_logged_with_its_text_and_chained(self):
        result = cg.screen('Someone at the company told me before the announcement.')
        cg.log_refusal(self.work, result, text='...', source='user', decision_id='D-001')
        cg.log_refusal(self.work, cg.screen('It is not public yet.'), text='...',
                       source='user', decision_id=None)
        events = cg.read_chain(self.work / cg.REFUSALS, 'refusals')
        self.assertEqual(len(events), 2)
        self.assertEqual(events[1]['previous'], events[0]['hash'])
        self.assertEqual(events[0]['payload']['decision_id'], 'D-001')

    def test_tampering_with_the_log_is_detected(self):
        cg.log_refusal(self.work, cg.screen('It is not public yet.'), text='original',
                       source='user', decision_id=None)
        path = self.work / cg.REFUSALS
        path.write_text(path.read_text(encoding='utf-8').replace('original', 'edited'),
                        encoding='utf-8')
        with self.assertRaisesRegex(cg.StateError, 'breaks the hash chain'):
            cg.read_chain(path, 'refusals')

    def test_the_log_is_never_a_reset_scope(self):
        from tools import reset_repo
        self.assertNotIn('compliance', reset_repo.SCOPES)
        for paths in reset_repo.SCOPE_MOVES.values():
            for path in paths:
                self.assertFalse(path.startswith('compliance'))


class ContractTests(WorkspaceTest):
    def test_the_categories_match_the_guardrails_file(self):
        for category in cg.MNPI_PATTERNS:
            with self.subTest(category=category):
                self.assertIn(category.replace('_', ' ').split()[0], GUARDRAILS.lower())
        for phrase in ('not public yet', 'before the announcement', 'told me'):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, GUARDRAILS)

    def test_the_guardrails_list_the_legal_sources_too(self):
        for source in ('Form 4', '13F', 'STOCK Act', 'FINRA short interest'):
            with self.subTest(source=source):
                self.assertIn(source, GUARDRAILS)

    def test_there_is_no_override(self):
        self.assertIn('There is no override', GUARDRAILS)
        self.assertIn('There is no override', cg.__doc__)

    def test_the_tool_admits_it_is_blunt(self):
        # A screen that claims precision it does not have invites the user to argue with it.
        self.assertIn('deliberately blunt', cg.__doc__)
        self.assertIn('not legal advice', cg.__doc__)


class CliTests(WorkspaceTest):
    def test_a_refusal_exits_nine_and_writes_the_log(self):
        result = self.run_tool('compliance_guard.py', '--root', self.work, 'screen',
                               '--text', 'Someone at the company told me before the announcement.',
                               '--decision', 'D-001', ok=False)
        self.assertIn('mnpi_suspected', result.stdout)
        self.assertIn('logged to compliance/refusals.jsonl', result.stdout)
        self.assertTrue((self.work / cg.REFUSALS).is_file())

    def test_a_clear_claim_exits_zero_and_writes_nothing(self):
        result = self.run_tool('compliance_guard.py', '--root', self.work, 'screen',
                               '--text', 'The Form 4 shows insider buying.')
        self.assertIn('verdict: clear', result.stdout)
        self.assertFalse((self.work / cg.REFUSALS).exists())

    def test_verify_reports_the_chain(self):
        cg.log_refusal(self.work, cg.screen('not public yet'), text='x', source='user',
                       decision_id=None)
        result = self.run_tool('compliance_guard.py', '--root', self.work, 'verify')
        self.assertIn('chain intact, 1 refusal', result.stdout)

    def test_passing_neither_text_nor_file_is_a_usage_error(self):
        result = self.run_tool('compliance_guard.py', '--root', self.work, 'screen', ok=False)
        self.assertIn('exactly one of', result.stderr)
