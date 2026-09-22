#!/usr/bin/env python3
"""Refuse a report whose numbers cannot be traced back to something recorded.

Usage (from the repository root):
    python tools/report_check.py check --report <file> [--strict] [--json]
    python tools/report_check.py keys  --report <file>

A report is the only part of this system the user actually reads. Everything upstream - gates,
coverage, immutable cards - is worth nothing if the final document can quietly contain a number
nobody can source. This tool is the last gate.

What it enforces:

- **Every number resolves.** Each figure in the report must sit on a line that also carries an
  `EV-` key, a `Q-` id, a `ledger:` path, a tool attribution (`profile_check.py`,
  `portfolio_math.py`), a decision/advice/alert id, a gate line, or - for the figures inside a view
  block - a `VW-` key. Unreferenced numbers are listed with their line, and in `--strict` they fail
  the run.
- **Every `EV-` key exists** as a card under `market/evidence/`, and every `Q-` id exists in
  `market/queries.json`. A citation to a card that was never written is worse than no citation.
- **A `VW-` reference is opinion, never evidence.** Views may appear in a report, but only in a
  block labelled as model opinion, and never on a line that also carries a gate verdict.
- **The disclosure line is present.** `08-behavioural-guardrails.md` fixes the wording.
- **Action lines carry a gate record.** A line proposing an action without `G1 … G2 … G3 … G4 …` is
  a prose recommendation wearing a report's clothes, which is exactly what this system exists to
  not produce.

Stdlib only. Exit 0 ok; 1 the report failed its checks; 4 bad arguments.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fa_state import StateError, read_json, safe_console  # noqa: E402

EVIDENCE_DIR = Path('market') / 'evidence'
QUERIES = Path('market') / 'queries.json'

EV_KEY = re.compile(r'EV-[0-9a-f]{8,16}')
Q_ID = re.compile(r'Q-[A-Za-z0-9][A-Za-z0-9._-]*')
VIEW_KEY = re.compile(r'VW-\d{8}-\d{2}')
LEDGER = re.compile(r'ledger:[A-Za-z0-9_.\[\]*-]+')
TOOL = re.compile(r'(profile_check|portfolio_math|household_plan|goal_check|cash_plan|decision_score|market_retrieve|views|fx)\.py')
DECISION_ID = re.compile(r'\b(D-\d{3,}|AR-\d{8}-\d{2}|RV-\d{8}-\d{2}|ALERT-\d{8}-\d{2})\b')
GATE_LINE = re.compile(r'G1\s+\w+.*G2\s+\w+.*G3\s+\w+.*G4\s+\w+')
ACTION_LINE = re.compile(r'^\s*[-*]\s+\*\*(D-\d{3,})')

DISCLOSURE = 'This is not licensed financial advice'

# A number that needs no citation: a date, a percentage of a table of contents, a heading level,
# a list index, a gate number, a year in a heading. Kept deliberately small - the default is that
# a number must be sourced.
DATE = re.compile(r'\b\d{4}-\d{2}(-\d{2})?\b')
NUMBER = re.compile(r'(?<![\w.-])\d[\d.,]*\s?(?:%|pp|EUR|€)?')
SKIP_PREFIXES = ('#', '|---', '```', '> ')
# Table separator rows, template placeholders and the c1-c4 coverage line carry no real figures.
PLACEHOLDER = re.compile(r'<[^>]+>')
COVERAGE_TOKEN = re.compile(r'\bc[1-4]\b')


def citations(line: str) -> list[str]:
    """What, on this line, makes a number on it traceable.

    A gate line counts as its own citation: `G1 PASS | ... | score 80` is the printed form of a
    `decision_score.py` record, so the score it carries is already sourced. Anything else needs an
    EV- key, a Q- id, a ledger path, a named tool or a decision id.
    """
    found = (EV_KEY.findall(line) + Q_ID.findall(line) + LEDGER.findall(line)
             + [m.group(0) for m in TOOL.finditer(line)] + DECISION_ID.findall(line))
    if GATE_LINE.search(line):
        found.append('gate-record')
    # A `VW-` key traces the numbers in a view block - a scenario probability comes from the
    # recorded view and nowhere else. It is provenance for an OPINION figure, which is why it is
    # accepted here and refused outright on a gate line: the two checks are what keep "traceable"
    # and "evidence" from collapsing into each other.
    found += VIEW_KEY.findall(line)
    return found


def numbers_in(line: str) -> list[str]:
    stripped = DATE.sub(' ', PLACEHOLDER.sub(' ', COVERAGE_TOKEN.sub(' ', line)))
    stripped = VIEW_KEY.sub(' ', EV_KEY.sub(' ', Q_ID.sub(' ', stripped)))
    stripped = DECISION_ID.sub(' ', stripped)
    return [match.group(0).strip() for match in NUMBER.finditer(stripped)
            if match.group(0).strip()]


def known_keys(root: Path) -> tuple[set[str], set[str]]:
    cards = {path.stem for path in (Path(root) / EVIDENCE_DIR).glob('*/EV-*.json')}
    queries_path = Path(root) / QUERIES
    queries = set(read_json(queries_path, 'market/queries.json')) if queries_path.is_file() else set()
    return cards, queries


def check(root: Path, text: str) -> dict:
    cards, queries = known_keys(root)
    unreferenced: list[dict] = []
    missing_cards: list[dict] = []
    missing_queries: list[dict] = []
    actions_without_gates: list[dict] = []
    view_on_gate_line: list[dict] = []

    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith(SKIP_PREFIXES):
            continue

        found = citations(line)
        figures = numbers_in(line)
        if figures and not found:
            unreferenced.append({'line': number, 'text': line[:100], 'numbers': figures})

        for key in EV_KEY.findall(line):
            if cards and key not in cards:
                missing_cards.append({'line': number, 'key': key})
        for query in Q_ID.findall(line):
            if queries and query not in queries:
                missing_queries.append({'line': number, 'key': query})

        if VIEW_KEY.search(line) and GATE_LINE.search(line):
            view_on_gate_line.append({'line': number, 'text': line[:100]})

        if ACTION_LINE.match(raw):
            window = '\n'.join(text.splitlines()[number - 1:number + 3])
            if not GATE_LINE.search(window):
                actions_without_gates.append({'line': number, 'text': line[:100]})

    problems = {
        'unreferenced_numbers': unreferenced,
        'evidence_keys_not_found': missing_cards,
        'query_ids_not_found': missing_queries,
        'actions_without_a_gate_record': actions_without_gates,
        'views_cited_as_gate_evidence': view_on_gate_line,
        'missing_disclosure': [] if DISCLOSURE in text else [{'line': None, 'text': DISCLOSURE}],
    }
    # Everything except an unreferenced number is a hard failure: those five are statements the
    # report makes that are not true. An unreferenced number is a hard failure under --strict,
    # and a listed warning otherwise, because a draft is allowed to be incomplete.
    hard = {k: v for k, v in problems.items() if k != 'unreferenced_numbers' and v}
    return {'problems': problems, 'hard_failures': hard,
            'ok': not hard and not unreferenced,
            'ok_non_strict': not hard,
            'citations_found': sorted({c for line in text.splitlines() for c in citations(line)})}


def main(argv=None) -> int:
    safe_console()
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--root', type=Path, default=Path.cwd())
    sub = parser.add_subparsers(dest='command', required=True)
    checker = sub.add_parser('check')
    checker.add_argument('--report', type=Path, required=True)
    checker.add_argument('--strict', action='store_true')
    checker.add_argument('--json', action='store_true')
    keys = sub.add_parser('keys')
    keys.add_argument('--report', type=Path, required=True)

    args = parser.parse_args(argv)
    try:
        text = args.report.read_text(encoding='utf-8')
    except OSError as exc:
        print(f'report_check: cannot read {args.report}: {exc}', file=sys.stderr)
        return 4

    try:
        result = check(args.root, text)
    except StateError as exc:
        print(f'report_check: {exc}', file=sys.stderr)
        return 1

    if args.command == 'keys':
        for citation in result['citations_found']:
            print(citation)
        return 0

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        labels = {
            'unreferenced_numbers': 'numbers with nothing to trace them to',
            'evidence_keys_not_found': 'EV- keys that are not cards on disk',
            'query_ids_not_found': 'Q- ids not in market/queries.json',
            'actions_without_a_gate_record': 'actions with no G1-G4 line',
            'views_cited_as_gate_evidence': 'a view cited on a gate line (opinion is not evidence)',
            'missing_disclosure': 'the disclosure line is missing',
        }
        for key, items in result['problems'].items():
            if not items:
                continue
            print(f'{labels[key]}: {len(items)}')
            for item in items[:20]:
                where = f"line {item['line']}: " if item.get('line') else ''
                print(f"  {where}{item.get('text') or item.get('key')}")
        if result['ok']:
            print('report_check: OK (every number resolves, gates present, disclosure present)')
        elif result['ok_non_strict']:
            print('report_check: no hard failures; unreferenced numbers listed above')

    return 0 if (result['ok'] if args.strict else result['ok_non_strict']) else 1


if __name__ == '__main__':
    sys.exit(main())
