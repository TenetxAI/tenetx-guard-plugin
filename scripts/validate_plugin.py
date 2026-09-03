#!/usr/bin/env python3
"""Structural validation for this plugin.

Catches the mistakes that produce a *silently* ungoverned session rather than
an error: a hook config that drifted between its two copies, a declared event
Devin never delivers to plugins, a hook command pointing at a bootstrap that
is not in the tree, a skill Devin will refuse to load.

    python3 scripts/validate_plugin.py
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

MANIFESTS = (".devin-plugin/plugin.json", ".claude-plugin/plugin.json")
HOOK_CONFIGS = ("hooks.json", "hooks/hooks.json")
BOOTSTRAP = "hooks/tenetx_devin_hook.py"

# Devin delivers every event except these to plugin hooks.
UNSUPPORTED_EVENTS = ("SessionStart", "SessionEnd")
SUPPORTED_EVENTS = (
    "PreToolUse",
    "PostToolUse",
    "PermissionRequest",
    "UserPromptSubmit",
    "Stop",
    "PostCompaction",
)

# Lowercase alphanumeric with single - or . separators.
NAME_RE = re.compile(r"^[a-z0-9]+(?:[-.][a-z0-9]+)*$")

FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)


def _load_json(rel: str, errors: list[str]) -> dict | None:
    path = ROOT / rel
    if not path.is_file():
        errors.append(f"{rel}: missing")
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        errors.append(f"{rel}: invalid JSON ({exc})")
        return None


def check_manifests(errors: list[str]) -> None:
    """Both manifests must exist and agree — Devin picks one by precedence, and
    a divergent fallback would install a differently-named plugin on an older
    build."""
    loaded = {rel: _load_json(rel, errors) for rel in MANIFESTS}
    present = {rel: body for rel, body in loaded.items() if body is not None}
    for rel, body in present.items():
        name = body.get("name")
        if not isinstance(name, str) or not NAME_RE.match(name):
            errors.append(f"{rel}: 'name' must be lowercase alphanumeric, got {name!r}")
    if len(present) == len(MANIFESTS):
        first, second = (present[rel] for rel in MANIFESTS)
        if first != second:
            errors.append(
                f"{MANIFESTS[0]} and {MANIFESTS[1]} differ — keep them identical"
            )


def check_hook_configs(errors: list[str]) -> None:
    loaded = {rel: _load_json(rel, errors) for rel in HOOK_CONFIGS}
    present = {rel: body for rel, body in loaded.items() if body is not None}
    if len(present) == len(HOOK_CONFIGS):
        first, second = (present[rel] for rel in HOOK_CONFIGS)
        if first != second:
            errors.append(
                f"{HOOK_CONFIGS[0]} and {HOOK_CONFIGS[1]} differ — regenerate with "
                "scripts/gen_hooks_json.py"
            )
    for rel, body in present.items():
        for event in UNSUPPORTED_EVENTS:
            if event in body:
                errors.append(f"{rel}: Devin does not deliver {event} to plugins")
        for event, rows in body.items():
            if event in UNSUPPORTED_EVENTS:
                continue
            if event not in SUPPORTED_EVENTS:
                errors.append(f"{rel}: unknown event {event!r}")
            if not isinstance(rows, list) or not rows:
                errors.append(f"{rel}: {event} must be a non-empty list")
                continue
            for row in rows:
                _check_hook_row(rel, event, row, errors)


def _check_hook_row(rel: str, event: str, row: object, errors: list[str]) -> None:
    if not isinstance(row, dict):
        errors.append(f"{rel}: {event} entry is not an object")
        return
    entries = row.get("hooks")
    if not isinstance(entries, list) or not entries:
        errors.append(f"{rel}: {event} row has no 'hooks'")
        return
    for entry in entries:
        if not isinstance(entry, dict):
            errors.append(f"{rel}: {event} hook is not an object")
            continue
        if entry.get("type") != "command":
            errors.append(f"{rel}: {event} hook type must be 'command'")
        command = entry.get("command")
        if not isinstance(command, str) or not command:
            errors.append(f"{rel}: {event} hook has no command")
            continue
        if BOOTSTRAP.rsplit("/", 1)[-1] not in command:
            errors.append(
                f"{rel}: {event} command does not reference {BOOTSTRAP} — "
                "the hook would resolve to nothing"
            )
        timeout = entry.get("timeout")
        if not isinstance(timeout, int) or not 1 <= timeout <= 60:
            errors.append(f"{rel}: {event} timeout must be 1-60s, got {timeout!r}")


def check_bootstrap(errors: list[str]) -> None:
    path = ROOT / BOOTSTRAP
    if not path.is_file():
        errors.append(f"{BOOTSTRAP}: missing")
        return
    source = path.read_text(encoding="utf-8")
    try:
        ast.parse(source)
    except SyntaxError as exc:
        errors.append(f"{BOOTSTRAP}: syntax error ({exc})")
        return
    if "@@" in source:
        errors.append(f"{BOOTSTRAP}: unsubstituted placeholder")
    if not source.startswith("#!"):
        errors.append(f"{BOOTSTRAP}: missing shebang")
    # The bootstrap runs under whatever python3 the session machine has.
    for banned in ("match ", "ExceptionGroup"):
        if f"\n{banned}" in source:
            errors.append(f"{BOOTSTRAP}: avoid {banned.strip()!r} for older python3")


def check_skills(errors: list[str]) -> None:
    skills_dir = ROOT / "skills"
    if not skills_dir.is_dir():
        errors.append("skills/: missing")
        return
    found = sorted(skills_dir.glob("*/SKILL.md"))
    if not found:
        errors.append("skills/: no SKILL.md files")
    for path in found:
        rel = path.relative_to(ROOT)
        text = path.read_text(encoding="utf-8")
        match = FRONTMATTER_RE.match(text)
        if not match:
            errors.append(f"{rel}: missing --- frontmatter block")
            continue
        keys = {
            line.split(":", 1)[0].strip()
            for line in match.group(1).splitlines()
            if ":" in line and not line.startswith((" ", "\t", "-"))
        }
        for required in ("name", "description"):
            if required not in keys:
                errors.append(f"{rel}: frontmatter has no '{required}'")
        declared = _frontmatter_value(match.group(1), "name")
        if declared and declared != path.parent.name:
            errors.append(
                f"{rel}: name {declared!r} does not match directory "
                f"{path.parent.name!r} — the directory is the slash command"
            )


def _frontmatter_value(block: str, key: str) -> str:
    for line in block.splitlines():
        if line.startswith(f"{key}:"):
            return line.split(":", 1)[1].strip().strip("\"'")
    return ""


def check_rules(errors: list[str]) -> None:
    if not (ROOT / "AGENTS.md").is_file():
        errors.append("AGENTS.md: missing (the plugin's always-on rule)")
    for path in sorted((ROOT / "rules").glob("*.md")):
        rel = path.relative_to(ROOT)
        match = FRONTMATTER_RE.match(path.read_text(encoding="utf-8"))
        if not match:
            errors.append(f"{rel}: a rules/ file needs trigger frontmatter")
            continue
        trigger = _frontmatter_value(match.group(1), "trigger")
        allowed = ("always_on", "manual", "model_decision", "agent", "glob")
        if trigger not in allowed:
            errors.append(f"{rel}: trigger must be one of {allowed}, got {trigger!r}")


def main() -> int:
    errors: list[str] = []
    check_manifests(errors)
    check_hook_configs(errors)
    check_bootstrap(errors)
    check_skills(errors)
    check_rules(errors)
    if errors:
        for error in errors:
            print(f"FAIL  {error}", file=sys.stderr)
        print(f"\n{len(errors)} problem(s)", file=sys.stderr)
        return 1
    print("plugin layout OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
