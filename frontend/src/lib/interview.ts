// View logic for the scripted interview (backend app/interview, docs/plans/scripted-voice-interviewer.md). Pure
// functions only, testable with node --test. Nothing here chooses an answer, completes a danger-sign screen or
// supplies a measurement: the health worker records what was said, and the server validates it again.

export type Speaker = "patient" | "caregiver" | "anm";

export type InterviewAnswer = {
  choice: string | null;
  text: string | null;
  source: "typed" | "transcript";
  transcription_id: string | null;
  char_start: number | null;
  char_end: number | null;
  speaker: Speaker;
  is_unknown: boolean;
  recorded_by_role: string;
  recorded_at: string;
  status: string;
};

export type InterviewQuestion = {
  key: string;
  label: string;
  question_text: string;
  kind: "choice" | "text";
  options: string[];
  audience: "patient" | "worker";
  answerable: boolean;
  is_danger_sign: boolean;
  answered: boolean;
  answer: InterviewAnswer | null;
};

export type InterviewView = {
  case_id: string;
  script_version: string;
  language: string;
  notice: string;
  answers_hidden: boolean;
  questions: InterviewQuestion[];
  still_missing: string[];
};

export const TEXT_MAX = 500;
export const SPEAKER_WORDS: Record<Speaker, string> = { patient: "The patient", caregiver: "A caregiver", anm: "The health worker" };

/** Questions the patient answers here. Measurements and danger-sign screens are tasks for the triage form. */
export function patientQuestions(v: InterviewView): InterviewQuestion[] {
  return v.questions.filter((q) => q.answerable);
}

export function workerTasks(v: InterviewView): InterviewQuestion[] {
  return v.questions.filter((q) => !q.answerable);
}

/** A known answer. "Not sure" is recorded as said but never counts as answered. */
export function isKnown(q: InterviewQuestion): boolean {
  return q.answered && q.answer !== null && !q.answer.is_unknown;
}

export type Progress = { total: number; known: number; unknown: number; unanswered: number; done: boolean };

export function progress(v: InterviewView): Progress {
  const qs = patientQuestions(v);
  const known = qs.filter(isKnown).length;
  const unknown = qs.filter((q) => q.answered && !isKnown(q)).length;
  return { total: qs.length, known, unknown, unanswered: qs.length - known - unknown, done: qs.length > 0 && known + unknown === qs.length };
}

/** The first patient question with no recorded answer, or -1 when every one has an answer (even "not sure"). */
export function firstUnanswered(v: InterviewView): number {
  return patientQuestions(v).findIndex((q) => !q.answered);
}

export function answerText(a: InterviewAnswer | null): string {
  if (!a) return "Not answered";
  return a.choice ?? a.text ?? "Not answered";
}

export type Draft = {
  choice: string | null;
  text: string;
  speaker: Speaker;
  confirmed: boolean;
  /** A completed voice transcript of this case the answer was taken from, with its full text. */
  transcript: { transcription_id: string; text: string } | null;
};

export function emptyDraft(q: InterviewQuestion): Draft {
  const a = q.answer;
  return { choice: a?.choice ?? null, text: a?.text ?? "", speaker: a?.speaker ?? "patient", confirmed: false, transcript: null };
}

export type AnswerBody = {
  question_key: string;
  choice?: string;
  text?: string;
  source?: { transcription_id: string; char_start: number; char_end: number };
  speaker: Speaker;
  confirm: true;
};

/** The request body, or the reason nothing can be sent yet. An answer is never sent without the confirmation tick. */
export function buildAnswer(q: InterviewQuestion, d: Draft): { ok: true; body: AnswerBody } | { ok: false; error: string } {
  if (q.kind === "choice") {
    if (!d.choice || !q.options.includes(d.choice)) return { ok: false, error: "Choose one of the listed answers." };
  } else {
    const t = d.text.trim();
    if (!t) return { ok: false, error: "Enter the answer as it was said." };
    if (t.length > TEXT_MAX) return { ok: false, error: `Keep the answer under ${TEXT_MAX} characters.` };
  }
  if (!d.confirmed) return { ok: false, error: "Tick the box to confirm the answer was read back and is what was said." };
  const body: AnswerBody = { question_key: q.key, speaker: d.speaker, confirm: true };
  if (q.kind === "choice") body.choice = d.choice!;
  else body.text = d.text.trim();
  if (q.kind === "text" && d.transcript && d.transcript.text.length > 0) {
    body.source = { transcription_id: d.transcript.transcription_id, char_start: 0, char_end: d.transcript.text.length };
  }
  return { ok: true, body };
}

/** Plain words for a refused answer. Nothing was saved in any of these cases. */
export function interviewErrorWords(code: string | undefined, status: number | undefined, fallback: string): string {
  switch (code) {
    case "PII_DETECTED":
      return "The answer looks like it contains a name, phone number or ID. Record only what was said about the problem. Nothing was saved.";
    case "NOT_IN_SCRIPT":
      return "The question list changed because the triage form changed. The list was reloaded. Nothing was saved.";
    case "RECORD_ON_TRIAGE_FORM":
      return "Measurements and danger-sign screens are recorded on the review step, not here. Nothing was saved.";
    case "ANSWER_INVALID":
      return "The answer was not accepted. Choose a listed answer, or type what was said. Nothing was saved.";
    case "CONSENT_NOT_EFFECTIVE":
    case "CONSENT_WITHDRAWN":
      return "Triage consent is not in effect, so the interview has stopped. Nothing was saved.";
    case "RATE_LIMITED":
      return "Too many requests. Wait a moment and try again. Nothing was saved.";
  }
  if (status === 403) return "This account cannot record interview answers for this visit. Nothing was saved.";
  if (status === 404) return "The visit or the voice recording was not found. Nothing was saved.";
  return `${fallback} Nothing was saved.`;
}
