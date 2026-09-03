#!/usr/bin/env python3
"""Emit the plugin's hooks.json to every layout Devin may read it from.

Devin picks a manifest by precedence (`.devin-plugin/plugin.json` >
`.claude-plugin/plugin.json` > root `plugin.json`) and then reads the hook
config from the path that layout declares — root `hooks.json` for the Devin
layout, `hooks/hooks.json` for the Claude layout. We ship both so the same
commit loads on either, and this generator keeps them byte-identical.

`scripts/validate_plugin.py` fails if a copy has drifted, so regenerate with:

    python3 scripts/gen_hooks_json.py
"""

from __future__ import annotations

import json
from pathlib import Path

# Plugin hooks cannot use SessionStart / SessionEnd — Devin does not deliver
# them to plugins. Session identity therefore comes from `session_id` on the
# events below, which the guard already prefers over the per-turn `prompt_id`.
EVENTS = (
    "PreToolUse",
    "PostToolUse",
    "PermissionRequest",
    "UserPromptSubmit",
    "Stop",
    "PostCompaction",
)

# The hook must stay inside its own timeout: the bootstrap allows 3s to fetch
# the guard plus 5s to run it, so 10s leaves headroom without letting a hung
# control plane stall the agent.
TIMEOUT_SECONDS = 10

BOOTSTRAP_REL = "hooks/tenetx_devin_hook.py"

# Locating the bootstrap is the whole difficulty of a plugin hook. Devin
# substitutes a plugin-root token into the command and exports the matching
# variable, but WHICH one depends on the layout it loaded us as
# (CLAUDE_PLUGIN_ROOT for the Claude layout, PLUGIN_ROOT for Agent Plugins),
# and the Devin-native layout's token is not documented. So we try every known
# name, then the installed-plugin directories, then the repo-committed
# enforcement hook that `tenetx install devin-cloud` writes.
#
# Failing to find it must exit 0: a plugin hook that exits 2 blocks the tool
# call, so a resolution miss would brick the session instead of failing open.
# Every miss leaves a breadcrumb so `tenetx doctor` can still see it.
COMMAND = (
    "sh -c '"
    "B=; "
    'for R in "${CLAUDE_PLUGIN_ROOT:-}" "${DEVIN_PLUGIN_ROOT:-}" '
    '"${PLUGIN_ROOT:-}" "${TENETX_DEVIN_PLUGIN_ROOT:-}"; do '
    f'if [ -n "$R" ] && [ -f "$R/{BOOTSTRAP_REL}" ]; then B="$R/{BOOTSTRAP_REL}"; break; fi; '
    "done; "
    'if [ -z "$B" ]; then for C in '
    f'"$HOME"/.devin/plugins/*/{BOOTSTRAP_REL} '
    f'"$HOME"/.devin/plugins/*/*/{BOOTSTRAP_REL} '
    f'"$HOME"/.config/devin/plugins/*/{BOOTSTRAP_REL} '
    f'"$HOME"/.config/devin/plugins/*/*/{BOOTSTRAP_REL} '
    '"${DEVIN_PROJECT_DIR:-.}/.devin/tenetx-hook.py"; do '
    'if [ -f "$C" ]; then B="$C"; break; fi; '
    "done; fi; "
    'if [ -n "$B" ]; then exec python3 "$B"; fi; '
    # Drain stdin so the agent is never left writing into a closed pipe.
    "cat >/dev/null 2>&1; "
    'mkdir -p "$HOME/.tenetx" 2>/dev/null; '
    'printf "%s\\n" "{\\"ts\\":\\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\\",'
    '\\"hook\\":\\"windsurf-devin-plugin\\",'
    '\\"reason\\":\\"bootstrap_not_found\\"}" '
    '>> "$HOME/.tenetx/capture_failures.jsonl" 2>/dev/null; '
    "exit 0"
    "'"
)


def hooks_config() -> dict[str, object]:
    return {
        event: [
            {
                "matcher": "",
                "hooks": [
                    {
                        "type": "command",
                        "command": COMMAND,
                        "timeout": TIMEOUT_SECONDS,
                    }
                ],
            }
        ]
        for event in EVENTS
    }


TARGETS = ("hooks.json", "hooks/hooks.json")


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    body = json.dumps(hooks_config(), indent=2) + "\n"
    for target in TARGETS:
        path = root / target
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
