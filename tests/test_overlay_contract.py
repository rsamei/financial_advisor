"""A jurisdiction overlay states sourced facts or says nothing, and never loosens a base rule."""
import datetime as dt
import re
import unittest

from support import ROOT
from tools import profile_check as pc

OVERLAY = ROOT / '.claude/skills/financial-advisor/07-jurisdiction-italy.md'
TEXT = OVERLAY.read_text(encoding='utf-8')
TEMPLATE = (ROOT / '.claude/skills/financial-advisor-jurisdiction-template/SKILL.md').read_text(
    encoding='utf-8')

ROW = re.compile(r'^\| `(IT-[A-Z]+-\d+)` \|(.+)$', re.M)
UNSET = '_(unset)_'


def rows():
    """(id, row) for every rule row, with the columns named rather than indexed.

    The table is `| id | Rule | Value | Applies to | Official source | Verified on | Notes |`, so
    what follows the id is six cells. Naming them here is not decoration: indexing them by position
    is how this test first asserted that the Rule column was the Value.
    """
    for match in ROW.finditer(TEXT):
        cells = [cell.strip() for cell in match.group(2).split('|')]
        cells += [''] * (6 - len(cells))
        yield match.group(1), {
            'rule': cells[0], 'value': cells[1], 'applies': cells[2],
            'source': cells[3], 'verified': cells[4], 'notes': cells[5]}


def flat(text):
    return ' '.join(text.split())


class RowContractTests(unittest.TestCase):
    """Value, source and date travel together: two of the three is not a rule."""

    def test_there_are_rule_rows_at_all(self):
        self.assertGreaterEqual(len(list(rows())), 10)

    def test_every_row_is_either_fully_sourced_or_fully_unset(self):
        for rule_id, row in rows():
            with self.subTest(rule=rule_id):
                if row['value'] == UNSET:
                    # An unverified row may not smuggle in a source or a date that implies it is.
                    self.assertIn(row['source'], (UNSET, ''),
                                  'an unset value with a source is ambiguous')
                    self.assertIn(row['verified'], ('-', '', UNSET))
                    continue
                self.assertTrue(row['source'].startswith('https://'),
                                f'{rule_id} states a value with no official URL')
                dt.date.fromisoformat(row['verified'])   # raises if it is not a real date
                self.assertTrue(row['applies'], f'{rule_id} does not say what it applies to')

    def test_a_populated_row_quotes_its_source(self):
        for rule_id, row in rows():
            if row['value'] == UNSET:
                continue
            with self.subTest(rule=rule_id):
                self.assertIn('Quoted:', row['notes'],
                              f'{rule_id} states a value without quoting the sentence that says it')

    def test_verified_dates_are_not_in_the_future(self):
        for rule_id, row in rows():
            if row['verified'] in ('-', '', UNSET):
                continue
            with self.subTest(rule=rule_id):
                self.assertLessEqual(dt.date.fromisoformat(row['verified']), dt.date.today())

    def test_the_file_states_how_many_rows_are_verified(self):
        verified = [rule for rule, row in rows() if row['value'] != UNSET]
        self.assertEqual(len(list(rows())), 16)
        self.assertEqual(len(verified), 1, 'update the Status section when a row is verified')
        self.assertIn('One row verified of sixteen', TEXT)


class VerificationProcedureTests(unittest.TestCase):
    def test_the_archive_page_trap_is_documented(self):
        # Found live on 2026-09-20: two genuine agenziaentrate.gov.it pages, one current with 26 %,
        # one archived with 12,50 %. The procedure exists because of this.
        self.assertIn('12,50 per cento', TEXT)
        self.assertIn('26 per cento', TEXT)
        self.assertIn('archive', TEXT.lower())

    def test_the_procedure_requires_a_snapshot_and_a_quote(self):
        self.assertIn('evidence_memory.py add --url', TEXT)
        self.assertIn('Quote the sentence that states the rule', TEXT)

    def test_model_memory_is_explicitly_not_a_source(self):
        self.assertIn("recollection of a tax rate is not a source", flat(TEXT))

    def test_a_stale_verification_counts_as_unverified(self):
        self.assertIn('older than a year is treated as unverified', flat(TEXT))


class GateInteractionTests(unittest.TestCase):
    def test_an_unset_row_flags_gate_three_rather_than_failing_it(self):
        self.assertIn('makes the gate FLAG (`cap:70 tax=FLAG`), never FAIL', flat(TEXT))
        rubric = (ROOT / '.claude/skills/financial-advisor/04-decision-evaluation.md').read_text(
            encoding='utf-8')
        self.assertIn('the relevant 07 rule row is `_(unset)_`', rubric)

    def test_no_tool_applies_a_tax_rate(self):
        from tools import portfolio_math as pm
        source = (ROOT / 'tools' / 'portfolio_math.py').read_text(encoding='utf-8')
        self.assertIn('no rate is applied here', source)
        self.assertIn('No tool applies a tax rate', TEXT)
        # And the lots computation returns a gain, never a tax figure.
        self.assertNotIn('tax_eur', source)


class OverlayNeverLoosensTests(unittest.TestCase):
    """An overlay adds specifics. It may tighten a base limit; it may never widen one."""

    def test_the_rule_is_stated_in_the_overlay_and_the_template(self):
        for document in (TEXT, TEMPLATE):
            with self.subTest(document=document[:40]):
                self.assertIn('never loosens a base threshold', flat(document).lower()
                              .replace('never loosen a base threshold', 'never loosens a base threshold'))

    def test_the_overlay_states_no_limit_looser_than_the_base_rubric(self):
        """Any percentage the overlay states for a base-limited quantity must not exceed it."""
        for limit_name, base in (('crypto', max(band['max_crypto_pct'] for band in pc.LIMITS.values())),
                                 ('single issuer', max(band['max_single_issuer_pct']
                                                       for band in pc.LIMITS.values()))):
            for match in re.finditer(rf'{limit_name}[^.\n]*?(\d+(?:\.\d+)?)\s*%', TEXT, re.I):
                with self.subTest(limit=limit_name, found=match.group(0)):
                    self.assertLessEqual(float(match.group(1)), base)

    def test_the_template_requires_the_same_source_and_date_discipline(self):
        self.assertIn('verified_on', TEMPLATE)
        self.assertIn('Official source (URL)', TEMPLATE)


class HonestyTests(unittest.TestCase):
    def test_the_file_says_plainly_that_it_is_the_largest_gap(self):
        self.assertIn('largest known gap', TEXT)
        self.assertIn('deliberately not being guessed at', TEXT)

    def test_it_is_no_longer_a_stub_but_does_not_pretend_to_be_complete(self):
        self.assertNotIn('Not yet populated', TEXT)
        self.assertIn(UNSET, TEXT)
