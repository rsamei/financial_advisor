"""`/decide`'s order of operations, and the archive that must never influence a ranking."""
import json
import re
import unittest

from support import ROOT, WorkspaceTest
from tools import tracker as tr

DECIDE = (ROOT / '.claude/commands/decide.md').read_text(encoding='utf-8')
ADVISE = (ROOT / '.claude/commands/advise.md').read_text(encoding='utf-8')


def flat(text):
    return ' '.join(text.split())


class OrderOfOperationsTests(unittest.TestCase):
    def test_the_screen_runs_before_any_analysis(self):
        screen = DECIDE.index('## Step 2: Screen Any User-Supplied Claim')
        evidence = DECIDE.index('## Step 3: Evidence')
        gates = DECIDE.index('## Step 4: Gates')
        self.assertLess(screen, evidence)
        self.assertLess(evidence, gates)
        self.assertIn('Step 2 comes before Step 3 for a reason', DECIDE)
        self.assertIn('before** any agent reads the claim', flat(DECIDE))

    def test_gates_precede_the_score(self):
        self.assertLess(DECIDE.index('## Step 4: Gates'), DECIDE.index('decision_score.py score'))
        self.assertIn('A FAIL stops the score', DECIDE)

    def test_the_tuple_is_confirmed_with_the_user_first(self):
        self.assertIn('show them to the user and get confirmation before', flat(DECIDE))
        for field in ('decision_type', 'amount_eur', 'funding_source', 'horizon', 'goal_id'):
            with self.subTest(field=field):
                self.assertIn(field, DECIDE)


class MnpiHandlingTests(unittest.TestCase):
    def test_the_command_refuses_without_arguing(self):
        self.assertIn('There is no override', DECIDE)
        self.assertIn('Do not weigh it, discount it, or reason around it', DECIDE)
        self.assertIn('Do not repeat the claim in the report', DECIDE)

    def test_stopping_entirely_is_a_valid_outcome(self):
        self.assertIn('That is a complete and correct outcome', DECIDE)

    def test_the_command_never_writes_the_refusal_log_itself(self):
        self.assertIn('belongs to `tools/compliance_guard.py` alone, and the command never '
                      'writes it directly', flat(DECIDE))


class RevisionTests(unittest.TestCase):
    def test_a_material_reframe_becomes_a_new_row(self):
        self.assertIn('origin: skeptic_reframe', DECIDE)
        self.assertIn('reframe_of:D-0NN', DECIDE)
        self.assertIn('Do not silently morph the original', flat(DECIDE))

    def test_revision_is_bounded_to_one_pass(self):
        self.assertIn('One pass', DECIDE)
        self.assertIn('A second revision is a new `/decide`', DECIDE)

    def test_the_reframe_origin_is_a_known_vocabulary_value(self):
        from tools import fa_state
        self.assertIn('skeptic_reframe', fa_state.ORIGINS)


class PromptReuseTests(unittest.TestCase):
    """The prompts live in one file. Two copies of a prompt drift, and nobody notices which won."""

    def test_decide_reuses_the_prompts_inlined_in_advise(self):
        self.assertIn('inlined in `.claude/commands/advise.md`', DECIDE)
        self.assertIn('the same\ntext, not a second copy', DECIDE)

    def test_decide_does_not_carry_its_own_copy(self):
        self.assertNotIn('### 0. Trust Boundary', DECIDE)
        self.assertIn('### 0. Trust Boundary', ADVISE)


class ViewQuarantineTests(unittest.TestCase):
    def test_a_view_is_linked_from_notes_and_never_from_a_gate(self):
        self.assertIn('view:VW-', DECIDE)
        self.assertIn('never** cited in a gate record', flat(DECIDE))
        self.assertIn('Never let a `VW-` view into a gate record', DECIDE)

    def test_the_view_block_comes_last_in_the_report(self):
        self.assertIn('last, labelled model opinion', flat(DECIDE))


class ArchiveTests(WorkspaceTest):
    def test_the_archive_is_shown_never_ranked_on(self):
        self.assertIn('shown, never ranked on', DECIDE)
        self.assertIn('must not influence what the system proposes next time', flat(DECIDE))

    def test_an_archived_concept_carries_a_revive_condition(self):
        self.assertIn('revive_when', DECIDE)
        with self.assertRaisesRegex(tr.StateError, 'revive_when is required'):
            tr.archive_add(self.work, 'buy a rental flat', 'no deposit yet', '')

    def test_an_archived_concept_round_trips(self):
        tr.archive_add(self.work, 'buy a rental flat', 'no deposit and DTI already 52 %',
                       'deposit above 20 % and DTI below 30 %')
        entries = tr.archive_list(self.work)
        self.assertEqual(entries[0]['concept'], 'buy a rental flat')
        self.assertIn('DTI below 30', entries[0]['revive_when'])
        self.assertTrue(entries[0]['archived_on'])

    def test_the_archive_holds_no_ranking_signal(self):
        tr.archive_add(self.work, 'x', 'y', 'z')
        entry = tr.archive_list(self.work)[0]
        # No score, no rating, no preference weight: nothing a later run could sort on.
        self.assertEqual(set(entry), {'concept', 'why_dropped', 'revive_when', 'archived_on'})

    def test_no_command_ranks_on_what_the_user_liked(self):
        liked = re.compile(r'(prefer|favour|favor|rank|prioriti[sz]e)[^.\n]{0,40}'
                           r'(previous|past|last time|earlier) (choice|preference|decision)', re.I)
        for path in sorted((ROOT / '.claude/commands').glob('*.md')):
            with self.subTest(command=path.stem):
                self.assertIsNone(liked.search(path.read_text(encoding='utf-8')))


class AcceptDeclineTests(WorkspaceTest):
    """Accepting a recommendation is not the same as doing it, and the tracker keeps them apart."""

    def test_the_command_separates_a_decision_from_an_action(self):
        self.assertIn('record a **decision**, not an action', flat(DECIDE))
        self.assertIn('`/track record`, and only that writes the ledger', flat(DECIDE))
        self.assertIn('Never record what the user *did*', DECIDE)

    def test_accept_moves_vetted_to_accepted_and_execution_needs_a_further_step(self):
        tr.add(self.work, {'decision_id': 'D-001', 'origin': 'user', 'question': 'q',
                           'decision_type': 'cash', 'gate1': 'PASS', 'gate2': 'PASS',
                           'gate3': 'PASS', 'gate4': 'SUPPORTED', 'score': '74',
                           'band': 'do_now', 'status': 'vetted'})
        row = tr.update(self.work, 'D-001', status='accepted')
        self.assertEqual(row['status'], 'accepted')
        row = tr.update(self.work, 'D-001', status='executed')
        self.assertEqual(row['status'], 'executed')

    def test_a_decline_is_terminal(self):
        tr.add(self.work, {'decision_id': 'D-001', 'origin': 'user', 'question': 'q',
                           'decision_type': 'cash', 'gate1': 'PASS', 'gate2': 'PASS',
                           'gate3': 'PASS', 'gate4': 'SUPPORTED', 'score': '74',
                           'band': 'do_now', 'status': 'vetted'})
        tr.update(self.work, 'D-001', status='declined')
        with self.assertRaisesRegex(tr.StateError, 'terminal'):
            tr.update(self.work, 'D-001', status='accepted')


class GuardIntegrationTests(WorkspaceTest):
    """The path a tip actually takes: screened, refused, logged, and never scored."""

    def test_a_tip_never_reaches_the_scorer(self):
        from tools import compliance_guard as cg
        from tools import decision_score as ds

        tip = 'My friend who works at Synthetic Corp told me the results are not public yet.'
        screened = cg.screen(tip)
        self.assertEqual(screened['verdict'], 'mnpi_suspected')
        cg.log_refusal(self.work, screened, text=tip, source='user', decision_id='D-001')

        # With the claim refused, the decision proceeds on what is left - and Gate 3 FAILs when
        # the thesis needed it, which decision_score records with its evidence quoted.
        record = ds.score({
            'decision_id': 'D-001', 'ips_version': '1.0',
            'gates': {
                'gate1_affordability': {'verdict': 'PASS', 'evidence': ['ledger:cash_flow.*']},
                'gate2_suitability': {'verdict': 'PASS', 'evidence': ['ips:max_equity_pct']},
                'gate3_legal_tax': {'verdict': 'FAIL',
                                    'evidence': ['compliance_guard: mnpi_suspected insider_tip']},
                'gate4_evidence_coverage': {'verdict': 'UNDETERMINED', 'evidence': ['Q-x']},
            },
        })
        self.assertEqual(record['band'], 'gated')
        self.assertIsNone(record['score'])
        events = cg.read_chain(self.work / cg.REFUSALS, 'refusals')
        self.assertEqual(events[0]['payload']['decision_id'], 'D-001')

    def test_the_refusal_text_is_stored_but_the_report_is_told_not_to_repeat_it(self):
        self.assertIn('without repeating the claim',
                      (ROOT / '.claude/skills/financial-advisor/08-behavioural-guardrails.md'
                       ).read_text(encoding='utf-8'))
