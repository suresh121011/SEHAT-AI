// Protocol evidence (docs/23): types and pure helpers for the panel beside "Why the rules gave …". No imports, so
// `node --test` runs it directly. The server's gate decides what may be shown; this only words it.

export type EvidenceState = "VERIFIED" | "UNCHECKED" | "LOCATOR_ONLY" | "WITHHELD";
export type EvidencePassage = {
  passage_id: string;
  state: EvidenceState;
  reasons: string[];
  locator: string | null; // none when the passage is from another source (withheld)
  text: string | null;
  reviewer: string | null;
  reviewed_at: string | null;
  message: string;
};
export type RuleEvidence = {
  rule_id: string;
  known: boolean;
  source: { id: string; citation: string; reference: string; version: string } | null;
  passages: EvidencePassage[];
};
export type EvidenceResponse = { rules: RuleEvidence[]; note: string };

export const STATE_WORDS: Record<EvidenceState, string> = {
  VERIFIED: "Checked passage",
  UNCHECKED: "Not yet checked",
  LOCATOR_ONLY: "Source location only",
  WITHHELD: "Passage withheld",
};

const RULE_ID = /^[A-Z0-9_]{3,64}$/;

/** The `ids` query value: valid rule ids only, each once, at most 50 (the server refuses anything else). */
export function idsParam(ruleIds: string[]): string {
  return [...new Set(ruleIds.filter((id) => RULE_ID.test(id)))].slice(0, 50).join(",");
}

/** A link for a web address or a DOI (through doi.org); anything else, such as a repository path, stays text. */
export function sourceHref(reference: string): string | null {
  if (/^https?:\/\//.test(reference)) return reference;
  const doi = /^doi:(10\.\S+)/i.exec(reference);
  return doi ? `https://doi.org/${doi[1]}` : null;
}
