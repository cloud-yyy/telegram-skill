#!/usr/bin/env python3
"""PreToolUse hook: keep the agent away from the Telegram session and the CLI code.

The session file gives full control of the account, so the agent must only
reach Telegram through tg.py, which allows read requests only. This hook
blocks any tool call that mentions the session directory or the plugin's
scripts, except a plain `tg.py ...` invocation.

It is a guard rail against mistakes and prompt injection, not a sandbox:
a determined process running as the same OS user can still get around it.
Exit code 2 blocks the call and shows stderr to the agent.
"""

import json
import os
import re
import sys
from pathlib import Path

PLUGIN_ROOT = Path(os.environ.get("CLAUDE_PLUGIN_ROOT") or Path(__file__).resolve().parent.parent)
SCRIPTS_DIR = PLUGIN_ROOT / "skills" / "telegram-reader" / "scripts"
SESSION_DIR = Path(os.environ.get("TG_SKILL_HOME") or Path.home() / ".config" / "telegram-skill")

# `[uv run [--quiet] [--script]] [python3] <path>/tg.py <args>` with no shell operators,
# optionally followed by `2>&1`.
SAFE_TG_CALL = re.compile(
    r"""^\s*(?:uv\s+run\s+(?:--quiet\s+)?(?:--script\s+)?)?(?:python3?\s+)?
        (?P<path>["']?[^\s;&|<>`$()'"]*/tg\.py["']?)
        (?P<args>(?:\s+(?:"[^"`$]*"|'[^']*'|[^\s;&|<>`$()'"]+))*)
        (?:\s+2>&1)?\s*$""",
    re.VERBOSE,
)


def variants(path):
    """A path as the agent might write it: absolute, ~/..., $HOME/..."""
    path = str(path)
    home = str(Path.home())
    out = {path}
    if path.startswith(home + "/"):
        rest = path[len(home):]
        out |= {"~" + rest, "$HOME" + rest, "${HOME}" + rest}
    return out


PROTECTED = {
    "the Telegram session": variants(SESSION_DIR) | {".config/telegram-skill", "TG_SESSION_STRING"},
    "the telegram-reader scripts": variants(SCRIPTS_DIR),
}


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from strings(v)


def is_safe_tg_call(tool_input):
    command = tool_input.get("command")
    if not isinstance(command, str):
        return False
    m = SAFE_TG_CALL.match(command)
    if not m or "login" in m.group("args").split():
        return False
    return m.group("path").strip("'\"") in variants(SCRIPTS_DIR / "tg.py")


def main():
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    tool_input = event.get("tool_input") or {}
    if is_safe_tg_call(tool_input):
        return 0
    text = "\n".join(strings(tool_input))
    for what, needles in PROTECTED.items():
        if any(n in text for n in needles):
            print(f"Blocked: this call touches {what}. Use the telegram-reader CLI "
                  f"(`{SCRIPTS_DIR}/tg.py <command>`) and nothing else to access Telegram. "
                  "If the session is missing, ask the user to run `tg.py login` in their terminal.",
                  file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
