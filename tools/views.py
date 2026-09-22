#!/usr/bin/env python3
"""Scenario views: validate them, record them immutably, and score them against what happened.

Usage (from the repository root):
    python tools/views.py validate --view <file>
    python tools/views.py record   --view <file>
    python tools/views.py list     [--expired] [--as-of YYYY-MM-DD]
    python tools/views.py score    --view VW-20260920-01 [--as-of YYYY-MM-DD]
    python tools/views.py calibration

A view is the one opinion this system is allowed to hold. Everything here exists to keep that
opinion out of the machinery that decides things, and to make it cost something later.

What this tool enforces that prose cannot:

- **Probabilities sum to exactly 100**, as integers. A "forecast" whose probabilities do not add up
  has not been thought through, and averaging it later would be arithmetic on nonsense.
- **The disclosure line is mandatory.** A view without it cannot be recorded, so it cannot reach a
  report without saying what it is.
- **Views are immutable.** Recording over an existing `view_id` is refused; a changed mind is a new
  view with `supersedes`. A view that can be edited after the fact cannot be scored honestly, which
  is the only reason to keep one.
- **Realised returns come from recorded observations only** (exit 8 if a price is missing). A
  scorecard the system filled in from memory would flatter it precisely where it matters.
- **Scoring is arithmetic, not narrative.** The Brier score is computed; there is no field in which
  a view can explain why its miss was really a hit.

Stdlib only. Exit 0 ok; 1 state or schema error; 4 bad arguments; 8 a required recorded price is
missing.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fa_state import StateError, exclusive_lock, read_json, safe_console, today, write_json  # noqa: E402
from track_actions import MissingPrice, recorded_price  # noqa: E402

VIEWS = Path('views')
SCORES = VIEWS / 'scores'
SCORES_LOG = VIEWS / 'scores.jsonl'
MISSING_PRICE = 8

VIEW_ID = re.compile(r'VW-\d{8}-\d{2}')
SCENARIOS = ('bear', 'base', 'bull')
CONFIDENCES = ('low', 'medium', 'high')
EVIDENCE_KEY = re.compile(r'EV-[0-9a-f]{8,16}')
DISCLOSURE = ('This is a model opinion, not evidence and not a forecast you should act on; '
              'see the decision verdict for what to do.')
# Anything past this horizon, and anything in these asset classes, must be low confidence (12).
LONG_HORIZON_DAYS = 186
ALWAYS_LOW_CONFIDENCE = ('crypto',)
# How stale a price may be and still count as "the price on that date" when scoring. Without this
# bound, a months-old observation would stand in for the end price and a view that missed entirely
# would score as a perfect call.
SCORING_PRICE_TOLERANCE_DAYS = 7


def validate(view: dict) -> dict:
    """Return the view unchanged, or raise listing every problem found."""
    problems: list[str] = []
    if not isinstance(view, dict):
        raise StateError('a view must be a JSON object')

    view_id = view.get('view_id')
    if not view_id or not VIEW_ID.fullmatch(str(view_id)):
        problems.append('view_id must look like VW-YYYYMMDD-NN')
    if not view.get('subject'):
        problems.append('subject is required')
    for field in ('created_on', 'horizon_end'):
        try:
            dt.date.fromisoformat(str(view.get(field)))
        except (TypeError, ValueError):
            problems.append(f'{field} must be an ISO date')

    scenarios = view.get('scenarios')
    if not isinstance(scenarios, list) or len(scenarios) != 3:
        problems.append('exactly three scenarios are required: bear, base, bull')
        scenarios = []
    names = [s.get('name') for s in scenarios if isinstance(s, dict)]
    if scenarios and sorted(names) != sorted(SCENARIOS):
        problems.append(f'scenario names must be {", ".join(SCENARIOS)}, got {names}')

    total = 0
    for scenario in scenarios:
        if not isinstance(scenario, dict):
            problems.append('each scenario must be an object')
            continue
        name = scenario.get('name')
        probability = scenario.get('probability')
        if not isinstance(probability, int) or isinstance(probability, bool) or not 0 <= probability <= 100:
            problems.append(f'{name}: probability must be an integer 0-100, got {probability!r}')
        else:
            total += probability
        span = scenario.get('range')
        if (not isinstance(span, list) or len(span) != 2
                or not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                           and math.isfinite(v) for v in span) or span[0] >= span[1]):
            problems.append(f'{name}: range must be [low, high] as numbers')
        assumptions = scenario.get('assumptions') or []
        if not isinstance(assumptions, list) or not 2 <= len(assumptions) <= 4:
            problems.append(f'{name}: 2-4 assumptions are required, each an observable claim')
        keys = scenario.get('evidence_keys') or []
        unsupported = scenario.get('unsupported_assumptions') or []
        if assumptions and not keys and not unsupported:
            problems.append(
                f'{name}: assumptions rest on no evidence key. Cite EV- keys, or list the '
                'assumptions in "unsupported_assumptions" so the report can say they are guesses.')
        for key in keys:
            if not EVIDENCE_KEY.fullmatch(str(key)):
                problems.append(f'{name}: {key!r} is not an EV- key')
        if not scenario.get('signposts'):
            problems.append(f'{name}: signposts are required - a scenario with nothing observable '
                            'to watch for is a story, not a scenario')

    if scenarios and total != 100:
        problems.append(f'probabilities sum to {total}, not 100. A forecast whose probabilities do '
                        'not add up has not been thought through.')

    if not problems and scenarios:
        ordered = sorted(scenarios, key=lambda s: s['range'][0])
        if [s['name'] for s in ordered] != list(SCENARIOS):
            problems.append('scenario ranges must be ordered bear, base, bull')
        if any(left['range'][1] > right['range'][0] for left, right in zip(ordered, ordered[1:])):
            problems.append('scenario ranges must not overlap except at a shared boundary')
    try:
        if dt.date.fromisoformat(str(view['horizon_end'])) <= dt.date.fromisoformat(str(view['created_on'])):
            problems.append('horizon_end must be after created_on')
    except (KeyError, ValueError):
        pass

    if not view.get('what_would_change_this'):
        problems.append('what_would_change_this is required, and must be observable')
    confidence = view.get('confidence')
    if confidence not in CONFIDENCES:
        problems.append(f'confidence must be one of {", ".join(CONFIDENCES)}')
    else:
        problems += _confidence_problems(view, confidence)

    if not view.get('base_rate'):
        problems.append('base_rate is required: a view starts from the historical distribution, '
                        'and a departure from it has to be argued')
    if view.get('disclosure') != DISCLOSURE:
        problems.append('the disclosure line must be present verbatim: ' + DISCLOSURE)
    supersedes = view.get('supersedes')
    if supersedes and not VIEW_ID.fullmatch(str(supersedes)):
        problems.append(f'supersedes {supersedes!r} is not a view id')

    if problems:
        raise StateError('view rejected: ' + '; '.join(problems))
    return view


def _confidence_problems(view: dict, confidence: str) -> list[str]:
    """Long horizons and crypto are low confidence by rule (12), not by choice."""
    if confidence == 'low':
        return []
    problems = []
    try:
        horizon = (dt.date.fromisoformat(str(view['horizon_end']))
                   - dt.date.fromisoformat(str(view['created_on']))).days
    except (KeyError, ValueError):
        horizon = None
    if horizon is not None and horizon > LONG_HORIZON_DAYS:
        problems.append(f'confidence {confidence!r} over a {horizon}-day horizon: anything beyond '
                        'six months is low confidence by rule')
    if str(view.get('asset_class', '')).lower() in ALWAYS_LOW_CONFIDENCE:
        problems.append(f'confidence {confidence!r} on {view.get("asset_class")}: crypto views are '
                        'low confidence by rule')
    return problems


def view_path(root: Path, view_id: str) -> Path:
    return Path(root) / VIEWS / f'{view_id}.json'


def record(root: Path, view: dict) -> Path:
    validate(view)
    path = view_path(root, view['view_id'])
    if path.exists():
        raise StateError(
            f'{view["view_id"]} already exists. A view is immutable: record a new one with '
            '"supersedes" set, so the original can still be scored against what it actually said.')
    write_json(path, view)
    return path


def load_all(root: Path) -> list[dict]:
    return [read_json(path, str(path)) for path in sorted((Path(root) / VIEWS).glob('VW-*.json'))]


def expired(views: list[dict], as_of: str | None = None) -> list[dict]:
    limit = dt.date.fromisoformat(as_of or today())
    out = []
    for view in views:
        try:
            if dt.date.fromisoformat(str(view.get('horizon_end'))) <= limit:
                out.append(view)
        except (TypeError, ValueError):
            continue
    return out


def brier(scenarios: list[dict], materialised: str) -> float:
    """mean((p - o)^2) over the three scenarios, with p as a probability in [0, 1]."""
    total = 0.0
    for scenario in scenarios:
        probability = scenario['probability'] / 100
        outcome = 1.0 if scenario['name'] == materialised else 0.0
        total += (probability - outcome) ** 2
    return round(total / len(scenarios), 4)


def which_scenario(scenarios: list[dict], realised: float) -> str | None:
    ordered = sorted(scenarios, key=lambda scenario: scenario['range'][0])
    for index, scenario in enumerate(ordered):
        low, high = scenario['range']
        if low <= realised < high or (index == len(ordered) - 1 and realised == high):
            return scenario['name']
    return None


def score(root: Path, view_id: str, as_of: str | None = None) -> dict:
    view = read_json(view_path(root, view_id), f'view {view_id}')
    validate(view)
    as_of = as_of or today()
    if dt.date.fromisoformat(as_of) < dt.date.fromisoformat(str(view['horizon_end'])):
        raise StateError(f'{view_id} does not expire until {view["horizon_end"]}; scoring it early '
                         'would score a different question than the one it asked')

    symbol = view['subject']
    start = recorded_price(root, symbol, str(view['created_on']),
                           SCORING_PRICE_TOLERANCE_DAYS)
    end = recorded_price(root, symbol, str(view['horizon_end']), SCORING_PRICE_TOLERANCE_DAYS)
    if not all(isinstance(p['value'], (int, float)) and not isinstance(p['value'], bool)
               and math.isfinite(p['value']) and p['value'] > 0 for p in (start, end)):
        raise StateError('scoring requires finite positive prices')
    realised = (end['value'] - start['value']) / start['value']
    materialised = which_scenario(view['scenarios'], realised)
    result = {
        'view_id': view_id,
        'scored_on': as_of,
        'subject': symbol,
        'asset_class': view.get('asset_class'),
        'horizon_end': view['horizon_end'],
        'horizon_days': (dt.date.fromisoformat(view['horizon_end'])
                         - dt.date.fromisoformat(view['created_on'])).days,
        'realised_return': round(realised, 4),
        'realised_ev_keys': [start['ev_key'], end['ev_key']],
        'prices_used': [start, end],
        'scenario_materialised': materialised,
        'brier': brier(view['scenarios'], materialised) if materialised else None,
        'uniform_brier': brier([dict(s, probability=p) for s, p in
                                zip(sorted(view['scenarios'], key=lambda s: SCENARIOS.index(s['name'])),
                                    (33, 33, 34))], materialised) if materialised else None,
        'probabilities': {s['name']: s['probability'] for s in view['scenarios']},
        'confidence_stated': view.get('confidence'),
        'note': (None if materialised else
                 'the realised return fell outside every stated range - the view did not consider '
                 'what happened, which is a worse miss than a wrong probability and is recorded '
                 'as such'),
    }
    score_path = Path(root) / SCORES / f'VS-{view_id}.json'
    with exclusive_lock(Path(root) / VIEWS / '.score.lock'):
        if score_path.exists():
            existing = read_json(score_path, 'view score')
            if existing['realised_ev_keys'] != result['realised_ev_keys'] or existing['realised_return'] != result['realised_return']:
                raise StateError('recorded score differs; investigate revised prices rather than overwrite it')
            _append_score(root, existing)
            return existing
        write_json(score_path, result)
        _append_score(root, result)
    return result


def _append_score(root: Path, result: dict) -> None:
    path = Path(root) / SCORES_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line]
        if any(row.get('view_id') == result['view_id'] for row in rows):
            return
    with path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + '\n')


def calibration(root: Path) -> dict:
    """Mean Brier by asset class and horizon bucket, with sample sizes. Never a ranking input."""
    path = Path(root) / SCORES_LOG
    if not path.is_file():
        return {'scored_views': 0,
                'note': 'no view has been scored yet, so there is no evidence either way about '
                        'this system\'s forecasting ability'}
    rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line]
    # Legacy logs may contain repeated reviews. A repeated forecast is not a new trial.
    unique = {}
    for row in rows:
        unique.setdefault(row['view_id'], row)
    rows = list(unique.values())
    buckets: dict[str, list[float]] = {}
    horizon_buckets = {}
    missed = 0
    for row in rows:
        if row.get('brier') is None:
            missed += 1
            continue
        key = f"{row.get('asset_class') or 'unclassified'}"
        buckets.setdefault(key, []).append(row['brier'])
        days = row.get('horizon_days')
        horizon = 'legacy_unknown' if days is None else ('up_to_six_months' if days <= 186 else 'longer')
        horizon_buckets.setdefault(f'{key}/{horizon}', []).append(row)
    return {
        'scored_views': len(rows),
        'outside_every_range': missed,
        'range_coverage': round((len(rows) - missed) / len(rows), 4) if rows else None,
        'by_asset_and_horizon': {
            key: {'n': len(group),
                  'mean_brier': round(sum(r['brier'] for r in group) / len(group), 4),
                  'mean_uniform_brier': (round(sum(r['uniform_brier'] for r in group) / len(group), 4)
                                         if all(r.get('uniform_brier') is not None for r in group) else None)}
            for key, group in sorted(horizon_buckets.items())},
        'by_asset_class': {name: {'mean_brier': round(sum(values) / len(values), 4),
                                  'n': len(values)}
                           for name, values in sorted(buckets.items())},
        'reference': 'a uniform 33/33/34 guess scores about 0.22; lower is better',
        'note': 'shown to the user and given to the skeptic as data; never used to rank anything',
    }


def main(argv=None) -> int:
    safe_console()
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--root', type=Path, default=Path.cwd())
    parser.add_argument('--json', action='store_true')
    sub = parser.add_subparsers(dest='command', required=True)
    validator = sub.add_parser('validate')
    validator.add_argument('--view', type=Path, required=True)
    recorder = sub.add_parser('record')
    recorder.add_argument('--view', type=Path, required=True)
    lister = sub.add_parser('list')
    lister.add_argument('--expired', action='store_true')
    lister.add_argument('--as-of', dest='as_of')
    scorer = sub.add_parser('score')
    scorer.add_argument('--view', required=True)
    scorer.add_argument('--as-of', dest='as_of')
    sub.add_parser('calibration')

    args = parser.parse_args(argv)
    try:
        if args.command == 'validate':
            validate(read_json(args.view, 'view'))
            print('view: valid (three scenarios, probabilities sum to 100, disclosure present)')
            return 0
        if args.command == 'record':
            path = record(args.root, read_json(args.view, 'view'))
            print(f'recorded {path.name} (immutable; supersede it rather than editing it)')
            return 0
        if args.command == 'list':
            views = load_all(args.root)
            if args.expired:
                views = expired(views, args.as_of)
            if not views:
                print('no views' + (' past their horizon' if args.expired else ''))
                return 0
            for view in views:
                print(f"{view['view_id']}  {view['subject']:14} horizon {view['horizon_end']}  "
                      f"confidence {view.get('confidence')}"
                      + (f"  supersedes {view['supersedes']}" if view.get('supersedes') else ''))
            return 0
        if args.command == 'calibration':
            print(json.dumps(calibration(args.root), indent=2))
            return 0

        result = score(args.root, args.view, args.as_of)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"{result['view_id']}: realised {result['realised_return']:+.2%} -> "
                  f"{result['scenario_materialised'] or 'outside every range'}")
            print(f"Brier {result['brier']}" if result['brier'] is not None else result['note'])
        return 0
    except MissingPrice as exc:
        print(f'views: {exc}', file=sys.stderr)
        return MISSING_PRICE
    except StateError as exc:
        print(f'views: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
