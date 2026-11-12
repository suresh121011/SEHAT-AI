# SEHAT AI: Claude Code instructions

@AGENTS.md

The import above loads every project rule. This file adds only what is specific to Claude Code. Other coding agents
(GitHub Copilot, Codex, Cursor) read `AGENTS.md` directly.

## How to work here

- **Start Claude Code in this folder** (the repo root or a worktree of it). The hooks, permissions, skills and
  subagent in `.claude/` load only from the folder where the session starts (`HARNESS.md`).
- **One phase, one worktree.** Branch from `origin/main`:
  `git worktree add --no-track -b feat/<name> <path> origin/main`. The main checkout holds the owner's work in
  progress: never edit, stage, stash, reset or clean it.
- **Stop after each phase** and wait for the owner's review. Commit only when the owner says "commit this", and stage
  files by name.
- **Safety changes** follow the `sehat-safety-change` skill: tests first, owner approval for any rule change.
- **Before you say "done":** run the suites (`sehat-run-tests` skill), compare with the baseline in `RUNBOOK.md`, ask
  the `sehat-safety-reviewer` subagent to review the diff, and say plainly what you could not verify.

## Facts that save time

- The project venv is `.venv` at the root of the main checkout. A worktree uses it by absolute path. Tests run with
  `SEHAT_DOTENV=0 ENVIRONMENT=test` and `-p no:cacheprovider`.
- Scratch servers for checks: backend on 8010, frontend on 3010; open `http://127.0.0.1:3010`, not `localhost`. The
  owner may be using 8000 and 3000.
- Extraction stays on the local Qwen model (`AI_PROVIDER=local`); do not switch the extraction provider without asking.
  Azure (`gpt-4.1-mini`) serves the Semantic Kernel assistant and the connection check.
- Never read or print `.env`. Never use `sehat.db`. Never install anything globally.
- The hooks answer some commands with "deny" or "ask". A denial is a project rule, not an error to work around: change
  the approach or ask the owner.

## Project skills and agents

| Name | Use when |
|---|---|
| `sehat-safety-change` (skill) | Any change to urgency rules, consent, privacy, model calls, guards, audit, auth, CI gates or clinical wording |
| `sehat-run-tests` (skill) | Running the suites the way CI does, focused test runs, comparing with the baseline |
| `sehat-real-app-check` (skill) | Confirming a document, summary or registry change in the real app in headless Chrome |
| `sehat-demo-run` (skill) | Starting or rehearsing the offline demo stack |
| `sehat-safety-reviewer` (subagent) | Review-only check of a diff against the Agent Rules before you finish |

See `SKILLS.md` for the index and `docs/20_Agent_Harness_and_Governance_Files.md` for what every governance file holds.
