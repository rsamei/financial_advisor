#!/usr/bin/env python3
"""Lint the repo's skill, command, reference and settings files.

Run from anywhere: python tools/lint_skills.py

Checks:
- Every SKILL.md under .claude/skills/* has frontmatter that parses, with non-empty `name`,
  `description`, `allowed-tools` and `framework_version`, and a `description` carrying a literal
  "Triggers on:" list (that list is what makes the skill discoverable).
- Every numbered reference file (.claude/skills/*/NN-*.md) carries a `framework_version` and NO
  `name`/`description`. Those files are LIBRARIES, reached only by an explicit Read from a
  command. A `name` would make one auto-invocable as a skill, which would let a rubric - a gate
  table, a score weight, a tax overlay - fire itself outside the command meant to execute it.
- Every .claude/commands/*.md starts with a `# /<name>` title and has NO frontmatter.
- .claude/settings.json is valid JSON with a permissions.allow list.

Frontmatter is parsed by a deliberately small scalar parser (below), not PyYAML: this repo has no
third-party runtime dependency, and the keys it lints are all plain scalars.

The commands directory may legitimately be empty in a fork that has not added any commands yet;
that is reported as a note, not a failure. A command file that exists is linted in full.

Stdlib only. Exit code 0 on success, 1 with a failure list otherwise.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
errors: list[str] = []

REFERENCE_FILE_RE = re.compile(r"^\d{2}-.+\.md$")
SCALAR_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$")


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def parse_frontmatter(path: Path, text: str) -> dict | None:
    """Return the frontmatter's top-level scalar keys, or None when there is none/it is broken.

    Only `key: value` lines at column 0 are read; nested blocks and lists are ignored rather than
    guessed at, because every key this linter rules on is a scalar. Quotes around a value are
    stripped so `description: "Triggers on: ..."` reads the same as the unquoted form.
    """
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---", 4)
    if end == -1:
        errors.append(f"{rel(path)}: unterminated frontmatter block")
        return None
    data: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if not line.strip() or line.startswith("#") or line[:1].isspace():
            continue
        match = SCALAR_RE.match(line)
        if not match:
            errors.append(f"{rel(path)}: frontmatter line is not `key: value`: {line[:60]!r}")
            continue
        key, value = match.group(1), match.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        data[key] = value
    return data


def check_skill(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        errors.append(f"{rel(path)}: missing frontmatter (file must start with ---)")
        return
    data = parse_frontmatter(path, text)
    if data is None:
        return

    for key in ("name", "description", "allowed-tools", "framework_version"):
        if not data.get(key):
            errors.append(f"{rel(path)}: frontmatter missing required key '{key}'")

    description = data.get("description", "")
    if description and "Triggers on:" not in description:
        errors.append(
            f"{rel(path)}: description must carry a literal 'Triggers on: ...' keyword list - "
            "that list is what makes the skill discoverable"
        )

    # Every allowlisted tool invocation must point at a file that exists, so a skill cannot
    # pre-approve a path that was renamed or never shipped.
    for match in re.finditer(r"python3? (tools/[^\s)*:]+\.py)", data.get("allowed-tools", "")):
        if not (ROOT / match.group(1)).is_file():
            errors.append(f"{rel(path)}: allowed-tools references a missing file: {match.group(1)}")


def check_reference(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    data = parse_frontmatter(path, text)
    if data is None:
        errors.append(
            f"{rel(path)}: numbered reference files need frontmatter carrying 'framework_version'"
        )
        return
    if "framework_version" not in data:
        errors.append(f"{rel(path)}: frontmatter missing 'framework_version'")
    for forbidden in ("name", "description"):
        if forbidden in data:
            errors.append(
                f"{rel(path)}: reference files must NOT declare '{forbidden}' - that would make "
                "this library file auto-invocable as a skill, outside the command meant to "
                "execute it"
            )


def check_command(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if text.startswith("---\n"):
        errors.append(
            f"{rel(path)}: command files must NOT have frontmatter - they start directly with "
            "the '# /<name>' title"
        )
        return
    lines = text.lstrip().splitlines()
    first = lines[0] if lines else ""
    if not first.startswith("# /"):
        errors.append(
            f"{rel(path)}: command file must start with a '# /<name>' title "
            f"(found: {first[:50]!r})"
        )


def check_settings() -> None:
    path = ROOT / ".claude" / "settings.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f".claude/settings.json: {exc}")
        return
    if not isinstance(data, dict):
        errors.append(".claude/settings.json: expected top-level JSON value to be an object")
        return
    permissions = data.get("permissions", {})
    if not isinstance(permissions, dict):
        errors.append(".claude/settings.json: expected permissions to be an object")
        return
    if not isinstance(permissions.get("allow"), list):
        errors.append(".claude/settings.json: expected permissions.allow to be a list")


def main() -> int:
    skills = sorted(ROOT.glob(".claude/skills/*/SKILL.md"))
    references = sorted(
        p for p in ROOT.glob(".claude/skills/*/*.md") if REFERENCE_FILE_RE.match(p.name)
    )
    commands = sorted((ROOT / ".claude" / "commands").glob("*.md"))
    notes: list[str] = []

    if not skills:
        errors.append("no SKILL.md files found - glob roots are wrong or the tree moved")
    if not references:
        errors.append("no numbered reference files found under .claude/skills/*/")
    if not (ROOT / ".claude" / "commands").is_dir():
        errors.append("no .claude/commands/ directory")
    elif not commands:
        notes.append("no command files yet under .claude/commands/")

    for skill in skills:
        check_skill(skill)
    for reference in references:
        check_reference(reference)
    for command in commands:
        check_command(command)
    check_settings()

    if errors:
        print(f"lint_skills: {len(errors)} failure(s)")
        for err in errors:
            print(f"  - {err}")
        return 1
    for note in notes:
        print(f"note: {note}")
    print(
        f"lint_skills: OK ({len(skills)} skills, {len(references)} reference files, "
        f"{len(commands)} commands, settings.json)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
