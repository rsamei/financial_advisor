#!/usr/bin/env python3
"""Preview and apply a selective, reversible reset of local financial state.

Usage (from the repository root):
    python tools/reset_repo.py preview --scope market [--scope decisions ...]
    python tools/reset_repo.py apply   --scope market [...] --token <token from preview>

`/reset` (.claude/commands/reset.md) drives this tool. Nothing is deleted:

- `preview` lists every file the scopes would move, with sizes, and prints a token derived from that
  exact list (paths, sizes and modification times).
- `apply` recomputes the list and refuses unless the token matches, so anything that changed after
  the preview - a new file, an edit - requires a new preview and a new approval.
- Files are moved into `.reset-trash/<UTC timestamp>/` with their relative paths preserved, plus a
  manifest. Restoring is moving them back. Emptying the trash is the user's own action.

Scopes:
  profile    profile/* (PROFILE.md, profile.json, balance_sheet.json, risk_profile.json)
  market     observations, evidence cards, snapshots, briefs; market/queries.json is reset to {}
  decisions  decision_tracker.csv, decisions/* (briefs and the concept archive)
  advice     advice/AR-*/
  ips        ips/* - moved, never deleted; a frozen policy is a record of the user's own approval
  track      track/* (actions ledger, snapshots, outcome reviews)
  watch      watch/* (watchlist and alerts)
  views      views/* (scenario views and their scores)
  state      .state/workflows/*, .cache/market/*

compliance/ is NEVER in scope: a refusal log that can be reset is not a refusal log.
Tracked files are never moved.

Stdlib only. Exit 0 on success; 1 with a reason on stderr otherwise.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fa_state import StateError, atomic_write_text, safe_console, write_json  # noqa: E402

SCOPES = ('profile', 'market', 'decisions', 'advice', 'ips', 'track', 'watch', 'views', 'state')
KEEP = {'.gitkeep'}

# Scope -> paths moved. A path may be a file or a directory.
SCOPE_MOVES: dict[str, tuple[str, ...]] = {
    'profile': ('profile',),
    'market': ('market/observations.json', 'market/evidence', 'market/snapshots', 'market/exports'),
    'decisions': ('decision_tracker.csv', 'decisions'),
    'advice': ('advice',),
    'ips': ('ips',),
    'track': ('track',),
    'watch': ('watch',),
    'views': ('views',),
    'state': ('.state/workflows', '.cache/market'),
}
# Scope -> tracked files emptied rather than moved, so provenance keeps its shape.
SCOPE_REWRITES: dict[str, tuple[str, ...]] = {
    'market': ('market/queries.json',),
}


def files_under(root: Path, relative: str) -> list[Path]:
    base = root / relative
    if base.is_file():
        return [base]
    if not base.is_dir():
        return []
    return sorted(p for p in base.rglob('*') if p.is_file() and p.name not in KEEP)


def tracked(root: Path, paths: list[Path]) -> set[str]:
    if not paths or not (root / '.git').exists():
        return set()
    rel = [p.relative_to(root).as_posix() for p in paths]
    result = subprocess.run(['git', 'ls-files', '-z', '--', *rel], cwd=root, capture_output=True)
    if result.returncode != 0:
        raise StateError('could not ask git which files are tracked; refusing to move anything')
    return {name for name in result.stdout.decode('utf-8').split('\0') if name}


def plan(root: Path, scopes: list[str]) -> dict:
    root = Path(root).resolve()
    if not scopes:
        raise StateError('choose at least one scope: ' + ', '.join(SCOPES))
    unknown = set(scopes) - set(SCOPES)
    if unknown:
        raise StateError('unknown scope: ' + ', '.join(sorted(unknown)))
    moves: list[Path] = []
    rewrites: list[str] = []
    for scope in scopes:
        for relative in SCOPE_MOVES[scope]:
            moves += files_under(root, relative)
        for relative in SCOPE_REWRITES.get(scope, ()):
            if (root / relative).is_file():
                rewrites.append(relative)
    protected = tracked(root, moves)
    moves = [p for p in moves if p.relative_to(root).as_posix() not in protected]
    entries = []
    for path in moves + [root / r for r in rewrites]:
        stat = path.stat()
        relative = path.relative_to(root).as_posix()
        entries.append({'path': relative, 'bytes': stat.st_size, 'mtime_ns': stat.st_mtime_ns,
                        'action': 'reset to {}' if relative in rewrites else 'move'})
    token = hashlib.sha256(json.dumps({'scopes': sorted(scopes), 'entries': entries},
                                      sort_keys=True).encode()).hexdigest()[:12]
    return {'root': str(root), 'entries': entries, 'skipped_tracked': sorted(protected),
            'token': token}


def apply(root: Path, scopes: list[str], token: str) -> Path | None:
    preview = plan(root, scopes)
    if token != preview['token']:
        raise StateError('the files changed since the preview or the token is wrong; run preview '
                         'again and re-approve')
    root = Path(preview['root'])
    if not preview['entries']:
        return None
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    trash = root / '.reset-trash' / stamp
    trash.mkdir(parents=True)
    write_json(trash / 'MANIFEST.json',
               {'created': stamp, 'scopes': scopes, 'entries': preview['entries'],
                'restore': 'Move each file back to its original relative path.'})
    for entry in preview['entries']:
        source = root / entry['path']
        target = trash / entry['path']
        target.parent.mkdir(parents=True, exist_ok=True)
        if entry['action'] == 'move':
            source.chmod(0o666)
            shutil.move(str(source), str(target))
        else:
            shutil.copy2(source, target)
            atomic_write_text(source, '{}\n')
    for scope in scopes:
        for relative in SCOPE_MOVES[scope]:
            folder = root / relative
            if not folder.is_dir():
                continue
            for directory in sorted((p for p in folder.rglob('*') if p.is_dir()),
                                    key=lambda p: len(p.parts), reverse=True):
                if not any(directory.iterdir()):
                    directory.rmdir()
    return trash


def main(argv=None) -> int:
    safe_console()
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--root', type=Path, default=Path.cwd())
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('preview', 'apply'):
        p = sub.add_parser(name)
        p.add_argument('--scope', action='append', default=[], choices=SCOPES)
        if name == 'apply':
            p.add_argument('--token', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'preview':
            preview = plan(args.root, args.scope)
            total = sum(e['bytes'] for e in preview['entries'])
            print(f"{len(preview['entries'])} file(s), {total} bytes:")
            for entry in preview['entries']:
                print(f"  {entry['action']}: {entry['path']} ({entry['bytes']} bytes)")
            for name in preview['skipped_tracked']:
                print(f'  kept (tracked by git): {name}')
            print(f"Token: {preview['token']}  (files move to .reset-trash/; nothing is deleted)")
        else:
            trash = apply(args.root, args.scope, args.token)
            print(f'Moved to {trash}. Restore by moving files back.' if trash else 'Nothing to reset.')
        return 0
    except StateError as exc:
        print(f'reset_repo: {exc}', file=sys.stderr)
        return 1
    except OSError as exc:
        print(f'reset_repo: filesystem error: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
