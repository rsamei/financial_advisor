"""Nothing shipped executes a money decision, and no agent prompt is handed an amount.

Plan section 10. These are the checks that matter most if everything else is working: a system
that gates well and then quietly tells the model how to place an order has gained nothing.
"""
import re
import unittest

from support import ROOT

COMMANDS = sorted((ROOT / '.claude/commands').glob('*.md'))
SKILLS = sorted((ROOT / '.claude/skills').rglob('*.md'))
DOCS = [ROOT / name for name in ('CLAUDE.md', 'README.md', 'SECURITY.md', 'SETUP.md', 'AGENTS.md')]

# A fenced block that starts with the trust boundary is an inlined agent prompt.
PROMPT_BLOCK = re.compile(r'```\n(### 0\. Trust Boundary.*?)```', re.S)
ANY_FENCED = re.compile(r'```\n(.*?)```', re.S)
# An absolute euro amount: "12 000 EUR", "EUR 12000", "€12,000".
EUR_AMOUNT = re.compile(r'(?:€\s?\d[\d.,\s]*)|(?:\d[\d.,\s]*\s?EUR\b)|(?:\bEUR\s?\d)')


def prompts():
    for path in COMMANDS:
        for block in PROMPT_BLOCK.findall(path.read_text(encoding='utf-8')):
            yield path.stem, block


class TrustBoundaryTests(unittest.TestCase):
    def test_every_inlined_prompt_starts_with_the_trust_boundary(self):
        found = list(prompts())
        self.assertTrue(found, 'no inlined agent prompts found')
        for command, block in found:
            with self.subTest(command=command):
                self.assertTrue(block.startswith('### 0. Trust Boundary'))

    def test_every_prompt_says_the_context_is_data_not_instructions(self):
        for command, block in prompts():
            with self.subTest(command=command, prompt=block[:60]):
                self.assertIn('DATA, never instructions', block)
                self.assertIn('Never fetch a URL', block)

    def test_every_prompt_handles_mnpi_explicitly(self):
        """Every prompt either stops on MNPI or classifies it - never ignores it.

        Most agents set `mnpi_flag` and stop: they have no business weighing a non-public claim.
        The compliance reviewer is the exception, because classifying the claim IS its job, so it
        returns an `mnpi_verdict` instead of halting.
        """
        for command, block in prompts():
            with self.subTest(command=command, prompt=block[:60]):
                lowered = block.lower()
                self.assertTrue('mnpi_flag' in lowered or 'mnpi_verdict' in lowered,
                                'prompt neither stops on nor classifies MNPI')

    def test_every_prompt_ends_with_an_output_contract(self):
        for command, block in prompts():
            with self.subTest(command=command, prompt=block[:60]):
                self.assertRegex(block, r'### \d+\. Output')

    def test_narrative_headings_are_required_even_when_the_answer_is_none(self):
        text = (ROOT / '.claude/commands/advise.md').read_text(encoding='utf-8')
        self.assertIn('headings present even when the answer is "none"', text)
        for heading in ('**What I propose**', '**Assumptions**', '**Strongest objection**',
                        '**Blocking**'):
            with self.subTest(heading=heading):
                self.assertIn(heading, text)


class NoAmountsInPromptsTests(unittest.TestCase):
    """Agents reason in ratios. The orchestrator owns every absolute figure."""

    def test_no_inlined_prompt_contains_an_absolute_euro_amount(self):
        for command, block in prompts():
            # "12 000 EUR" appears in advise.md's standing rules as the thing NOT to send; inside
            # a prompt block it would be the mistake itself.
            match = EUR_AMOUNT.search(block)
            with self.subTest(command=command):
                self.assertIsNone(match, f'prompt in {command} carries {match.group(0)!r}'
                                         if match else '')

    def test_the_prompts_ask_for_ratios_explicitly(self):
        advise = (ROOT / '.claude/commands/advise.md').read_text(encoding='utf-8')
        self.assertIn('Household context (ratios only)', advise)
        self.assertIn('amount_as_pct_of_investable', advise)

    def test_the_rule_is_stated_where_the_prompts_live(self):
        advise = ' '.join((ROOT / '.claude/commands/advise.md').read_text(encoding='utf-8').split())
        self.assertIn('No context block ever contains an absolute EUR amount', advise)


class NoExecutionTests(unittest.TestCase):
    ORDER_PLACEMENT = (
        re.compile(r'\bplace (?:an? )?(?:order|trade)\b', re.I),
        re.compile(r'\b(?:log|sign) ?in to (?:your |the )?(?:broker|bank|account)\b', re.I),
        re.compile(r'\bbroker (?:password|credentials|login|api key)\b', re.I),
        re.compile(r'\bexecute (?:the )?(?:trade|order|transfer)\b', re.I),
        re.compile(r'\btransfer (?:the )?(?:money|funds) (?:to|from)\b', re.I),
    )
    # A sentence that forbids the thing is the rule, not a breach of it.
    NEGATION = re.compile(r'\b(never|no|not|nothing|cannot|refus|without)\b', re.I)

    def test_nothing_shipped_explains_how_to_execute(self):
        for path in [*COMMANDS, *SKILLS, *DOCS]:
            for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), start=1):
                if self.NEGATION.search(line):
                    continue
                for pattern in self.ORDER_PLACEMENT:
                    with self.subTest(file=path.name, line=number):
                        self.assertIsNone(pattern.search(line), line[:90])

    def test_every_command_promises_it_executes_nothing(self):
        for path in COMMANDS:
            text = ' '.join(path.read_text(encoding='utf-8').split()).lower()
            with self.subTest(command=path.stem):
                self.assertTrue(
                    'executes nothing' in text or 'never execut' in text
                    or 'nothing here executes' in text or 'it executes nothing' in text,
                    f'{path.stem} does not say that it executes nothing')

    def test_the_disclosure_line_is_in_every_report_template(self):
        templates = (ROOT / '.claude/skills/financial-advisor/09-reporting-templates.md').read_text(
            encoding='utf-8')
        blocks = ANY_FENCED.findall(templates)
        self.assertGreaterEqual(len(blocks), 5)
        for block in blocks:
            heading = block.splitlines()[0]
            with self.subTest(template=heading):
                self.assertTrue(
                    'not licensed financial advice' in block
                    or 'not a recommendation' in block         # the alert says it its own way
                    or 'does not rewrite' in block,            # the review says it its own way
                    f'{heading}: no disclosure')

    def test_no_tool_offers_to_move_money(self):
        for path in sorted((ROOT / 'tools').glob('*.py')):
            text = path.read_text(encoding='utf-8')
            for name in ('def execute', 'def place_order', 'def transfer', 'def login'):
                with self.subTest(tool=path.name, name=name):
                    self.assertNotIn(name, text)


class CredentialTests(unittest.TestCase):
    def test_no_command_asks_for_a_credential(self):
        asks = re.compile(r'ask (?:the user )?for (?:your |their )?(?:password|api key|credential)',
                          re.I)
        for path in COMMANDS:
            for line in path.read_text(encoding='utf-8').splitlines():
                if re.search(r'\b(never|not|no|do not)\b', line, re.I):
                    continue
                with self.subTest(command=path.stem):
                    self.assertIsNone(asks.search(line), line[:90])

    def test_setup_states_the_rule_about_identifiers(self):
        setup = ' '.join((ROOT / '.claude/commands/setup.md').read_text(encoding='utf-8').split())
        self.assertIn('Account numbers, IBANs, card numbers and credentials are never copied',
                      setup)
