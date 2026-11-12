// Run: node --test src/lib/assistant.test.ts   (Node ≥ 22 strips TypeScript types natively; no test framework)
import { test } from "node:test";
import assert from "node:assert/strict";

import { DROP_WORDS, droppedLine, statusWords, toolLine, unavailableWords } from "./assistant.ts";

test("dropped sentences are counted by reason, never shown", () => {
  assert.equal(droppedLine([]), null);
  assert.equal(droppedLine([{ reason: "diagnostic_language" }]), "1 sentence dropped: diagnostic wording (1).");
  assert.equal(
    droppedLine([{ reason: "urgency_statement" }, { reason: "diagnostic_language" }, { reason: "urgency_statement" }]),
    "3 sentences dropped: a statement about urgency (2), diagnostic wording (1).",
  );
  assert.equal(droppedLine([{ reason: "some_new_reason" }]), "1 sentence dropped: some new reason (1).");
});

test("every gate reason has plain words", () => {
  for (const r of ["uncited_or_unknown_item", "number_not_in_cited_items", "missing_unchecked_tag", "urgency_statement", "diagnostic_language",
                   "prescriptive_language", "instruction_text", "too_many_sentences", "judgement_word", "number_in_words",
                   "too_many_citations", "polarity_mismatch", "unexpected_bracket", "removed_value_as_current",
                   "unexpected_character", "uncited_item_named", "cited_item_not_named", "item_form_mismatch",
                   "check_state_mismatch", "change_contradicted"]) assert.ok(DROP_WORDS[r], r);
});

test("the tool trace and refusals are shown in words", () => {
  assert.equal(toolLine({ called: ["get_urgency_record", "list_missing_information"], fetched_by_server: [], refused: [] }),
               "Tools the model called: rules engine record → missing information.");
  assert.equal(toolLine({ called: [], fetched_by_server: [], refused: ["list_conflicts", "unlisted tool"] }),
               "Tools the model called: none. Refused (not on this question's list): conflicting sources, unlisted tool.");
  assert.equal(toolLine({ called: [], fetched_by_server: ["get_visit_facts"], refused: [] }),
               "Fetched by the server (the model chose no tool): visit facts.");
});

test("an answer that could not be shown says why; availability says why not", () => {
  assert.match(statusWords({ status: "tool_limit" }, 8) ?? "", /more than 8 tool calls/);
  assert.match(statusWords({ status: "unparseable" }, 8) ?? "", /not in the agreed form/);
  assert.equal(statusWords({ status: "answered" }, 8), null);
  assert.equal(unavailableWords("cloud_disabled"), "cloud models are switched off on this server");
  assert.equal(unavailableWords(null), "the cloud model is not set up");
});
