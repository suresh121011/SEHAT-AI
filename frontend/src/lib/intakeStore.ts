// Local, unsubmitted notes for one case: follow-up answers (the body map is now saved with the case, PUT
// /cases/{id}/body-map; the `bodyMap` field is kept only so older stored notes still parse). There is no backend
// endpoint for follow-up answers, so they live in this browser tab only (sessionStorage), keyed by case. They are never sent to the server and never become triage input by themselves.
// Cleared on triage submit, consent withdrawal, logout, and when another case is opened (shared-device safety).

export type LocalNotes = { bodyMap: string[]; followUps: Record<string, { answer: string; question: string }> };

const PREFIX = "sehat:notes:";
const ACTIVE = "sehat:active-case";

type StorageLike = Pick<Storage, "getItem" | "setItem" | "removeItem" | "key" | "length">;

function store(): StorageLike | null {
  try {
    return typeof window === "undefined" ? null : window.sessionStorage;
  } catch {
    return null; // storage blocked: notes simply are not kept
  }
}

export function emptyNotes(): LocalNotes {
  return { bodyMap: [], followUps: {} };
}

export function readNotes(caseId: string, s: StorageLike | null = store()): LocalNotes {
  if (!s) return emptyNotes();
  try {
    const raw = s.getItem(PREFIX + caseId);
    const parsed = raw ? (JSON.parse(raw) as Partial<LocalNotes>) : {};
    return { bodyMap: Array.isArray(parsed.bodyMap) ? parsed.bodyMap.filter((x) => typeof x === "string") : [], followUps: parsed.followUps && typeof parsed.followUps === "object" ? parsed.followUps : {} };
  } catch {
    return emptyNotes();
  }
}

export function writeNotes(caseId: string, notes: LocalNotes, s: StorageLike | null = store()): void {
  if (!s) return;
  try {
    s.setItem(PREFIX + caseId, JSON.stringify(notes));
  } catch {
    // quota or privacy mode: ignore, notes are optional
  }
}

export function clearNotes(caseId: string, s: StorageLike | null = store()): void {
  s?.removeItem(PREFIX + caseId);
}

// Opening a different case removes every other case's notes from this tab.
export function activateCase(caseId: string, s: StorageLike | null = store()): void {
  if (!s) return;
  if (s.getItem(ACTIVE) !== caseId) {
    clearAllLocalNotes(s);
    s.setItem(ACTIVE, caseId);
  }
}

export function clearAllLocalNotes(s: StorageLike | null = store()): void {
  if (!s) return;
  const keys: string[] = [];
  for (let i = 0; i < s.length; i++) {
    const k = s.key(i);
    if (k && (k.startsWith(PREFIX) || k === ACTIVE)) keys.push(k);
  }
  for (const k of keys) s.removeItem(k);
}
