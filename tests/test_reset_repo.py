"""Reset moves state; it never deletes it, and it never acts on a stale approval."""
from support import WorkspaceTest
from tools import reset_repo


class ResetTests(WorkspaceTest):
    def seed(self):
        (self.work / 'profile').mkdir()
        (self.work / 'profile' / 'balance_sheet.json').write_text('{"synthetic": true}',
                                                                  encoding='utf-8')
        (self.work / 'market').mkdir()
        (self.work / 'market' / 'observations.json').write_text('[]', encoding='utf-8')
        (self.work / 'market' / 'queries.json').write_text('{"Q-synthetic": {}}', encoding='utf-8')

    def test_preview_lists_files_and_apply_moves_them_to_trash(self):
        self.seed()
        preview = reset_repo.plan(self.work, ['profile'])
        self.assertEqual([e['path'] for e in preview['entries']],
                         ['profile/balance_sheet.json'])
        trash = reset_repo.apply(self.work, ['profile'], preview['token'])
        self.assertFalse((self.work / 'profile' / 'balance_sheet.json').exists())
        self.assertTrue((trash / 'profile' / 'balance_sheet.json').is_file())
        self.assertTrue((trash / 'MANIFEST.json').is_file())

    def test_a_changed_file_invalidates_the_approval(self):
        self.seed()
        token = reset_repo.plan(self.work, ['profile'])['token']
        (self.work / 'profile' / 'profile.json').write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(reset_repo.StateError, 'run preview again'):
            reset_repo.apply(self.work, ['profile'], token)
        self.assertTrue((self.work / 'profile' / 'balance_sheet.json').is_file())

    def test_query_provenance_is_emptied_in_place_not_moved(self):
        self.seed()
        preview = reset_repo.plan(self.work, ['market'])
        actions = {e['path']: e['action'] for e in preview['entries']}
        self.assertEqual(actions['market/queries.json'], 'reset to {}')
        self.assertEqual(actions['market/observations.json'], 'move')
        reset_repo.apply(self.work, ['market'], preview['token'])
        self.assertEqual((self.work / 'market' / 'queries.json').read_text(encoding='utf-8'), '{}\n')

    def test_an_unknown_scope_is_refused(self):
        with self.assertRaisesRegex(reset_repo.StateError, 'unknown scope'):
            reset_repo.plan(self.work, ['compliance'])
        with self.assertRaisesRegex(reset_repo.StateError, 'at least one scope'):
            reset_repo.plan(self.work, [])

    def test_cli_failure_has_no_traceback(self):
        result = self.run_tool('reset_repo.py', '--root', self.work, 'apply',
                               '--scope', 'profile', '--token', 'wrong', ok=False)
        self.assertNotIn('Traceback', result.stderr)
