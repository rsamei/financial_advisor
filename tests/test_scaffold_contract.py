"""Phase 0 contract: the layout, the ownership table and the reset scopes agree with the plan.

This is the test that keeps the scaffold honest while most of the system is still stubs. It
asserts what Phase 0 promised and, deliberately, asserts that the stubs still declare themselves
stubs - so nobody can quote one as a rule without the suite noticing.
"""
import re
import unittest

from support import ROOT
from tools import reset_repo, security_guards as guard

REFERENCE_RE = re.compile(r'^\d{2}-.+\.md$')


class LayoutTests(unittest.TestCase):
    def test_root_documents_exist(self):
        for name in ('CLAUDE.md', 'AGENTS.md', 'README.md', 'SETUP.md', 'SECURITY.md', 'LICENSE',
                     '.gitignore', 'requirements-dev.txt', 'decision_tracker.template.csv',
                     '.claude/settings.json'):
            with self.subTest(name=name):
                self.assertTrue((ROOT / name).is_file(), f'missing {name}')

    def test_tracked_directories_exist(self):
        for name in ('providers', 'tools', 'tests', 'documents', 'market'):
            with self.subTest(name=name):
                self.assertTrue((ROOT / name).is_dir(), f'missing {name}/')

    def test_state_directories_are_created_on_demand(self):
        # These hold amounts, so they are gitignored and absent from a fresh clone. The tools
        # that write them call mkdir(parents=True, exist_ok=True), so requiring them up front
        # would fail for everyone but the author. Assert only that nothing else has taken the
        # name: if the path exists at all, it has to be the directory the tools expect.
        for name in ('profile', 'decisions', 'advice', 'ips', 'track', 'watch', 'views',
                     'compliance'):
            with self.subTest(name=name):
                path = ROOT / name
                self.assertFalse(path.exists() and not path.is_dir(),
                                 f'{name} exists but is not a directory')

    def test_no_stray_placeholder_file(self):
        self.assertFalse((ROOT / 'test.md').exists(),
                         'the empty test.md from the bare repository must be gone')


class IgnoreRuleTests(unittest.TestCase):
    """Every directory that can hold an amount must be ignored, and the ignore file must say so."""

    def test_every_personal_directory_is_ignored(self):
        rules = set(guard.REQUIRED_IGNORE_RULES)
        for directory in ('profile', 'documents', 'decisions', 'advice', 'ips', 'track', 'watch',
                          'views', 'compliance'):
            with self.subTest(directory=directory):
                self.assertIn(f'{directory}/**', rules)
        self.assertIn('decision_tracker.csv', rules)

    def test_query_provenance_is_not_ignored(self):
        # market/queries.json is tracked on purpose: no personal data, and it is what makes a
        # market claim replayable.
        self.assertNotIn('market/queries.json', guard.REQUIRED_IGNORE_RULES)
        self.assertNotIn('market/**', guard.REQUIRED_IGNORE_RULES)


class ResetScopeTests(unittest.TestCase):
    def test_scopes_match_the_plan(self):
        self.assertEqual(set(reset_repo.SCOPES),
                         {'profile', 'market', 'decisions', 'advice', 'ips', 'track', 'watch',
                          'views', 'state'})
        self.assertEqual(set(reset_repo.SCOPE_MOVES), set(reset_repo.SCOPES))

    def test_compliance_is_never_resettable(self):
        # A refusal log that can be reset is not a refusal log.
        self.assertNotIn('compliance', reset_repo.SCOPES)
        for paths in reset_repo.SCOPE_MOVES.values():
            for path in paths:
                self.assertFalse(path.startswith('compliance'), path)

    def test_query_provenance_is_emptied_not_moved(self):
        self.assertEqual(reset_repo.SCOPE_REWRITES['market'], ('market/queries.json',))


class StubHonestyTests(unittest.TestCase):
    """A reference is either a declared stub or a populated file with its own contract test.

    POPULATED grows one phase at a time. Adding a name here without adding that file's contract
    test is the mistake this pairing exists to make visible.
    """

    POPULATED = {
        # Phase 1 - asserted by tests/test_profile_check.py::ReferenceContractTests
        '01-financial-profile.md',
        '02-balance-sheet-and-cashflow.md',
        '03-risk-profile.md',
        # Phase 2 - asserted by tests/test_market_contract.py
        '05-market-intelligence.md',
        # Phase 3 - asserted by tests/test_decision_contract.py and
        # tests/test_tracker_and_report.py
        '04-decision-evaluation.md',
        '06-portfolio-construction.md',
        '08-behavioural-guardrails.md',
        '09-reporting-templates.md',
        # Phase 6 - asserted by tests/test_track_actions.py
        '11-outcome-review.md',
        # Phase 6b - asserted by tests/test_scenario_contract.py
        '12-scenario-view.md',
        # Household planning - asserted by tests/test_household_plan.py
        '13-household-planning.md',
        # Phase 7 - asserted by tests/test_watch_and_ops.py
        '10-workflow-checkpoints.md',
        # Phase 8 - asserted by tests/test_overlay_contract.py. Populated means "has its table and
        # its verification procedure", not "every row is verified": 15 of 16 rows are _(unset)_ on
        # purpose, and the file says so.
        '07-jurisdiction-italy.md',
    }

    def test_every_reference_is_either_a_declared_stub_or_populated(self):
        references = sorted(p for p in (ROOT / '.claude/skills/financial-advisor').glob('*.md')
                            if REFERENCE_RE.match(p.name))
        self.assertEqual(len(references), 13)
        for path in references:
            with self.subTest(name=path.name):
                text = path.read_text(encoding='utf-8')
                self.assertIn('Library file', text)
                if path.name in self.POPULATED:
                    self.assertNotIn('Not yet populated', text,
                                     'listed as populated but still carries the stub marker')
                else:
                    self.assertIn('Not yet populated', text,
                                  'a populated reference must drop the stub marker, gain its own '
                                  'contract test, and be listed in POPULATED - in one change')

    def test_the_router_and_the_root_document_both_declare_the_build_status(self):
        for name in ('.claude/skills/financial-advisor/SKILL.md', 'CLAUDE.md'):
            with self.subTest(name=name):
                self.assertIn('Build status', (ROOT / name).read_text(encoding='utf-8'))


class NoExecutionTests(unittest.TestCase):
    """Nothing shipped may explain how to place an order or log into a broker."""

    FORBIDDEN = (re.compile(r'\bplace (?:an? )?order\b', re.I),
                 re.compile(r'\blog ?in to (?:your )?broker\b', re.I),
                 re.compile(r'\bbroker (?:password|credentials|login)\b', re.I),
                 re.compile(r'\bapi (?:secret|key) for (?:your )?broker\b', re.I))

    def test_no_shipped_instruction_explains_execution(self):
        paths = [*(ROOT / '.claude').rglob('*.md'), *(ROOT / 'tools').glob('*.py'),
                 ROOT / 'CLAUDE.md', ROOT / 'README.md', ROOT / 'SECURITY.md', ROOT / 'SETUP.md']
        for path in paths:
            text = path.read_text(encoding='utf-8')
            for line in text.splitlines():
                # A negation ("nothing places an order") is the point of the rule, not a breach.
                if re.search(r'\b(never|no|not|nothing|refus)', line, re.I):
                    continue
                for pattern in self.FORBIDDEN:
                    with self.subTest(path=path.name, line=line[:60]):
                        self.assertIsNone(pattern.search(line))
