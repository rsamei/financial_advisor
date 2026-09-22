#!/usr/bin/env python3
"""Compute a decision's verdict, score and band mechanically from a findings JSON.

Usage (from the repository root):
    python tools/decision_score.py score --findings <file> [--json] [--out gates.json]
    python tools/decision_score.py weights
    python tools/decision_score.py explain --band 63

The rubric is `.claude/skills/financial-advisor/04-decision-evaluation.md`. This tool is the only
thing that turns findings into a verdict, and `tests/test_decision_contract.py` asserts the two
still agree.

What this tool enforces that prose cannot:

- **Gates run before any score exists.** A FAIL on gates 1-3 produces `band: gated` and an EMPTY
  score - not a low one. A gated decision carrying a number would later be quoted as though it had
  survived, and the number is the thing people remember.
- **Gate 4 can never FAIL.** Absence of evidence is `UNDETERMINED`, a FLAG with `cap:55`. A gate
  that could fail on missing evidence would convert ignorance into a verdict.
- **Weights sum to 100**, asserted at import time, so a dimension cannot be added without the sum
  being corrected in the same change.
- **Caps apply after the sum and the lowest wins**, and the raw score is always reported beside the
  capped one. "84, capped to 55" keeps the information that the idea was good and the evidence was
  not.
- **Every FAIL must quote evidence.** A gate result with `verdict: FAIL` and no `evidence` is
  rejected: a gate that fails without citing something is an opinion wearing a gate's clothes.
- **A scenario view can never be gate evidence.** Any `VW-` key in a gate's evidence is refused
  outright. Views are model opinion, and the whole point of quarantining them is that they cannot
  leak into the machinery through a citation field.

Stdlib only. Exit 0 ok; 1 state or schema error; 4 bad arguments.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fa_state import StateError, read_json, safe_console, write_json  # noqa: E402

# Dimension -> weight (04). The assertion below is deliberate: an unbalanced rubric must not import.
WEIGHTS = {
    'goal_contribution': 25,
    'risk_adjusted_robustness': 25,
    'cost_and_tax_efficiency': 15,
    'evidence_strength': 15,
    'simplicity': 10,
    'reversibility': 10,
}
assert sum(WEIGHTS.values()) == 100, 'the weights in 04 must sum to 100'

GATES = ('gate1_affordability', 'gate2_suitability', 'gate3_legal_tax',
         'gate4_evidence_coverage')
GATE_ORDER = {name: position for position, name in enumerate(GATES)}
VERDICTS = ('PASS', 'FLAG', 'FAIL')
COVERAGE = ('SUPPORTED', 'UNDETERMINED')
# Gate 4 reports coverage; it never FAILs.
GATE4_VERDICTS = ('PASS', 'FLAG', *COVERAGE)

BANDS = (('do_now', 72), ('do_scoped', 58), ('park', 45), ('reject', 0))
GATED = 'gated'

# cap token -> ceiling (04). The lowest applicable cap wins.
CAPS = {
    'coverage=UNDETERMINED': 55,
    'liquidity=FLAG': 60,
    'concentration=FLAG': 65,
    'ips=missing': 65,
    'tax=FLAG': 70,
    'behaviour': 60,          # behaviour=<chasing|recency|fomo>, needs a card
}
BEHAVIOUR_TENDENCIES = ('chasing', 'recency', 'fomo')

# Skeptic BLOCKER category -> the gate it re-opens, or None for "cap only" (04).
BLOCKER_MAP = {
    'cannot_afford': 'gate1_affordability',
    'breaches_limit': 'gate2_suitability',
    'not_purchasable': 'gate3_legal_tax',
    'mnpi': 'gate3_legal_tax',
    'evidence_absent': 'gate4_evidence_coverage',
    'behavioural': None,
}

EVIDENCE_KEY = re.compile(r'(EV-[0-9a-f]{8,16}|Q-[A-Za-z0-9._-]+|ledger:[A-Za-z0-9_.\[\]*-]+)')
VIEW_KEY = re.compile(r'VW-\d{8}-\d{2}')


def _gate_result(findings: dict, name: str, problems: list[str]) -> dict:
    result = (findings.get('gates') or {}).get(name)
    if not isinstance(result, dict):
        problems.append(f'gates.{name}: missing - all four gates must be reported, in order')
        return {'verdict': None, 'evidence': []}
    verdict = result.get('verdict')
    allowed = GATE4_VERDICTS if name == 'gate4_evidence_coverage' else VERDICTS
    if verdict not in allowed:
        problems.append(f'gates.{name}.verdict: {verdict!r} is not one of {", ".join(allowed)}')
    if name == 'gate4_evidence_coverage' and verdict == 'FAIL':
        problems.append('gate4 never FAILs: absence of evidence is UNDETERMINED, a FLAG with '
                        'cap:55. A gate that fails on missing evidence turns ignorance into a '
                        'verdict.')
    evidence = result.get('evidence') or []
    if not isinstance(evidence, list) or not all(isinstance(item, str) for item in evidence):
        problems.append(f'gates.{name}.evidence: expected a list of citation strings')
        evidence = []
    if verdict == 'FAIL' and not evidence:
        problems.append(f'gates.{name}: a FAIL must quote its evidence - the balance-sheet row and '
                        'its as_of, the IPS limit, the 07 rule id, or the EV- key. A gate that '
                        'fails without citing something is an opinion.')
    for citation in evidence:
        if VIEW_KEY.search(citation):
            problems.append(
                f'gates.{name}.evidence: {citation!r} cites a scenario view. A view is model '
                'opinion: it never enters a gate record, never moves a score dimension and never '
                'originates an action. Cite the observation the view was built from instead.')
    return {'verdict': verdict, 'evidence': evidence, 'note': result.get('note')}


def _dimensions(findings: dict, problems: list[str]) -> dict:
    scores = findings.get('dimensions')
    if not isinstance(scores, dict):
        problems.append('dimensions: missing - survivors need all six scored 0-100')
        return {}
    unknown = set(scores) - set(WEIGHTS)
    if unknown:
        problems.append(f'dimensions: unknown dimension(s) {sorted(unknown)}')
    out = {}
    for name in WEIGHTS:
        value = scores.get(name)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value <= 100:
            problems.append(f'dimensions.{name}: expected a number 0-100, got {value!r}')
            continue
        out[name] = float(value)
    return out


def _caps(findings: dict, gates: dict, problems: list[str]) -> list[dict]:
    """Caps the findings declare, plus the ones the gate verdicts imply."""
    applied: list[dict] = []

    def add(token: str, ceiling: int, why: str) -> None:
        applied.append({'token': f'cap:{ceiling} {token}', 'ceiling': ceiling, 'reason': why})

    if gates['gate4_evidence_coverage']['verdict'] in ('FLAG', 'UNDETERMINED'):
        add('coverage=UNDETERMINED', CAPS['coverage=UNDETERMINED'],
            'gate 4 did not meet c1-c4')
    if gates['gate1_affordability']['verdict'] == 'FLAG':
        add('liquidity=FLAG', CAPS['liquidity=FLAG'], 'gate 1 flagged')
    if gates['gate3_legal_tax']['verdict'] == 'FLAG':
        add('tax=FLAG', CAPS['tax=FLAG'], 'gate 3 flagged')
    if findings.get('increases_largest_concentration'):
        add('concentration=FLAG', CAPS['concentration=FLAG'],
            'the action increases the largest concentration')
    if not findings.get('ips_version'):
        add('ips=missing', CAPS['ips=missing'], 'no frozen IPS exists')

    for flag in findings.get('behavioural_flags') or []:
        if not isinstance(flag, dict):
            problems.append('behavioural_flags: each flag must be an object')
            continue
        tendency = flag.get('tendency')
        if tendency not in BEHAVIOUR_TENDENCIES:
            problems.append(f'behavioural_flags: {tendency!r} is not one of '
                            f'{", ".join(BEHAVIOUR_TENDENCIES)}')
            continue
        if not flag.get('evidence_key'):
            # 04: the skeptic may set this cap "only with a recorded card as evidence".
            problems.append(
                f'behavioural_flags.{tendency}: needs a recorded evidence card. A behavioural cap '
                'on a hunch about the user is the model overriding their judgement with its own.')
            continue
        add(f'behaviour={tendency}', CAPS['behaviour'], f'skeptic flag backed by '
            f'{flag["evidence_key"]}')
    return applied


def _blockers(findings: dict, problems: list[str]) -> list[dict]:
    """Map skeptic BLOCKERs to gates. Only a carded BLOCKER in the table re-opens a gate."""
    out = []
    for objection in findings.get('skeptic_objections') or []:
        if not isinstance(objection, dict):
            problems.append('skeptic_objections: each objection must be an object')
            continue
        if objection.get('severity') != 'BLOCKER':
            continue
        category = objection.get('category')
        carded = bool(objection.get('evidence_key'))
        gate = BLOCKER_MAP.get(category)
        out.append({
            'category': category, 'gate': gate, 'carded': carded,
            'applies': bool(gate) and carded,
            'note': ('re-opens ' + gate if gate and carded else
                     'shown to the user only: ' + ('no gate maps to this category'
                                                   if not gate else
                                                   'backed by reasoning, not a recorded card')),
        })
    return out


def score(findings: dict) -> dict:
    """Return the full verdict record. Raises StateError listing every problem it found."""
    problems: list[str] = []
    if not isinstance(findings, dict):
        raise StateError('findings must be a JSON object')
    if not findings.get('decision_id'):
        problems.append('decision_id: required')

    gates = {name: _gate_result(findings, name, problems) for name in GATES}
    blockers = _blockers(findings, problems)
    failed = [name for name in GATES if gates[name]['verdict'] == 'FAIL']
    unapplied_blockers = [b for b in blockers if not b['applies']]

    result = {
        'decision_id': findings.get('decision_id'),
        'gates': {name: gates[name]['verdict'] for name in GATES},
        'gate_evidence': {name: gates[name]['evidence'] for name in GATES},
        'gate_order': list(GATES),
        'blockers': blockers,
        'failed_gates': failed,
    }

    if failed:
        # Gate-before-score: there is no score to compute, and none is invented.
        caps = _caps(findings, gates, problems)
        if problems:
            raise StateError('; '.join(problems))
        result.update({'band': GATED, 'score': None, 'raw_score': None,
                       'caps': [cap['token'] for cap in caps],
                       'why': [f'{name} FAILED: ' + '; '.join(gates[name]['evidence'])
                               for name in failed],
                       'unapplied_blockers': unapplied_blockers})
        return result

    dimensions = _dimensions(findings, problems)
    caps = _caps(findings, gates, problems)
    if problems:
        raise StateError('; '.join(problems))

    raw = sum(dimensions[name] * WEIGHTS[name] for name in WEIGHTS) / 100
    raw_score = int(round(raw))
    ceiling = min([cap['ceiling'] for cap in caps], default=100)
    final = min(raw_score, ceiling)
    band = next(name for name, floor in BANDS if final >= floor)

    result.update({
        'dimensions': dimensions,
        'weights': dict(WEIGHTS),
        'raw_score': raw_score,
        'caps': [cap['token'] for cap in caps],
        'cap_reasons': caps,
        'ceiling': ceiling,
        'score': final,
        'band': band,
        'capped': final < raw_score,
        'why': ([f'{raw_score}, capped to {final} (' + ', '.join(c['token'] for c in caps) + ')']
                if final < raw_score else [f'score {final}']),
        'unapplied_blockers': unapplied_blockers,
        'requires_watch_trigger': band == 'park',
        'staging_required': band == 'do_scoped',
    })
    return result


def band_for(value: int) -> str:
    return next(name for name, floor in BANDS if value >= floor)


def main(argv=None) -> int:
    safe_console()
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--root', type=Path, default=Path.cwd())
    sub = parser.add_subparsers(dest='command', required=True)
    scorer = sub.add_parser('score')
    scorer.add_argument('--findings', type=Path, required=True)
    scorer.add_argument('--out', type=Path, default=None)
    scorer.add_argument('--json', action='store_true')
    sub.add_parser('weights')
    explain = sub.add_parser('explain')
    explain.add_argument('--band', type=int, required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == 'weights':
            for name, weight in WEIGHTS.items():
                print(f'{name:28} {weight:>3}')
            print(f'{"total":28} {sum(WEIGHTS.values()):>3}')
            return 0
        if args.command == 'explain':
            if not 0 <= args.band <= 100:
                print('decision_score: --band must be 0-100', file=sys.stderr)
                return 4
            print(f'{args.band} -> {band_for(args.band)}')
            return 0

        findings = read_json(args.findings, 'findings')
        result = score(findings)
        if args.out:
            write_json(args.out, result)
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            line = ' | '.join(f'G{position + 1} {result["gates"][name]}'
                              for position, name in enumerate(GATES))
            print(line)
            if result['band'] == GATED:
                print('band: gated (no score - a failed gate does not get a number)')
                for reason in result['why']:
                    print(f'  - {reason}')
            else:
                print(f"score: {result['raw_score']}"
                      + (f", capped to {result['score']}" if result['capped'] else '')
                      + f" | band: {result['band']}")
                for token in result['caps']:
                    print(f'  {token}')
                if result['requires_watch_trigger']:
                    print('  park: a watch trigger must be written, or this is just forgetting')
                if result['staging_required']:
                    print('  do_scoped: halve the amount or stage it over at least 3 tranches')
            for blocker in result['unapplied_blockers']:
                print(f"  skeptic BLOCKER not applied ({blocker['category']}): {blocker['note']}")
        return 0
    except StateError as exc:
        print(f'decision_score: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
