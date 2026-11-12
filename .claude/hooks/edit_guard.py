#!/usr/bin/env python3
"""SEHAT AI agent harness: Claude Code PreToolUse hook for Edit, Write, MultiEdit and NotebookEdit (HARNESS.md).

Denies writes to `.env` secrets files and the shared `sehat.db`. Asks the owner before a change to a safety-critical
file (urgency rules, AI gateway, PII redaction, output guards, consent, audit, auth, CI gates, this harness, the agent
rules) or to the owner's work in progress. Prints nothing for every other path. The path lists live in
sehat_policy.py; backend/tests/ci/test_agent_harness.py pins them.

If the check itself fails, the hook asks instead of failing open. Exit code is always 0.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import sehat_policy  # noqa: E402

TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    if not isinstance(payload, dict) or payload.get("tool_name") not in TOOLS:
        return 0
    tool_input = payload.get("tool_input") or {}
    path = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not isinstance(path, str) or not path:
        return 0
    try:
        decision = sehat_policy.edit_decision(path)
    except Exception as exc:  # a bug in the guard must not silently allow
        decision = (sehat_policy.ASK, f"SEHAT edit guard could not check {path} ({type(exc).__name__}). Confirm it by hand.")
    if decision:
        level, reason = decision
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": level,
                                                 "permissionDecisionReason": "SEHAT harness: " + reason}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
