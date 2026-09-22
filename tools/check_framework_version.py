#!/usr/bin/env python3
"""Fail when a skill file changed without its framework_version being bumped.

Run from anywhere: python tools/check_framework_version.py [--base <git-ref>]

The rubric files under .claude/skills/ are executable specification: `/decide` reads
04-decision-evaluation.md and does what it says. A silent edit to a gate threshold, a score weight or a tax rule
changes every verdict the repo produces from that point on, and nothing in the output records
which version of the rubric was applied.

Bumping framework_version is the cheap fix: it makes the change visible in the diff, and it gives
a verdict recorded months ago something to be compared against.

Compares the working tree against a git ref (default: HEAD). Files that are new, or that have no
framework_version at all, are reported as notes rather than failures - the linter owns the
"must have a version" rule.

Stdlib only, plus git on PATH. Exit 0 on success, 1 with a failure list otherwise.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_RE = re.compile(r"^framework_version:\s*(\S+)\s*$", re.MULTILINE)

# Only these are executable specification. Commands are prose the model follows in-session and
# carry no version; tools are covered by tests.
WATCHED_GLOBS = [".claude/skills/*/*.md"]


def extract_version(text: str) -> str | None:
    """Read framework_version from the frontmatter block only.

    Scoped to the frontmatter so a version string quoted in the body - a changelog line, an
    example - cannot be mistaken for the file's own stamp.
    """
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---", 4)
    if end == -1:
        return None
    match = VERSION_RE.search(text[4:end])
    return match.group(1) if match else None


def git(*args: str) -> tuple[int, str]:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8"
    )
    return result.returncode, result.stdout


def main() -> int:
    parser = argparse.ArgumentParser(description="Check framework_version bumps.")
    parser.add_argument("--base", default="HEAD", help="git ref to compare against (default HEAD)")
    args = parser.parse_args()

    code, _ = git("rev-parse", "--git-dir")
    if code != 0:
        print("check_framework_version: not a git repository - nothing to compare, skipping")
        return 0

    code, _ = git("rev-parse", "--verify", f"{args.base}^{{commit}}")
    if code != 0:
        print(
            f"check_framework_version: no commit at {args.base!r} yet "
            "(fresh repo?) - nothing to compare, skipping"
        )
        return 0

    watched: list[Path] = []
    for pattern in WATCHED_GLOBS:
        watched.extend(sorted(ROOT.glob(pattern)))

    errors: list[str] = []
    notes: list[str] = []
    checked = 0

    for path in watched:
        relpath = str(path.relative_to(ROOT)).replace("\\", "/")
        code, old_text = git("show", f"{args.base}:{relpath}")
        if code != 0:
            notes.append(f"{relpath}: new file, not in {args.base}")
            continue
        new_text = path.read_text(encoding="utf-8")
        if old_text == new_text:
            continue
        checked += 1
        old_version = extract_version(old_text)
        new_version = extract_version(new_text)
        if new_version is None:
            notes.append(f"{relpath}: changed but carries no framework_version")
            continue
        if old_version == new_version:
            errors.append(
                f"{relpath}: content changed but framework_version is still {new_version!r}. "
                "This file is executable specification - a changed threshold or weight silently "
                "changes every verdict produced from now on. Bump the version in the same change."
            )

    if errors:
        print(f"check_framework_version: {len(errors)} failure(s)")
        for err in errors:
            print(f"  - {err}")
        return 1
    for note in notes:
        print(f"note: {note}")
    print(f"check_framework_version: OK ({checked} changed file(s) checked against {args.base})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
