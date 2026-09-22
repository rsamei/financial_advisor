#!/usr/bin/env python3
"""Screen a claim for material non-public information, and log every refusal permanently.

Usage (from the repository root):
    python tools/compliance_guard.py screen --text "..." [--source user] [--decision D-001]
    python tools/compliance_guard.py screen --file <path> [--json]
    python tools/compliance_guard.py log [--limit 20]
    python tools/compliance_guard.py verify

The patterns come from `08-behavioural-guardrails.md`, and `tests/test_compliance_guard.py`
asserts the two lists match.

What this tool enforces that prose cannot:

- **A refusal is recorded before anything else reads the claim.** `/decide` screens first, so a
  suspect tip never reaches a gate, a score, an agent prompt or a report. Screening afterwards
  would mean the reasoning already happened.
- **The log is hash-chained and `compliance/` is never a reset scope.** A refusal record that can
  be quietly emptied is not a refusal record.
- **Public filings are not MNPI, and saying so is half this tool's job.** Form 4, 13F,
  congressional disclosures, short interest, press releases and published statistics are public
  registers. A guard that refuses ordinary research is a guard the user learns to route around,
  and then it protects nothing. Every pattern below is therefore matched per sentence, and a
  sentence that names a public source is cleared even when it contains a trigger word.
- **There is no override.** No flag, no "hypothetically", no confirmation prompt. Acting on MNPI
  is a criminal matter under the EU Market Abuse Regulation and US securities law, and the person
  carrying that risk is the user.

This tool is a screen, not legal advice, and it is deliberately blunt: it will sometimes refuse
something innocent. The report says what was refused and why, so the user can rephrase a claim
that genuinely rests on public information.

Stdlib only. Exit 0 clear; 1 state error; 4 bad arguments; 9 MNPI suspected (a refusal).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fa_state import (StateError, append_chain, read_chain, safe_console)  # noqa: E402

REFUSALS = Path('compliance') / 'refusals.jsonl'
MNPI_REFUSAL = 9

# category -> (pattern, what it looks like). Every one of these describes information the public
# does not have. Ordering is irrelevant; all are applied.
MNPI_PATTERNS = {
    'insider_tip': [
        re.compile(r'\b(someone|somebody|a (?:guy|friend|contact|source)|my (?:friend|cousin|'
                   r'brother|sister|colleague|contact))\b[^.]{0,60}\b(at|inside|who works? (?:at|for))\b',
                   re.I),
        re.compile(r'\b(?:told|tipped|whispered|let slip|mentioned)\s+me\b[^.]{0,40}'
                   r'\b(?:before|ahead of|prior to)\b', re.I),
        re.compile(r'\b(?:my|a)\s+(?:client|customer|patient|student)\b[^.]{0,40}'
                   r'\b(?:told|mentioned|said)\b', re.I),
        re.compile(r'\bworks? (?:at|for|in)\b[^.]{0,40}\b(?:told|said|mentioned|confirmed)\b', re.I),
    ],
    'unpublished': [
        re.compile(r'\bnot (?:yet )?public(?:ly (?:known|available|announced))?\b', re.I),
        re.compile(r'\bnot been (?:announced|released|published|filed)\b', re.I),
        re.compile(r"\b(?:they|he|she|it) (?:haven't|hasn't|have not|has not) "
                   r"(?:announced|released|published|filed|disclosed)\b", re.I),
        re.compile(r'\b(?:confidential|non-public|nonpublic|internal only|embargoed)\b', re.I),
        re.compile(r'\bunder (?:embargo|NDA|a non-disclosure)\b', re.I),
    ],
    'pre_announcement': [
        re.compile(r'\b(?:before|ahead of|in advance of|prior to) the (?:announcement|earnings|'
                   r'release|filing|merger|acquisition|approval|results)\b', re.I),
        re.compile(r'\bwhen (?:it|this|the news) (?:becomes|goes|is made) public\b', re.I),
        re.compile(r'\b(?:upcoming|unannounced|undisclosed) (?:merger|acquisition|deal|earnings|'
                   r'approval|contract|layoffs?)\b', re.I),
    ],
    'private_document': [
        re.compile(r'\b(?:internal|leaked|draft) (?:memo|document|report|deck|figures?|numbers?|'
                   r'results?|forecast)\b', re.I),
        re.compile(r'\bI (?:saw|have|got|received) (?:the|their|an internal)\b[^.]{0,40}'
                   r'\b(?:before|early|first)\b', re.I),
    ],
}

# A sentence that names a public source is doing research, not trading on a tip. These clear a
# sentence even when a trigger word appears in it - which is the point: "the Form 4 shows three
# insiders bought before the announcement" is a description of a public filing.
PUBLIC_MARKERS = [
    re.compile(r'\b(?:form\s?4|13[FDG](?:-[A-Z]+)?|8-K|10-[KQ]|S-1|DEF\s?14A)\b', re.I),
    re.compile(r'\b(?:SEC|EDGAR|FINRA|CONSOB|ESMA|Borsa Italiana|Companies House)\b'),
    re.compile(r'\b(?:press release|public filing|published|disclosure|disclosed publicly|'
               r'prospectus|KID|annual report|quarterly report|official statistic)\b', re.I),
    re.compile(r'\b(?:congressional|senate|house) (?:disclosure|filing|trade report)\b', re.I),
    re.compile(r'\b(?:STOCK Act|periodic transaction report|PTR)\b', re.I),
    re.compile(r'\b(?:central bank|ECB|Federal Reserve|Bank of England) (?:statement|minutes|'
               r'speech|communication)\b', re.I),
]

VERDICTS = ('clear', 'mnpi_suspected')
SENTENCE = re.compile(r'[^.!?\n]+[.!?\n]?')


def sentences(text: str) -> list[str]:
    return [s.strip() for s in SENTENCE.findall(text or '') if s.strip()]


def screen(text: str) -> dict:
    """Classify a claim. Returns the verdict, the matches, and which sentences were cleared."""
    hits: list[dict] = []
    cleared: list[dict] = []
    for sentence in sentences(text):
        public = [marker.pattern for marker in PUBLIC_MARKERS if marker.search(sentence)]
        for category, patterns in MNPI_PATTERNS.items():
            for pattern in patterns:
                match = pattern.search(sentence)
                if not match:
                    continue
                entry = {'category': category, 'matched': match.group(0)[:120],
                         'sentence': sentence[:200]}
                if public:
                    # The trigger sits inside a description of a public filing.
                    cleared.append(entry | {'public_markers': public[:2]})
                else:
                    hits.append(entry)
    verdict = 'mnpi_suspected' if hits else 'clear'
    return {
        'verdict': verdict,
        'hits': hits,
        'cleared_by_public_source': cleared,
        'categories': sorted({hit['category'] for hit in hits}),
        'advice': (
            'This claim cannot be used. If it actually rests on something published - a filing, a '
            'press release, an official statistic - cite that source and screen it again.'
            if hits else 'No MNPI pattern matched. This is a screen, not legal advice.'),
    }


def log_refusal(root: Path, result: dict, *, text: str, source: str,
                decision_id: str | None) -> dict:
    """Append to the hash-chained refusal log. The text is stored so a review can see what was refused.

    `compliance/` is ignored by git and is never a reset scope, so this file is the one place in
    the repository that only ever grows.
    """
    path = Path(root) / REFUSALS
    events = read_chain(path, 'compliance/refusals.jsonl')
    return append_chain(path, events, 'refusal', {
        'verdict': result['verdict'],
        'categories': result['categories'],
        'hits': result['hits'],
        'source': source,
        'decision_id': decision_id,
        'text': text,
    })


def main(argv=None) -> int:
    safe_console()
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--root', type=Path, default=Path.cwd())
    sub = parser.add_subparsers(dest='command', required=True)
    screener = sub.add_parser('screen')
    screener.add_argument('--text')
    screener.add_argument('--file', type=Path)
    screener.add_argument('--source', default='user',
                          help='where the claim came from: user, document, provider')
    screener.add_argument('--decision')
    screener.add_argument('--json', action='store_true')
    screener.add_argument('--no-log', action='store_true',
                          help='screen without logging; for tests and dry runs only')
    logger = sub.add_parser('log')
    logger.add_argument('--limit', type=int, default=20)
    sub.add_parser('verify')

    args = parser.parse_args(argv)
    try:
        if args.command == 'log':
            events = read_chain(Path(args.root) / REFUSALS, 'compliance/refusals.jsonl')
            if not events:
                print('no refusals recorded')
                return 0
            for event in events[-args.limit:]:
                payload = event['payload']
                print(f"{event['recorded_at']}  {payload['verdict']}  "
                      f"{','.join(payload['categories']) or '-'}  "
                      f"{(payload.get('decision_id') or '-')}")
                print(f"    {payload['text'][:120]}")
            return 0

        if args.command == 'verify':
            events = read_chain(Path(args.root) / REFUSALS, 'compliance/refusals.jsonl')
            print(f'compliance_guard: chain intact, {len(events)} refusal(s) recorded')
            return 0

        if bool(args.text) == bool(args.file):
            print('compliance_guard: pass exactly one of --text or --file', file=sys.stderr)
            return 4
        text = args.text if args.text else args.file.read_text(encoding='utf-8', errors='replace')
        result = screen(text)

        if result['verdict'] == 'mnpi_suspected' and not args.no_log:
            log_refusal(args.root, result, text=text, source=args.source,
                        decision_id=args.decision)

        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(f"verdict: {result['verdict']}")
            for hit in result['hits']:
                print(f"  {hit['category']}: {hit['matched']!r}")
            for entry in result['cleared_by_public_source']:
                print(f"  cleared ({entry['category']} inside a public-source sentence): "
                      f"{entry['matched']!r}")
            print(result['advice'])
            if result['verdict'] == 'mnpi_suspected' and not args.no_log:
                print('logged to compliance/refusals.jsonl (append-only, never reset)')
        return MNPI_REFUSAL if result['verdict'] == 'mnpi_suspected' else 0
    except StateError as exc:
        print(f'compliance_guard: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
