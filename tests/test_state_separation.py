"""Each command declares its write set, and the declaration matches the ownership table.

Plan section 3 says one writer per location. Prose can say that; this test is what makes it true.
Every command file must open with a State separation block, name only paths it owns, and name the
ones it must never touch. The table below is the ownership table from the plan, transcribed once.
"""
import re
import unittest

from support import ROOT

COMMANDS = ROOT / '.claude' / 'commands'

# command -> the paths it may write. A command naming anything else in its write set is a bug in
# the command file, not in this test: change the plan first, then both.
OWNERSHIP = {
    'setup': {'profile/', 'CLAUDE.md'},
    'market': {'market/', 'watch/alerts/', 'views/'},
    'advise': {'advice/', 'decision_tracker.csv', 'decisions/'},
    'decide': {'decision_tracker.csv', 'decisions/', 'views/', 'compliance/'},
    'ips': {'ips/'},
    # `/track snapshot` is the one writer of profile/balance_sheet.json other than /setup, and it
    # may replace only these two arrays. The field names appear in its write set for that reason.
    'track': {'track/', 'profile/balance_sheet.json', 'views/scores.jsonl', 'views/',
              'assets', 'holdings'},
    'watch': {'watch/'},
    'status': set(),                      # writes nothing at all
    'checkin': set(),                     # a router: every write belongs to the workflow it hands to
    'resume': {'.state/workflows/'},       # checkpoints only; domain writes belong to the owner
    'reset': {'.reset-trash/', 'market/queries.json'},
}

# Locations no command may ever claim to write directly; a tool owns them outright.
# `.state/workflows/` is deliberately NOT here: `/resume` legitimately drives
# tools/workflow_state.py, which remains the only writer. The refusal log is different - no command
# may write it under any circumstances, because a command that could write a refusal could also
# decline to write one.
TOOL_OWNED = {'compliance/refusals.jsonl'}

STATE_BLOCK = re.compile(r'\*\*State separation\.\*\*(.+?)(?=\n\n)', re.S)
PATH = re.compile(r'`([^`]+)`')


def command_files():
    return sorted(COMMANDS.glob('*.md'))


def state_block(path):
    """The State separation paragraph as one line - the file wraps it, the sentences do not."""
    match = STATE_BLOCK.search(path.read_text(encoding='utf-8'))
    return ' '.join(match.group(1).split()) if match else None


WRITES_ONLY = re.compile(r'writes only(.+?)(?:\. [A-Z]|$)', re.S)


def declared_writes(block):
    """The paths in the command's own `writes only ...` sentence, and nothing else.

    Scoped to that one sentence deliberately. The rest of a State separation block explains what
    the command does NOT write and which tool owns what - `/reset` mentions `compliance/` to say
    it is never in scope, `/resume` mentions `market/` to say `/market` owns it - and reading those
    as claims would make the paragraph unwritable.
    """
    match = WRITES_ONLY.search(block)
    if not match:
        return []                      # e.g. /status, which writes nothing at all
    return PATH.findall(match.group(1))


class StateSeparationTests(unittest.TestCase):
    def test_every_command_declares_a_state_separation_block(self):
        files = command_files()
        self.assertTrue(files, 'no command files found')
        for path in files:
            with self.subTest(command=path.stem):
                block = state_block(path)
                self.assertIsNotNone(block, 'missing a **State separation.** block')
                self.assertTrue(
                    any(phrase in block for phrase in
                        ('never writes', 'writes **nothing at all**')),
                    'the block must name what the command must NOT write, not only what it writes')

    def test_declared_writes_stay_inside_the_ownership_table(self):
        for path in command_files():
            with self.subTest(command=path.stem):
                self.assertIn(path.stem, OWNERSHIP, 'unknown command - add it to the table')
                owned = OWNERSHIP[path.stem]
                for mentioned in declared_writes(state_block(path)):
                    if (mentioned.startswith(('/', '-')) or mentioned.endswith('.py')
                            or ' ' in mentioned):
                        continue  # a command name, a flag or a tool invocation, not a target
                    with self.subTest(path=mentioned):
                        self.assertTrue(
                            any(mentioned.startswith(prefix) for prefix in owned),
                            f'{path.stem} claims to write {mentioned!r}, which it does not own')

    def test_no_command_claims_a_tool_owned_location(self):
        for path in command_files():
            writes = declared_writes(state_block(path))
            for location in TOOL_OWNED:
                with self.subTest(command=path.stem, location=location):
                    self.assertNotIn(location, writes)

    def test_commands_have_no_frontmatter_and_a_slash_title(self):
        for path in command_files():
            with self.subTest(command=path.stem):
                text = path.read_text(encoding='utf-8')
                self.assertFalse(text.startswith('---'))
                self.assertTrue(text.startswith(f'# /{path.stem}'))

    def test_every_command_carries_the_no_execution_promise(self):
        """A money command that never says it does not execute is one prompt away from trying."""
        for path in command_files():
            text = path.read_text(encoding='utf-8').lower()
            with self.subTest(command=path.stem):
                self.assertTrue(
                    'never' in text and ('execut' in text or 'logs into' in text
                                         or 'places an order' in text),
                    'the command must state that it executes nothing')


class SetupContractTests(unittest.TestCase):
    """`/setup` is the only writer of profile/, and the only command that touches CLAUDE.md."""

    def setUp(self):
        self.text = (COMMANDS / 'setup.md').read_text(encoding='utf-8')

    def test_setup_names_the_four_instance_files(self):
        for name in ('profile/PROFILE.md', 'profile/profile.json', 'profile/balance_sheet.json',
                     'profile/risk_profile.json'):
            with self.subTest(name=name):
                self.assertIn(name, self.text)

    def test_setup_runs_the_checker_before_deriving(self):
        check = self.text.index('profile_check.py check')
        derive = self.text.index('profile_check.py derive --write')
        self.assertLess(check, derive, 'derive must not run before check passes')

    def test_setup_refuses_to_invent_defaults(self):
        for promise in ('no default is ever invented', 'never invent a number', '_(unset)_'):
            with self.subTest(promise=promise):
                self.assertIn(promise, self.text.lower())

    def test_setup_never_asks_for_credentials(self):
        self.assertIn('Do not ask for credentials', self.text)
        self.assertIn('never copied', self.text)

    def test_the_claude_md_summary_fields_are_enumerated_and_amount_free(self):
        self.assertIn('band label', self.text.lower())
        self.assertIn('No amounts', self.text)
