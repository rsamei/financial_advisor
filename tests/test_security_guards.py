"""The guards are the only thing standing between a balance sheet and a git remote."""
import json
from unittest.mock import patch

from support import ROOT, WorkspaceTest
from tools import security_guards as guard


class SecurityTests(WorkspaceTest):
    def setUp(self):
        super().setUp()
        guard.errors.clear()
        self.addCleanup(guard.errors.clear)

    def test_repository_guards_and_exact_permissions(self):
        self.run_tool('security_guards.py')
        settings = json.loads((ROOT / '.claude/settings.json').read_text(encoding='utf-8'))
        self.assertEqual(set(settings['permissions']['allow']), guard.ALLOWED_PERMISSIONS)
        self.assertEqual(guard.ALLOWED_HOOKS, set())

    def test_automatic_hook_and_blanket_shell_are_rejected(self):
        (self.work / '.claude').mkdir()
        self.write('.claude/settings.json',
                   {'permissions': {'allow': ['Bash(*)']},
                    'hooks': {'SessionStart': [{'hooks': [{'command': 'echo injected'}]}]}})
        with patch.object(guard, 'ROOT', self.work):
            guard.check_permissions()
        self.assertTrue(any('hook not in' in e for e in guard.errors))
        self.assertTrue(any('permission not in' in e for e in guard.errors))

    def test_fetchers_and_installers_can_never_be_pre_approved(self):
        (self.work / '.claude').mkdir()
        self.write('.claude/settings.json', {'permissions': {'allow': [
            'Bash(curl https://example.invalid:*)', 'Bash(python -c:*)', 'Bash(pip install:*)']}})
        with patch.object(guard, 'ROOT', self.work):
            guard.check_permissions()
        joined = ' '.join(guard.errors)
        for fragment in ('raw fetcher', 'arbitrary code', 'package installer'):
            self.assertIn(fragment, joined)

    def test_a_reincluded_ignore_rule_is_rejected(self):
        (self.work / '.gitignore').write_text(
            '\n'.join(guard.REQUIRED_IGNORE_RULES) + '\n!profile/PROFILE.md\n', encoding='utf-8')
        with patch.object(guard, 'ROOT', self.work):
            guard.check_gitignore()
        self.assertTrue(any('negation' in e for e in guard.errors))

    def test_a_missing_ignore_rule_is_rejected(self):
        kept = [r for r in guard.REQUIRED_IGNORE_RULES if r != 'profile/**']
        (self.work / '.gitignore').write_text('\n'.join(kept) + '\n', encoding='utf-8')
        with patch.object(guard, 'ROOT', self.work):
            guard.check_gitignore()
        self.assertTrue(any("'profile/**'" in e for e in guard.errors))

    def test_an_amount_in_the_tracked_profile_summary_is_rejected(self):
        (self.work / 'CLAUDE.md').write_text(
            '# Repo\n\n## Profile summary\n\nPopulated 2026-09-20. Net worth EUR 184.000, '
            'residency IT.\n\n## Workflow\n', encoding='utf-8')
        with patch.object(guard, 'ROOT', self.work):
            guard.check_profile_summary()
        self.assertTrue(any('currency amount' in e for e in guard.errors))

    def test_a_band_label_in_the_profile_summary_is_allowed(self):
        (self.work / 'CLAUDE.md').write_text(
            '# Repo\n\n## Profile summary\n\nPopulated 2026-09-20; residency IT; effective band '
            'moderate; net-worth band B3; IPS v1.\n\n## Workflow\n', encoding='utf-8')
        with patch.object(guard, 'ROOT', self.work):
            guard.check_profile_summary()
        self.assertEqual(guard.errors, [])

    def test_a_missing_profile_summary_section_is_rejected(self):
        (self.work / 'CLAUDE.md').write_text('# Repo\n\n## Workflow\n', encoding='utf-8')
        with patch.object(guard, 'ROOT', self.work):
            guard.check_profile_summary()
        self.assertTrue(any('Profile summary' in e for e in guard.errors))

    def test_a_key_shaped_string_in_a_tracked_file_is_rejected(self):
        with patch.object(guard, 'ROOT', self.work), \
                patch.object(guard, '_tracked_files', lambda: ['leaky.md']):
            (self.work / 'leaky.md').write_text(
                'FRED_API_KEY = abcdef0123456789abcdef0123456789\n', encoding='utf-8')
            guard.check_tracked_secrets()
        self.assertTrue(any('leaky.md' in e for e in guard.errors))
