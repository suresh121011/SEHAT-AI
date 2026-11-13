# SEHAT AI: agent harness

A **harness** is everything around an AI coding agent that decides what it knows, what it may do, how it performs
recurring tasks and who checks its work. SEHAT AI is medical-safety software built largely with AI coding agents, so
the harness exists to make the owner's rules hold even when nobody is watching a session: no broad git operations, no
secrets read, no shared database touched, no change to urgency rules or safety code without the owner saying yes.

## Decision

**Claude Code's own project configuration (`.claude/`), with `AGENTS.md` as the rule book for every agent.**

| Option | Verdict | Why |
|:---|:---|:---|
| Claude Code native: `CLAUDE.md`, `.claude/settings.json`, hooks, skills, subagent | **Chosen** | Built into the tool the team uses; enforced before every command (hooks run first); reviewed in git like code; tested in CI; no new dependency |
| `AGENTS.md` (cross-agent convention) | **Chosen, as the shared rule book** | GitHub Copilot, Codex, Cursor and others read it; `CLAUDE.md` imports it, so the rules exist once |
| HarnessKit (third-party Claude Code plugin; `.harnesskit/` is git-ignored here) | Not adopted | Adds its own feature list, session logs and failure files that duplicate `docs/plans/`, the PR flow and CI; another moving part before a finale |
| Agent frameworks or multi-agent orchestration | Not relevant | They build agents; this repo needs guardrails around the agents that edit it |

## Layers

| Layer | File | What it does | Enforced by |
|:---|:---|:---|:---|
| Instructions | `AGENTS.md`, `CLAUDE.md` | Agent Rules 1–8, safety invariants, repository map, commands, git and data rules, protected files, WIP no-go list, definition of done | The agent reads them at session start |
| Permission rules | `.claude/settings.json` (`permissions.deny`) | Refuse reading or editing `.env` files, editing `sehat.db`, and the worst git and `sudo` commands | Claude Code, before any hook |
| Shell guard | `.claude/hooks/bash_guard.py` | Deny or ask for each shell command against the rules in `sehat_policy.py` | Claude Code `PreToolUse` hook on `Bash` |
| Edit guard | `.claude/hooks/edit_guard.py` | Ask before safety-critical files or the owner's WIP change; deny `.env` and `sehat.db` | `PreToolUse` hook on `Edit`, `Write`, `MultiEdit`, `NotebookEdit` |
| Skills | `.claude/skills/*/SKILL.md` (index: `SKILLS.md`) | Task checklists: safety changes, tests, real-app check, demo | Loaded when the task matches |
| Review subagent | `.claude/agents/sehat-safety-reviewer.md` | Review-only diff check against the Agent Rules; reports `PASS` or `BLOCK` with file:line | Asked for before finishing a safety change |
| Harness tests | `backend/tests/ci/test_agent_harness.py` | 129 tests pin the hook decisions, the settings wiring, the skills, the subagent's tools and the eight Agent Rules | CI; a required module in the skip gate |
| CI | `.github/workflows/ci.yml`, `backend/scripts/ci_skip_gate.py`, gitleaks | The final gate for every change, human or agent | GitHub, plus one approving review |

## What the hooks decide

| Decision | Commands or paths | Examples |
|:---|:---|:---|
| **Deny** | Broad staging and destructive git | `git add -A`, `git add .`, `-u`, `-f`; `git commit -a`; `git reset`; `git clean`; `git stash` (except `list`/`show`); force, mirror or delete pushes; `git checkout -- <path>` or `.`; `git restore` without `--staged`; `git branch -D`; `git worktree remove --force`; history rewrites |
| **Deny** | Secrets and the shared database | Any command that reads, copies or writes a `.env` file (`.env.example` is fine; `ls`/`test` on `.env` are fine); any command that uses `sehat.db` (searching the docs for the name is fine); editing either with a file tool |
| **Deny** | System-level and unreviewed code | `sudo`; `rm -r` of `/`, `~`, `.`, `..`, `*` or the repository; piping `curl`/`wget` into a shell or interpreter |
| **Ask** | Outward or owner-only actions | `git commit` (the owner says "commit this"); `git push`; global installs (`npm -g`, `pip --user`, `brew install`, `pipx`, `uv tool install`) |
| **Ask** | Safety-critical files | Edits, or shell writes (`sed -i`, `>`, `mv`, `rm`, `tee`), to `backend/app/rules/`, `backend/app/privacy/`, the output guard, NeMo rails, grounding, MAKER, red-flag keywords, consent, audit, auth, sessions, OIDC, rate limits, patient identity, case triage, the review queue, the database schema, config, CI, the invariant tests, `.claude/`, `AGENTS.md`, `CLAUDE.md` |
| **Ask** | The owner's work in progress | `routes/referrals.py`, `routes/cds.py`, `routes/patient.py`, `routes/surveillance.py`, `app/cds/`, `app/patient/`, `app/surveillance/`, `frontend/package.json` |
| Nothing | Everything else | The normal permission flow continues; the hooks never allow anything on their own |

Shell commands are parsed, not pattern-matched on raw text: chains (`;`, `&&`, `|`), new lines, `$(...)`, `bash -c`,
`env -S`, `xargs`, leading `VAR=value` and `git -C` are all followed; comments cannot hide a second command; heredoc
bodies (text for a file) are ignored. Text that cannot be parsed (for example unbalanced quotes) gets a conservative
scan for the worst patterns.

## Using it

1. **Start Claude Code in `sehat-ai/`** (the repo root) or in a worktree of it. Project settings, hooks, skills and
   agents load from the folder where the session starts. A session started in a parent folder (for example the
   `Hackathon` workspace) does not load them.
2. Hooks need `python3` (3.8 or later) on `PATH`. No packages are needed.
3. A denial is a project rule: change the approach. An "ask" is a question for the human at the keyboard.
4. Personal, machine-only settings go in `.claude/settings.local.json` (git-ignored), never in the shared file.

## Testing it

```bash
make hooks-test          # the 129 harness tests
echo '{"tool_name":"Bash","tool_input":{"command":"git add -A"}}' | python3 .claude/hooks/bash_guard.py
echo '{"tool_name":"Edit","tool_input":{"file_path":"backend/app/rules/atp.py"}}' | python3 .claude/hooks/edit_guard.py
```

The first prints a `deny` decision, the second an `ask` decision; an ordinary command prints nothing.

## Changing it

- The hook files, `settings.json`, `AGENTS.md` and `CLAUDE.md` are themselves protected (an agent asks first).
- Change a rule in `.claude/hooks/sehat_policy.py`, add test cases to `backend/tests/ci/test_agent_harness.py`, and
  update the tables above in the same change.
- New skills follow `SKILLS.md` ("Adding a skill"); the harness tests fail if a skill is not listed there.

## Limits

- **A hook sees command text, not program behaviour.** `python -c`, scripts, `make` targets, `git apply` and other
  programs can still change files. The hooks are a second line of defence behind the rules, code review and CI.
- **Pattern rules can be evaded** by unusual spellings; they are tuned to stop mistakes, not a determined attacker.
- **Only Claude Code runs these hooks.** Other agents rely on `AGENTS.md`, review and CI.
- **Failure behaviour.** If the guard itself raises an error it answers "ask". If `python3` is missing or the hook
  times out (10 s), Claude Code continues without it; the `permissions.deny` rules in `settings.json` still apply.
- It is not a sandbox and not a security boundary between people; see `SECURITY.md`.

## Second harness: the safety evaluation (Phase 6, `docs/25`)

The agent harness controls how code is changed. The safety-evaluation harness checks what the product does. One
command, `make safety-eval` (about 30 s), writes a scorecard in which every number carries its denominator (feature
#31) and exits 1 if any gate fails. `make safety-eval-quick` skips the pytest suites.

| Kind | Checks |
|:---|:---|
| Gated | The rules engine against 71 labelled synthetic vignettes and 45 threshold probes; a missing-data sweep; the raise-only merge; citations; the safety test suites; the two synthetic evaluations |
| Reported only (pinned, not gated) | The keyword suggester, the output guard, the input rail and redaction |

Its own tests (`backend/tests/safety_eval/`) run in CI. They hand the evaluation five deliberately broken components
(an engine, a threshold, a merge, a citation and a guard) and check that the matching check turns red. They also pin
the reported numbers, so a drop shows up in a pull request.
