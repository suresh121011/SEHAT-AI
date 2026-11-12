# SEHAT AI: project skills

A skill is a short, task-specific instruction file that a coding agent loads only when the task needs it. Claude Code
discovers the skills in `.claude/skills/<name>/SKILL.md` when a session starts in this repo; other agents can read the
same files as plain Markdown. Each skill's `description` says when it applies.

| Skill | Use when | What it gives you |
|---|---|---|
| [`sehat-safety-change`](.claude/skills/sehat-safety-change/SKILL.md) | A change touches urgency rules, the rules engine, clinical thresholds or sources, consent, PII redaction, the AI gateway, model prompts or providers, output guards, grounding, red-flag keywords, audit, auth, sessions, facility isolation, append-only migrations, CI gates, or clinical or patient-facing wording | The approval rule for clinical rules, which tests to write first, the coding rules (raise-only, source links, consent, audit, append-only), and the finish checklist |
| [`sehat-run-tests`](.claude/skills/sehat-run-tests/SKILL.md) | Running the backend and frontend suites as CI does, focused runs, or comparing with the baseline | Exact commands (with the worktree venv rule), how to read skips and expected failures, and a table of focused runs |
| [`sehat-real-app-check`](.claude/skills/sehat-real-app-check/SKILL.md) | A document, OCR, summary or patient-registry change must be confirmed in the running app | Scratch database and ports, the headless Chrome check, which screenshots to look at, known non-failures |
| [`sehat-demo-run`](.claude/skills/sehat-demo-run/SKILL.md) | Starting, rehearsing or checking the offline demo stack | Pre-day checks, the three-terminal start, the pre-demo check, what to say honestly on stage, cleanup |

The subagent [`sehat-safety-reviewer`](.claude/agents/sehat-safety-reviewer.md) is not a skill: it is a review-only
agent (no edit tools) that checks a diff against the Agent Rules and reports `PASS` or `BLOCK` with file:line evidence.

## Planned

| Skill | Arrives with | Why not yet |
|---|---|---|
| `sehat-evidence-corpus` | Phase 4 (protocol evidence retrieval) | The curated, hash-pinned passage corpus does not exist yet; a skill for it now would describe code that is not there |

## Adding a skill

1. Create `.claude/skills/<name>/SKILL.md` with frontmatter `name: <name>` (same as the folder) and a `description`
   that starts with "Use when..." or "Use to..." and contains no `: ` sequence (it must stay valid YAML).
2. Keep it to what an agent needs for that task; link to the docs instead of copying them.
3. Add a row to the table above. `backend/tests/ci/test_agent_harness.py` fails if a skill is missing here or its
   name does not match its folder.
