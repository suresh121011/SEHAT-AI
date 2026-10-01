"""Spoken read-back via Sarvam Bulbul v3 (docs/12 §6). Optional; off by default.

The spoken text is built server-side from the stored candidate (never from client input), so this
endpoint cannot be used as a general text-to-speech relay. The text contains a health value, which is
sent to Sarvam AI, so it requires the same `voice_cloud` consent as cloud transcription. When TTS is
unavailable the UI keeps the visible read-back; it never claims audio was played.
"""

from __future__ import annotations

import base64
import json

import anyio
import httpx

from app import audit, consent
from app.config import Settings
from app.database import transaction
from app.errors import ApiError, not_found
from app.voice import readback
SARVAM_TTS_MODEL = "bulbul:v3"
SARVAM_LANGUAGE = {"en": "en-IN", "hi": "hi-IN", "or": "od-IN"}


def _unavailable(reason: str) -> ApiError:
    return ApiError(503, "TTS_UNAVAILABLE", "Spoken read-back is unavailable; use the on-screen read-back", {"reason": reason})


def synthesize(text: str, language: str, *, base_url: str, api_key: str, timeout_s: float, transport: httpx.BaseTransport | None = None) -> bytes:
    try:
        with httpx.Client(timeout=timeout_s, transport=transport, follow_redirects=False) as client:
            resp = client.post(
                f"{base_url.rstrip('/')}/text-to-speech",
                headers={"api-subscription-key": api_key},
                json={"text": text, "language_code": SARVAM_LANGUAGE[language], "model": SARVAM_TTS_MODEL, "speech_sample_rate": 16000, "output_audio_codec": "wav"},
            )
    except httpx.HTTPError:
        raise _unavailable("tts_unreachable") from None
    if resp.status_code >= 400:
        raise _unavailable("tts_rejected" if resp.status_code < 500 else "tts_provider_error")
    try:
        audio = base64.b64decode(resp.json()["audios"][0], validate=True)
    except (ValueError, KeyError, IndexError, TypeError):
        raise _unavailable("tts_bad_response") from None
    if not audio.startswith(b"RIFF"):
        raise _unavailable("tts_bad_response")
    return audio


async def readback_audio(conn, principal, case_id: str, candidate_id: str, settings: Settings, request_id: str | None, transport=None) -> bytes:
    if not settings.voice_tts_enabled or not settings.sarvam_configured:
        raise _unavailable("tts_not_enabled")
    denial = None
    try:
        async with transaction(conn):
            await consent.load_case(conn, principal, case_id, "triage")  # reviewer only (creator ANM or MO)
            for purpose in ("triage", "voice_cloud"):
                await consent.require(conn, case_id, purpose)
            async with conn.execute(
                "SELECT c.*, t.language, t.transcript_raw FROM voice_candidates c JOIN voice_transcriptions t USING (transcription_id) WHERE c.candidate_id = ? AND c.case_id = ?",
                (candidate_id, case_id),
            ) as cur:
                row = await cur.fetchone()
    except consent.ConsentNotEffective as exc:
        denial = exc
    if denial is not None:
        raise await consent.audit_denied(conn, principal, case_id, denial, request_id)
    if row is None:
        raise not_found()
    normalized = json.loads(row["normalized_json"]) if row["normalized_json"] else None
    heard = (row["transcript_raw"] or "")[row["char_start"]:row["char_end"]]
    text = readback.readback_text(row["field"], row["raw_value"], row["raw_value2"], row["unit"], normalized, row["language"], heard=heard, flags=json.loads(row["flags_json"]))
    error: ApiError | None = None
    try:
        audio = await anyio.to_thread.run_sync(lambda: synthesize(text, row["language"], base_url=settings.sarvam_base_url, api_key=settings.sarvam_api_key, timeout_s=settings.sarvam_timeout_s, transport=transport))
    except ApiError as exc:
        error = exc
    async with transaction(conn):  # audited either way; an audit failure surfaces as a 500, never as silent success
        await audit.record(conn, principal=principal, action="voice_tts_generated", outcome="success" if error is None else "failure", case_id=case_id, request_id=request_id,
                           details=audit.VoiceTtsDetails(language=row["language"], ok=error is None))
    if error is not None:
        raise error
    return audio
