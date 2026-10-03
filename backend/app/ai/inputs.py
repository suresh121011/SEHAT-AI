"""Build extraction input segments and their source references (docs/16 §4).

Sources, in order:
- typed intake text from the request (never stored raw; only its redacted segments are stored);
- completed voice transcripts of the case: English as-is; Hindi/Odia only through local IndicTrans2
  translation when TRANSLATION_ENABLED=1, otherwise skipped with a reason (the gateway would fail closed
  on non-Latin script anyway, and nothing is sent untranslated);
- reviewed OCR values are NOT segments: they are already human-reviewed and are added deterministically
  (see extract.py). Raw OCR text never reaches a model.

Every segment carries a source reference so each quote can be traced back: for transcripts, character
offsets into the stored raw transcript (exact when the segment needed no Unicode normalisation).
"""

import re
from dataclasses import dataclass, field

import aiosqlite

MAX_INTAKE_CHARS = 4000
MAX_SEGMENTS = 60
MAX_SEGMENT_CHARS = 600
# A sentence ends at . ! ? । ॥ followed by whitespace or the end (so "39.2" is not split), or at a newline.
# । and ॥ end sentences in Hindi and Odia.
_SENTENCE_END = re.compile(r"(?<=[.!?।॥])(?=\s|$)|\n")


@dataclass
class SourceSegment:
    segment_id: str
    raw: str
    source: dict


@dataclass
class Inputs:
    segments: list[SourceSegment] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)


def split_sentences(text: str) -> list[tuple[int, int]]:
    """(start, end) spans of non-empty sentences, trimmed; long sentences are cut at MAX_SEGMENT_CHARS."""
    spans = []
    bounds, start = [], 0
    for m in _SENTENCE_END.finditer(text):
        bounds.append((start, m.start()))
        start = m.end()
    bounds.append((start, len(text)))
    for s, e in bounds:
        while s < e and text[s].isspace():
            s += 1
        while e > s and text[e - 1].isspace():
            e -= 1
        while e - s > MAX_SEGMENT_CHARS:
            spans.append((s, s + MAX_SEGMENT_CHARS))
            s += MAX_SEGMENT_CHARS
        if e > s:
            spans.append((s, e))
    return spans


async def build(conn: aiosqlite.Connection, case_id: str, intake_text: str | None, include_voice: bool, translator=None) -> Inputs:
    out = Inputs()

    def add(raw: str, source: dict) -> None:
        if len(out.segments) >= MAX_SEGMENTS:
            out.skipped.append({**{k: v for k, v in source.items() if k in ("type", "transcription_id")}, "reason": "segment_limit"})
            return
        out.segments.append(SourceSegment(f"S{len(out.segments) + 1}", raw, source))

    if intake_text:
        for s, e in split_sentences(intake_text):
            add(intake_text[s:e], {"type": "manual_text"})

    if include_voice:
        async with conn.execute(
            "SELECT transcription_id, language, transcript_raw FROM voice_transcriptions WHERE case_id = ? AND status = 'completed' ORDER BY created_at",
            (case_id,),
        ) as cur:
            rows = await cur.fetchall()
        for r in rows:
            text = r["transcript_raw"] or ""
            if r["language"] == "en":
                for s, e in split_sentences(text):
                    add(text[s:e], {"type": "transcript", "transcription_id": r["transcription_id"], "language": "en", "transcript_chars": [s, e]})
            elif translator is None:
                out.skipped.append({"type": "transcript", "transcription_id": r["transcription_id"], "language": r["language"], "reason": "translation_disabled"})
            else:
                try:
                    pieces = await translator.translate_transcript(text, r["language"])
                except Exception:
                    out.skipped.append({"type": "transcript", "transcription_id": r["transcription_id"], "language": r["language"], "reason": "translation_failed"})
                    continue
                for (s, e), english in pieces:
                    add(english, {"type": "transcript_translated", "transcription_id": r["transcription_id"], "language": r["language"],
                                  "original_chars": [s, e], "machine_translated_unreviewed": True, "translation_model": translator.model_id})
    return out
