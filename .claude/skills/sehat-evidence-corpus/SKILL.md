---
name: sehat-evidence-corpus
description: Use when adding, checking or changing a protocol passage in SEHAT AI's evidence corpus (backend/app/evidence/passages.jsonl), when a rule is added or renamed and needs its corpus entry, or when the protocol evidence panel shows the wrong state. Covers the hash, licence and reviewer steps, the linter, and what must never be done (no invented or paraphrased quotes, no rule changes).
---

# SEHAT AI evidence corpus

The corpus holds the source passage behind each rule the engine can fire. The gate (`backend/app/evidence/gate.py`)
shows a passage as checked only when its hash, source, licence, threshold numbers and named reviewer all hold. What
ships must be VERIFIED or a location only (`docs/23_Protocol_Evidence.md` §3).

## Never

- Never write a passage from memory, a summary, a model's output or a secondary source. Only the exact words of the
  source the rule cites (`app/rules/sources.py`), copied from the source document.
- Never fill in `reviewer` or `reviewed_at` yourself. Only the named person who checked the text against the source
  gives them, in their own words in chat or a review.
- Never add or remove a name in `backend/app/evidence/reviewers.json`: that list is the owner's. A passage counts as
  checked only if its reviewer is on it.
- `passages.jsonl`, `gate.py` and `reviewers.json` are protected files: the hook asks the owner before any change.
- Never download a source document without the owner's permission (say the filename, source and size when asking).
- Never change a rule, a threshold or a source here. That is a `sehat-safety-change` with the owner's approval.
- Never mark a licence `quote_permitted` without having read the licence. A journal article stays `to_check` or
  `locator_only` until then.

## Add or check a passage

1. Read the rule in `backend/app/rules/` (its reason and cited source) and its entry in `passages.jsonl`
   (`passage_id` is the rule id in lower case).
2. With the owner's permission, obtain the source document; keep it outside the repo.
3. Check the licence. Record the outcome in `licence.status` and a one-line `licence.note`.
4. Copy the exact passage into a scratch file (trailing newlines are stripped before hashing), then, from `backend/`:
   `<venv>/bin/python scripts/lint_evidence_corpus.py --hash <scratch file>`. Put the text in `text` and the hash in
   `sha256`. Make the `locator` exact (table, page, section).
5. The passage must contain every number in `anchors`. If it does not, the source may not say what the rule says:
   stop and tell the owner (Agent Rule 8). The guard must not block it either (the linter says so); a genuine passage
   the guard blocks stays a location only, so tell the owner rather than rewording it.
6. The reviewer checks the text against the source; record their name and the date.
7. Run `<venv>/bin/python scripts/lint_evidence_corpus.py` and
   `SEHAT_DOTENV=0 ENVIRONMENT=test <venv>/bin/python -m pytest -q -p no:cacheprovider tests/evidence`.
   The tests fail if a shipped passage would show as unchecked or withheld.

## A rule was added or renamed

`tests/evidence/test_evidence.py` and the linter fail until every rule the engine can fire has an entry from its own
source. Add a location-only entry (text, hash and reviewer empty; anchors are the numbers in the rule's reason; the
licence `to_check`, or `project_text` for `SEHAT_POLICY`). A rule decided at run time (NEWS2, qSOFA, the safety floors)
is listed in `DYNAMIC_RULES` in `backend/app/evidence/corpus.py`.

## Check in the app

Open a case whose latest run fired rules, as a medical officer, at `http://127.0.0.1:3010`: the **Protocol evidence for
these rules** panel below **Why the rules gave …** shows each rule's source and the passage's state. The route is
`GET /api/v1/evidence/rules?ids=<rule ids>`.
