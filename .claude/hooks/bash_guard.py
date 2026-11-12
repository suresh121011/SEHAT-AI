#!/usr/bin/env python3
"""SEHAT AI agent harness: Claude Code PreToolUse hook for the Bash tool (HARNESS.md).

Reads the hook payload (JSON) on stdin. For a command that breaks the repo's standing rules it prints a
`permissionDecision` of "deny" (or "ask" for commits, pushes, global installs and safety-critical files); for
anything else it prints nothing, so Claude Code's normal permission flow continues. It never allows on its own.
The rules live in sehat_policy.py; backend/tests/ci/test_agent_harness.py pins them.

If the check itself fails, the hook asks instead of failing open. Exit code is always 0.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import sehat_policy  # noqa: E402

REPO_ROOT = HERE.parents[1]


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    if not isinstance(payload, dict) or payload.get("tool_name") != "Bash":
        return 0
    command = (payload.get("tool_input") or {}).get("command")
    if not isinstance(command, str) or not command.strip():
        return 0
    cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else None
    root = os.environ.get("CLAUDE_PROJECT_DIR") or str(REPO_ROOT)
    try:
        decision = sehat_policy.bash_decision(command, cwd=cwd, repo_root=root)
    except Exception as exc:  # a bug in the guard must not silently allow
        decision = (sehat_policy.ASK, f"SEHAT bash guard could not check this command ({type(exc).__name__}). Confirm it by hand.")
    if decision:
        level, reason = decision
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": level,
                                                 "permissionDecisionReason": "SEHAT harness: " + reason}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
