#!/usr/bin/env python3
"""Render a short financial check-in from decision findings, with computed checks beneath it.

python tools/plain_report.py --input <local bundle.json>
Prints Markdown only; the owning workflow saves it under advice/ or decisions/.
"""
import argparse
import html
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from decision_score import GATES, score
from fa_state import StateError, read_json, safe_console
import cash_plan

LABELS = {'do_now': 'Reasonable to consider', 'do_scoped': 'Consider only a smaller or staged step',
          'park': 'Wait; do not act yet', 'reject': 'Do not proceed',
          'gated': 'Do not proceed; a required check failed'}
CHECKS = ('Whether you can afford it', 'Whether it fits your goals and risk limits',
          'Legal and tax requirements', 'Whether the evidence is sufficient')


def clean(value):
    # User text is content, never Markdown/HTML structure supplied by the caller.
    text = html.escape(' '.join(str(value).split()), quote=False)
    return re.sub(r'([\\`*\[\]#])', r'\\\1', text)


def cite(line, key):
    return f'{line} ({key})' if re.search(r'\d', line) else line


def cash_answer(cash):
    """The direct cash answer, recomputed from the saved cash_plan.py output, never narrated."""
    if not cash:
        return []
    plan, checked = cash.get('plan'), cash.get('check')
    if not isinstance(plan, dict) or plan.get('kind') != 'cash_plan':
        raise StateError('cash.plan must be a saved cash_plan.py plan result')
    if abs(plan['spoken_for_eur'] + plan['free_eur'] - plan['usable_cash_eur']) > 0.01:
        raise StateError('cash plan does not reconcile to the usable cash')
    words = cash_plan.plain(plan)
    opening = words['answer']
    if checked:
        if checked.get('kind') != 'cash_plan_check' or checked['counted_eur'] > plan['free_eur'] + 0.005:
            raise StateError('cash.check must be a cash_plan.py check within the free amount')
        for part in checked['components']:
            if part['counted_eur'] and part['band'] in cash_plan.BLOCKED:
                raise StateError('a blocked component cannot be counted in the answer')
        opening = checked['opening']
    tag = lambda text: f'{clean(text)} (cash_plan.py)' if re.search(r'\d', text) else clean(text)
    out = ['## Your answer', '', '**' + tag(opening) + '**', '']
    out += ['Why: ' + tag(line) for line in words['why']]
    out += ['', 'Next step: ' + tag(words['next_step']),
            'What would change this: ' + clean('; '.join(words['would_change'])) + '.']
    waiting = (checked or {}).get('waiting', []) + words['unknowns']
    if waiting:
        out += ['What I could not check or is not ready: ' + tag('; '.join(waiting))]
    out += ['If you change nothing: ' + clean(words['do_nothing']), '']
    return out


def render(bundle):
    decisions = bundle.get('decisions')
    if not isinstance(decisions, list) or not decisions:
        raise StateError('decisions must include vetted findings and the do-nothing alternative')
    if not any(d.get('is_do_nothing') is True for d in decisions):
        raise StateError('the do-nothing alternative must be present')
    computed, seen = [], set()
    for item in decisions:
        result = score(item.get('findings'))
        key = result['decision_id']
        if not re.fullmatch(r'D-\d{3,}', key) or key in seen:
            raise StateError('unique D- decision ids are required')
        seen.add(key)
        for field in ('title', 'why', 'next_check'):
            if not item.get(field):
                raise StateError(f'{key}: {field} is required')
        computed.append((item, result))
    lines = ['# Your financial check-in', '']
    lines += cash_answer(bundle.get('cash'))
    lines += ['**Your situation:** ' + clean(bundle.get('situation', 'See the checks below.')), '',
              '**What changed:** ' + clean(bundle.get('changed', 'No comparison with a previous review was supplied.')), '',
              '**Next review:** ' + clean(bundle.get('review_when', 'When the missing information is available.')), '',
              '## What needs attention', '']
    # Input order is the workflow's existing priority order, never a new forecast ranking.
    # Decision ids stay in the supporting checks; a lead line carries one only when it quotes a
    # number, so report_check.py can still trace that number.
    for item, result in computed[:3]:
        key = result['decision_id']
        lines += [cite(f'- **{clean(item["title"])}**: {LABELS[result["band"]]}.', key),
                  cite(f'  Why: {clean(item["why"])}', key),
                  cite(f'  First check: {clean(item["next_check"])}', key)]
    lines += ['', '## Restrictions and missing information', '']
    grouped = {}
    for item, result in computed:
        issues = []
        if result['band'] in ('gated', 'reject', 'park', 'do_scoped'):
            issues.append(LABELS[result['band']])
        for name, label in zip(GATES, CHECKS):
            verdict = result['gates'][name]
            if verdict in ('FAIL', 'FLAG', 'UNDETERMINED'):
                issues.append(label + (': failed' if verdict == 'FAIL' else ': unresolved'))
        if any('ips=missing' in cap for cap in result['caps']):
            issues.append('Your written investment policy is not confirmed')
        # Never hide a limit simply because a card falls outside the top three.
        for token in result['caps']:
            if 'concentration=FLAG' in token:
                issues.append('This would put more money into your already largest investment exposure')
            if 'behaviour=' in token:
                issues.append('A recorded pattern of reacting to market moves limits this proposal')
        issues += [str(b.get('note') or b.get('claim') or b.get('category'))
                   for b in result.get('unapplied_blockers', [])]
        if item.get('uncertainty'):
            issues.append(item['uncertainty'])
        if not issues:
            issues = ['No unresolved check recorded']
        # The same issue on several options is stated once, naming every option it affects.
        for issue in issues:
            grouped.setdefault(issue, []).append((item['title'], result['decision_id']))
    for issue, where in grouped.items():
        names = ', '.join(clean(title) for title, _ in where)
        keys = ' '.join(key for _, key in where)
        lines.append(cite(f'- {clean(issue)} (affects: {names}).', keys))
    lines += ['', '## If you change nothing', '']
    for item, result in computed:
        if item.get('is_do_nothing') is True:
            lines += [cite(f'{clean(item["title"])}: {clean(item["why"])} '
                           f'**{LABELS[result["band"]]}**.', result['decision_id']), '']
    lines += ['## Supporting checks', '',
              'Scores below are internal decision ratings, never probabilities of success.', '']
    for item, result in computed:
        key = result['decision_id']
        lines += [f'### {key}', '', f'**{clean(item["title"])}: {LABELS[result["band"]]}**', '',
                  '<details>', '<summary>Show the checks and sources</summary>', '',
                  ' | '.join(f'G{i + 1} {result["gates"][name]}' for i, name in enumerate(GATES))]
        if result['score'] is not None:
            lines.append(f'{key}: score {result["raw_score"]}, capped to {result["score"]} (decision_score.py).')
        for name, label in zip(GATES, CHECKS):
            evidence = '; '.join(result['gate_evidence'][name]) or 'No evidence recorded'
            note = (item['findings']['gates'][name].get('note') or '')
            lines.append(f'- {key} {label}: {clean(evidence)}. {clean(note)}')
        for cap in result['caps']:
            lines.append(f'- {key}: {clean(cap)}')
        lines += ['', '</details>', '']
    lines += ['This is not licensed financial advice. Nothing here has been executed; you decide and you act.', '']
    return '\n'.join(lines)


def main(argv=None):
    safe_console()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--input', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        print(render(read_json(args.input, 'report bundle')))
        return 0
    except (StateError, TypeError, KeyError) as exc:
        print(f'plain_report: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
