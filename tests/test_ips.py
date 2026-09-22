"""Freezing pins the text; a limit moves only through a dated, approved deviation."""
import json
import re

from support import ROOT, WorkspaceTest
from tools import ips

IPS_COMMAND = (ROOT / '.claude/commands/ips.md').read_text(encoding='utf-8')
APPROVAL = 'Yes - I have read all nine sections and I accept these limits as my policy.'


def flat(text):
    return ' '.join(text.split())


class DraftTests(WorkspaceTest):
    def test_init_writes_every_required_section(self):
        ips.init(self.work)
        text = (self.work / ips.IPS_FILE).read_text(encoding='utf-8')
        self.assertEqual(ips.missing_sections(text), [])
        self.assertEqual(len(ips.SECTIONS), 9)

    def test_a_fresh_draft_is_entirely_unset(self):
        ips.init(self.work)
        text = (self.work / ips.IPS_FILE).read_text(encoding='utf-8')
        self.assertEqual(set(ips.unset_sections(text)), set(ips.SECTIONS))

    def test_init_refuses_to_overwrite_a_frozen_policy(self):
        ips.init(self.work)
        ips.freeze(self.work, APPROVAL, allow_unset=True)
        with self.assertRaisesRegex(ips.StateError, 'frozen'):
            ips.init(self.work)

    def test_status_says_what_is_still_unanswered(self):
        self.assertFalse(ips.status(self.work)['exists'])
        ips.init(self.work)
        result = ips.status(self.work)
        self.assertTrue(result['exists'])
        self.assertFalse(result['frozen'])
        self.assertIn('Debt policy', result['unset_sections'])


class FreezeTests(WorkspaceTest):
    def fill(self):
        ips.init(self.work)
        path = self.work / ips.IPS_FILE
        text = path.read_text(encoding='utf-8').replace('_(unset)_', 'decided')
        path.write_text(text, encoding='utf-8')
        return path

    def test_freezing_pins_the_sha256(self):
        path = self.fill()
        lock = ips.freeze(self.work, APPROVAL)
        self.assertEqual(lock['version'], 1)
        self.assertEqual(lock['sha256'], ips.file_sha256(path))
        self.assertEqual(ips.check(self.work)['state'], 'frozen')

    def test_any_edit_after_freezing_fails_the_check(self):
        path = self.fill()
        ips.freeze(self.work, APPROVAL)
        path.write_text(path.read_text(encoding='utf-8') + '\nequity limit raised to 90 %\n',
                        encoding='utf-8')
        result = ips.check(self.work)
        self.assertFalse(result['ok'])
        self.assertEqual(result['state'], 'modified')
        self.assertIn('A policy that can be edited between decisions is not a policy',
                      ' '.join(result['problems']))

    def test_a_single_character_change_is_detected(self):
        path = self.fill()
        ips.freeze(self.work, APPROVAL)
        text = path.read_text(encoding='utf-8')
        path.write_text(text.replace('Version 1', 'Version 2', 1), encoding='utf-8')
        self.assertEqual(ips.check(self.work)['state'], 'modified')

    def test_a_placeholder_cannot_be_frozen(self):
        ips.init(self.work)
        with self.assertRaisesRegex(ips.StateError, r'still _\(unset\)_'):
            ips.freeze(self.work, APPROVAL)

    def test_a_hollow_approval_is_refused(self):
        self.fill()
        for hollow in ('yes', 'ok', 'approved', 'The user agreed', 'lgtm'):
            with self.subTest(approval=hollow):
                with self.assertRaisesRegex(ips.StateError, "user's own words"):
                    ips.freeze(self.work, hollow)

    def test_the_approval_is_stored_verbatim(self):
        self.fill()
        lock = ips.freeze(self.work, APPROVAL)
        self.assertEqual(lock['approval'], APPROVAL)

    def test_freeze_blocked_exits_seven(self):
        self.fill()
        result = self.run_tool('ips.py', '--root', self.work, 'freeze', '--approval', 'yes',
                               ok=False)
        self.assertNotIn('Traceback', result.stderr)
        self.assertIn("own words", result.stderr)

    def test_an_unfilled_draft_is_blocked_for_a_different_reason(self):
        ips.init(self.work)
        result = self.run_tool('ips.py', '--root', self.work, 'freeze', '--approval', APPROVAL,
                               ok=False)
        self.assertIn('would pin a policy that says nothing', result.stderr)


class DeviationTests(WorkspaceTest):
    def frozen(self):
        ips.init(self.work)
        path = self.work / ips.IPS_FILE
        path.write_text(path.read_text(encoding='utf-8').replace('_(unset)_', 'decided'),
                        encoding='utf-8')
        ips.freeze(self.work, APPROVAL)
        return path

    def test_a_deviation_bumps_the_version_and_refreezes(self):
        self.frozen()
        result = ips.deviate(
            self.work, section='Target allocation and rebalance bands',
            before='equity 60 %', after='equity 70 %',
            reason='horizon extended by five years after the house purchase slipped',
            approval='Yes, raise equity to 70 % now that the deposit is not needed until 2036.')
        self.assertEqual(result['deviation_id'], 'DV-001')
        self.assertEqual(result['lock']['version'], 2)
        self.assertEqual(ips.check(self.work)['state'], 'frozen')

    def test_the_deviation_row_records_before_after_reason_and_approval(self):
        self.frozen()
        ips.deviate(self.work, section='Debt policy', before='repay above 6 %',
                    after='repay above 4 %', reason='mortgage rate reset upward',
                    approval='Yes, I want to clear the loan before investing more.')
        text = (self.work / ips.DEVIATIONS_FILE).read_text(encoding='utf-8')
        self.assertIn('DV-001', text)
        self.assertIn('repay above 6 %', text)
        self.assertIn('repay above 4 %', text)
        self.assertIn('mortgage rate reset upward', text)
        self.assertIn('clear the loan before investing', text)

    def test_deviation_ids_increment(self):
        self.frozen()
        for index in range(3):
            ips.deviate(self.work, section='Debt policy', before='a', after='b',
                        reason=f'change {index}',
                        approval='Yes, I have thought about this and I want the change.')
        text = (self.work / ips.DEVIATIONS_FILE).read_text(encoding='utf-8')
        self.assertIn('DV-003', text)
        self.assertEqual(ips.read_lock(self.work)['version'], 4)

    def test_deviating_from_an_unfrozen_ips_is_refused(self):
        ips.init(self.work)
        with self.assertRaisesRegex(ips.StateError, 'not frozen'):
            ips.deviate(self.work, section='Debt policy', before='a', after='b', reason='c',
                        approval=APPROVAL)

    def test_an_unknown_section_is_refused(self):
        self.frozen()
        with self.assertRaisesRegex(ips.StateError, 'unknown section'):
            ips.deviate(self.work, section='Vibes', before='a', after='b', reason='c',
                        approval=APPROVAL)

    def test_a_deviation_needs_a_real_approval_too(self):
        self.frozen()
        with self.assertRaisesRegex(ips.StateError, "own words"):
            ips.deviate(self.work, section='Debt policy', before='a', after='b', reason='c',
                        approval='ok')

    def test_a_pipe_in_a_field_cannot_break_the_table(self):
        self.frozen()
        ips.deviate(self.work, section='Debt policy', before='a | b', after='c',
                    reason='rates | changed',
                    approval='Yes, I accept this change to the debt policy as written.')
        text = (self.work / ips.DEVIATIONS_FILE).read_text(encoding='utf-8')
        row = [line for line in text.splitlines() if line.startswith('| DV-001')][0]
        self.assertEqual(row.count('|') - row.count('\\|'), 8)   # 7 columns, 8 delimiters


class NeverAllowlistedTests(WorkspaceTest):
    """freeze and deviate must always reach the permission prompt."""

    def test_the_ips_tool_is_not_in_the_permission_allowlist(self):
        from tools import security_guards as guard
        for entry in guard.ALLOWED_PERMISSIONS:
            with self.subTest(entry=entry):
                self.assertNotIn('ips.py', entry)

    def test_settings_json_does_not_carry_it_either(self):
        settings = json.loads((ROOT / '.claude/settings.json').read_text(encoding='utf-8'))
        for entry in settings['permissions']['allow']:
            with self.subTest(entry=entry):
                self.assertNotIn('ips.py', entry)

    def test_the_reason_is_written_down_where_someone_will_read_it(self):
        self.assertIn('deliberately **absent from the permission allowlist**', ips.__doc__)
        self.assertIn('always reach the permission prompt', IPS_COMMAND)


class CommandContractTests(WorkspaceTest):
    def test_the_command_names_all_nine_sections(self):
        for section in ips.SECTIONS:
            with self.subTest(section=section):
                self.assertIn(section, IPS_COMMAND)

    def test_the_reviewer_proposes_questions_not_values(self):
        self.assertIn('suggested_question_for_the_user', IPS_COMMAND)
        self.assertIn('proposes questions, never values', flat(IPS_COMMAND))
        self.assertIn('A limit the user did not choose is not their policy', IPS_COMMAND)

    def test_the_command_asks_whether_a_deviation_is_circumstance_or_mood(self):
        self.assertIn('change of\ncircumstances, or a change of mood', IPS_COMMAND)

    def test_the_two_kinds_of_drift_are_kept_apart(self):
        self.assertIn('The document changed', IPS_COMMAND)
        self.assertIn('The portfolio drifted', IPS_COMMAND)

    def test_the_missing_ips_cap_is_explained_not_just_named(self):
        self.assertIn('cap:65 ips=missing', IPS_COMMAND)
        self.assertIn('each decision is argued from scratch', flat(IPS_COMMAND))


class CliTests(WorkspaceTest):
    def test_check_exits_one_when_there_is_no_ips(self):
        result = self.run_tool('ips.py', '--root', self.work, 'check', ok=False)
        self.assertIn('cap:65 ips=missing', result.stdout)

    def test_the_full_lifecycle_through_the_cli(self):
        self.run_tool('ips.py', '--root', self.work, 'init')
        path = self.work / ips.IPS_FILE
        path.write_text(path.read_text(encoding='utf-8').replace('_(unset)_', 'decided'),
                        encoding='utf-8')
        self.run_tool('ips.py', '--root', self.work, 'freeze', '--approval', APPROVAL)
        self.run_tool('ips.py', '--root', self.work, 'check')
        self.run_tool('ips.py', '--root', self.work, 'deviate',
                      '--section', 'Cash and emergency policy',
                      '--before', '6 months', '--after', '9 months',
                      '--reason', 'income became variable after the contract change',
                      '--approval', 'Yes, hold nine months now that my income is variable.')
        self.run_tool('ips.py', '--root', self.work, 'check')
        self.assertEqual(ips.read_lock(self.work)['version'], 2)
