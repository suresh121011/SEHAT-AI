## What and why

<!-- One or two sentences. Link the plan, issue or phase. -->

## How it was checked

- [ ] Backend: `make test-backend` — passed / skipped / xfailed: … (baseline in `RUNBOOK.md`)
- [ ] Frontend: `make test-frontend` — tests passed: …; TypeScript and ESLint clean
- [ ] Tests added, moved or skipped: `make ci` passes the skip gate; required modules and `MIN_EXECUTED` updated if needed
- [ ] UI change: checked in the running app on a scratch database; screenshots attached
- [ ] Not verified (say what and why):

## Safety checklist

Tick each line, or write "n/a" and the reason. See `AGENTS.md` and `GUARDRAILS.md`.

- [ ] Urgency is set only by the rules engine; nothing lowers it; missing data stays "needs human review" (Agent Rule 5)
- [ ] No change to urgency rules, thresholds or sources, or the owner approved it and the primary source is cited (Rule 8)
- [ ] No diagnostic, prescriptive or reassuring wording added anywhere a user can see it (Rule 2)
- [ ] Every new clinical value links to its source (Rule 7)
- [ ] Model calls go through the privacy gateway; case access, facility scope and consent are checked; an audit event is written
- [ ] Append-only tables are never updated in place; migrations were tested on a populated database
- [ ] Synthetic data only: no secrets, `.env` values, real names or identifiers in code, tests, logs or screenshots
- [ ] No claim of DPDP compliance, CDSCO clearance or clinical accuracy; every number has its denominator (Rules 3 and 4)
- [ ] The owner's work-in-progress paths are untouched (`AGENTS.md`, "Work in progress")
- [ ] `GUARDRAILS.md`, `MODEL_CARD.md` and the affected docs are updated for any change to a control or a model
- [ ] Safety change reviewed by the `sehat-safety-reviewer` subagent or a second person — result:
