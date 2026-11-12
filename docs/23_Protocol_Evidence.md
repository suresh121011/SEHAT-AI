# 23. Protocol evidence behind fired rules

> **Status:** built in Phase 4 (feature #40, the PRD's "CRAG" layer). Synthetic data only. Display only: the evidence
> never sets, changes or explains away urgency.
>
> **No passage text is stored yet.** All 44 entries are a source location only, until the source documents are
> obtained under their licences and a named reviewer checks each passage against its source. Until then the screen shows
> the citation and the location in the source as cited, and says that the passage has not been added and checked.

## 1. What it does

`GET /evidence/rules?ids=ATP_RED_RR,NEWS2_HIGH` (ANM, medical officer or supervisor; patients get 403) takes rule ids
only: no case id and no patient data. For each id it returns whether the rule is known, the rule's registered source
(citation, reference, version) and that rule's passages, each with the state the evidence gate gave it (§3).

In the case workspace, the **Protocol evidence for these rules** panel sits directly below **Why the rules gave …** and
shows the same rules, those of the latest triage run.

Passages are **looked up by rule id, not searched**. The passage shown for a flag is the one curated for that rule from
that rule's own cited source, so a retrieval mistake cannot attach the wrong passage to a flag.

| Part | Rule |
|:---|:---|
| Ids accepted | 1 to 50 comma-separated ids matching `^[A-Z0-9_]{3,64}$`; anything else is `400 VALIDATION_ERROR`. Repeated ids are answered once |
| Unknown id | `known: false`, no source, no passages; nothing is guessed |
| Source | From `app/rules/sources.py`, the same register the rules cite. A web address or DOI becomes a link; a repository path stays as text |
| Text | Returned only for VERIFIED and UNCHECKED passages; never for LOCATOR_ONLY or WITHHELD |

## 2. The corpus

`backend/app/evidence/passages.jsonl`, one JSON line per passage:

| Field | Meaning |
|:---|:---|
| `passage_id` | Lower-case id, unique |
| `rule_ids` | The rules this passage supports |
| `source_id` | A registered source; it must be the source the rule cites |
| `locator` | Where in the source, only as far as the repo can back it (the citation or `docs/10`): table, chart, section or decision record, with the rule's wording. Where the section is not yet known it says "Section not yet confirmed" (WHO haemoglobin 2024, IHCI hypertension) |
| `anchors` | The threshold numbers of the rule (for example `22` and `10` for a respiratory rate above 22 or below 10): the numbers in a static rule's wording, and for the six rules built at run time the values in `DYNAMIC_ANCHORS`, pinned against the engine's own thresholds |
| `text`, `sha256` | The exact passage and the SHA-256 of its UTF-8 bytes; both empty until a passage is added |
| `licence` | `to_check`, `quote_permitted`, `locator_only` or `project_text`, with a note |
| `reviewer`, `reviewed_at` | The named person who checked the text against the source, and the date (`YYYY-MM-DD`, a real date, not in the future). The name must be on the owner's reviewer list (below) |

**Coverage:** one entry for each of the 44 rules the engine can fire: the static rules (AIIMS ATP and the scenario
packs), `NEWS2_HIGH`, `NEWS2_MEDIUM`, `NEWS2_LOW_MEDIUM`, `QSOFA_POSITIVE` and the two safety floors. By source: ATP
2022 35, NEWS2 3, SEHAT policy 2 (the safety floors, `docs/10`), and one each for Sepsis-3, WHO haemoglobin 2024,
the MoHFW Mother and Child Protection Card and IHCI hypertension. Today 42 are `to_check` and 2 are `project_text`;
none has text.

**The owner's reviewer list** is `backend/app/evidence/reviewers.json`: each entry is a name, a role and the date the
owner added it. It is empty today. Only the owner adds or removes a name; an agent never does.

`backend/scripts/lint_evidence_corpus.py` checks the corpus: valid lines, unique ids, registered sources, every rule
known and citing the passage's source, anchors equal to the rule's threshold numbers, a hash that matches the text, a
text the guard does not block, no hash without a text, reviewer and date together and only with a text, a reviewer on
the owner's list (and a valid list), and an entry for every rule the engine can fire. CI runs it through
the tests.

## 3. The evidence gate

`backend/app/evidence/gate.py` decides what each passage may show. It is deterministic: no model, no web search, no
ranking. It is corrective in the CRAG sense, but where CRAG would search further, it abstains.

| Check, in order | Result |
|:---|:---|
| The passage's source is not the rule's cited source | **WITHHELD** (`source_mismatch`), with no location |
| No text | **LOCATOR_ONLY** (`no_passage_text`) |
| The text's SHA-256 does not match the stored hash | **WITHHELD** (`hash_mismatch`) |
| The guard blocks the text (`app/ai/guard.py`, `check_input` and `check_output`): instructions to a model (`instruction_like_text`), or an override, urgency-lowering, diagnostic or prescriptive wording, or an identifier (`guard_blocked`) | **WITHHELD** |
| The licence does not allow quoting (`quote_permitted` or `project_text`) | **LOCATOR_ONLY** (`licence_not_confirmed`) |
| A threshold number of the rule is not in the text, no reviewer and date, or a reviewer not on the owner's list | **UNCHECKED** (`threshold_not_in_passage`, `not_reviewed`, `reviewer_not_listed`) |
| Otherwise | **VERIFIED**: "Checked against the source by {reviewer} on {date}." |

**What ships is VERIFIED or LOCATOR_ONLY only.** A test fails CI if any shipped passage would show as UNCHECKED or
WITHHELD. Those two states exist for a working copy and for a passage changed after it was hashed; the screen says
"not yet checked" or "withheld" in words, never as a checked passage.

## 4. Search (closed vocabulary)

`backend/app/evidence/index.py` builds an in-memory SQLite FTS5 table (BM25 ranking) from each passage's location,
the source's citation, the rules' wording and the passage text. It accepts **one word from a closed vocabulary** (the
words of the rules' wording and the locations); free text, and above all a patient's words, is refused. No embedding
model, no vector store, no network. No route uses it yet: it is for the assistant's read-only evidence tool (Phase 5).
It indexes every entry whatever its gate state and returns passage ids only, so Phase 5 must pass every hit through
`gate.view` before anything is shown or given to the assistant, with a test.

Why not vector retrieval: the corpus is 44 short entries tied one-to-one to rules, the facts that matter are exact
numbers, and a lexical index can be audited line by line. An embedding model would add a model, its failure modes and
its licence, and gain nothing at this size.

## 5. Adding or checking a passage

The `sehat-evidence-corpus` skill (`.claude/skills/sehat-evidence-corpus/SKILL.md`) has the steps. In short: obtain the
source document (downloading needs the owner's permission), check its licence, copy the exact passage, compute its hash
with `lint_evidence_corpus.py --hash`, have a named reviewer check it against the source and record their name and the
date (the reviewer must already be on the owner's list), run the linter and `tests/evidence/`. `passages.jsonl`,
`gate.py` and `reviewers.json` are protected files: an agent asks the owner before changing them. A change to `passages.jsonl` never changes a rule; a rule change follows the
`sehat-safety-change` skill and needs the owner's approval.

## 6. What it does not do

- No urgency, no change to triage, no explanation that softens a flag. `app/rules/` never imports `app/evidence/`
  (a test pins this), and the panel only displays.
- No web search, no model call, no generated or paraphrased text presented as a quote.
- No patient text in a lookup: the route takes rule ids, the index takes one vocabulary word.
- The passages are not a clinical protocol. They show the basis of a rule from its cited source; Agent Rule 8 still
  applies to any rule change.

## 7. Limits

- **No passage text yet:** the panel shows the citation and the location only. Making passages VERIFIED needs the
  source documents, a licence check per source (journal articles may not allow quoting; such a source stays
  `locator_only`) and a named clinical reviewer.
- Locations are at table, chart or section level; page numbers come when the documents are in hand.
- The threshold check is a number match, not a check of meaning; the reviewer is the real check.
- The guard check is the regex output guard: a backstop, since every text is also reviewed. It is strict: a genuine
  protocol passage with diagnostic wording (for example "suspected infection") is withheld and shown as a location;
  any exception for a specific passage is the owner's decision.
- **The hash detects drift, not tampering.** Whoever edits a text can re-hash it. The controls are review of every
  change, the owner's reviewer list (a typed name counts only if the owner listed it) and the agent harness, which
  makes `passages.jsonl`, `gate.py` and `reviewers.json` protected files (approved by the owner on 2026-11-13). The
  harness is a second line of defence, not a sandbox (`HARNESS.md`); `corpus.py` (anchors, validation) and the linter
  are not protected, but tests pin them.
- The route gives supervisors the source text for rule ids (no patient data); the case panel appears only where the
  case screen already shows "Why the rules gave …".

## 8. Verification

- `backend/tests/evidence/test_evidence.py` (23 tests): coverage of every rule from its own source, dynamic rule ids
  still in the engine, anchors equal to every rule's threshold numbers (the run-time ones pinned against the NEWS2 bands
  and qSOFA criteria), the shipped corpus lints clean and ships only VERIFIED or LOCATOR_ONLY, the rules package never
  imports the evidence package, each gate state, a reviewer not on the owner's list never verified (gate and linter),
  guard-blocked text withheld and failing the linter, review dates real and not in the future, text and location shown only when allowed, the closed-vocabulary index, and the API (staff
  access, unknown ids, patients refused, bad ids 400). Required in the CI skip gate.
- `frontend/src/lib/protocolEvidence.test.ts` (3 tests): the ids parameter, the state words, and links only for web
  addresses and DOIs.
- Real app (scratch servers, synthetic case, 2026-11-13): a RED run firing `ATP_RED_RR` and `NEWS2_HIGH` showed both
  rules' sources with working links (doi.org and rcp.ac.uk, opened in a new tab without referrer) and "Source location
  only" with the location as cited; no quote shown. ANM and medical officer 200, patient 403, an unknown id `known: false`,
  a lower-case id 400. No horizontal scroll at 375 px.
- An independent review-only safety review: PASS with no blocking issue. Its four warnings (locations the repo cannot
  back, a hash that detects drift but not tampering, the guard check limited to instruction patterns, and run-time
  anchors checked by nothing) and its notes are fixed; on the owner's approval the gate, corpus and reviewer list are
  protected files and VERIFIED needs a reviewer on the owner's list (§7).
- `backend/tests/ci/test_agent_harness.py` (5 cases): the hook asks before an edit or a `sed -i` to each of the three
  files, and leaves the unprotected search index alone.
