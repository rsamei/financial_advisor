"""The linter keeps a rubric from firing itself outside the command meant to execute it."""
from unittest.mock import patch

from support import ROOT, WorkspaceTest
from tools import lint_skills as lint


class SkillLintTests(WorkspaceTest):
    def setUp(self):
        super().setUp()
        lint.errors.clear()
        self.addCleanup(lint.errors.clear)

    def test_repository_lints(self):
        self.run_tool('lint_skills.py')

    def test_reference_cannot_become_auto_invocable(self):
        path = self.work / '04-reference.md'
        path.write_text('---\nframework_version: 1.0.0\nname: accidental\n---\n', encoding='utf-8')
        with patch.object(lint, 'ROOT', self.work):
            lint.check_reference(path)
        self.assertTrue(any("must NOT declare 'name'" in e for e in lint.errors))

    def test_reference_without_a_version_is_rejected(self):
        path = self.work / '04-reference.md'
        path.write_text('---\nowner: someone\n---\n', encoding='utf-8')
        with patch.object(lint, 'ROOT', self.work):
            lint.check_reference(path)
        self.assertTrue(any("missing 'framework_version'" in e for e in lint.errors))

    def test_skill_needs_a_triggers_list(self):
        path = self.work / 'SKILL.md'
        path.write_text('---\nname: sample\ndescription: "Does things"\n'
                        'allowed-tools: Read\nframework_version: 0.1.0\n---\n', encoding='utf-8')
        with patch.object(lint, 'ROOT', self.work):
            lint.check_skill(path)
        self.assertTrue(any('Triggers on:' in e for e in lint.errors))

    def test_missing_tool_target_is_rejected(self):
        path = self.work / 'SKILL.md'
        path.write_text('---\nname: sample\ndescription: "Triggers on: sample"\n'
                        'allowed-tools: Bash(python tools/missing.py:*)\n'
                        'framework_version: 0.1.0\n---\n', encoding='utf-8')
        with patch.object(lint, 'ROOT', self.work):
            lint.check_skill(path)
        self.assertTrue(any('missing file' in e for e in lint.errors))

    def test_command_files_carry_no_frontmatter(self):
        path = self.work / 'advise.md'
        path.write_text('---\nname: advise\n---\n# /advise\n', encoding='utf-8')
        with patch.object(lint, 'ROOT', self.work):
            lint.check_command(path)
        self.assertTrue(any('must NOT have frontmatter' in e for e in lint.errors))

    def test_the_twelve_references_and_the_skills_exist(self):
        skills = ROOT / '.claude/skills'
        self.assertEqual(
            sorted(p.stem for p in (skills / 'financial-advisor').glob('[0-9][0-9]-*.md')),
            ['01-financial-profile', '02-balance-sheet-and-cashflow', '03-risk-profile',
             '04-decision-evaluation', '05-market-intelligence', '06-portfolio-construction',
             '07-jurisdiction-italy', '08-behavioural-guardrails', '09-reporting-templates',
             '10-workflow-checkpoints', '11-outcome-review', '12-scenario-view',
             '13-household-planning'])
        for name in ('financial-advisor', 'market-scout',
                     'financial-advisor-jurisdiction-template'):
            self.assertTrue((skills / name / 'SKILL.md').is_file(), name)
