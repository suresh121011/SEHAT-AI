"""Voice API end to end with the cloud provider mocked (httpx.MockTransport) and real Silero VAD.
Label in reports: `tested_mock` for the cloud engine. Synthetic audio only (tests/fixtures/voice)."""

import json
import sqlite3
import uuid
from pathlib import Path

import httpx
import numpy as np
import pytest

from app.config import get_settings
from app.voice.audio import to_wav
from tests.privacy.helpers import audit_rows, auth, new_case, token_for, withdraw
from tests.privacy.helpers import grant as grant_consent
from tests.privacy.helpers import triage as submit_triage

pytest.importorskip("silero_vad")

FIXTURES = Path(__file__).parents[1] / "fixtures" / "voice"
EN_WAV = (FIXTURES / "en_fever_102.wav").read_bytes()
FAKE_KEY = "sk-test-not-a-real-key-123"


class FakeSarvam:
    """Records calls; replies like the documented API (docs.sarvam.ai, accessed 2026-09-30)."""

    def __init__(self, transcript="I have had fever for three days. My temperature is 102 degrees Fahrenheit. I do not have chest pain.", status=200, exc=None):
        self.calls: list[httpx.Request] = []
        self.transcript, self.status, self.exc = transcript, status, exc

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.exc:
            raise self.exc
        if request.url.path == "/text-to-speech":
            import base64

            return httpx.Response(200, json={"request_id": "r", "audios": [base64.b64encode(to_wav(np.zeros(1600, dtype=np.float32))).decode()]})
        if self.status != 200:
            return httpx.Response(self.status, json={"message": "provider says something with a secret", "code": "x"})
        return httpx.Response(200, json={"request_id": "r", "transcript": self.transcript, "language_code": "en-IN"})


@pytest.fixture
def voice_client(client, monkeypatch):
    monkeypatch.setenv("VOICE_ENABLED", "1")
    monkeypatch.setenv("VOICE_CLOUD_STT_ENABLED", "1")
    monkeypatch.setenv("VOICE_TTS_ENABLED", "1")
    monkeypatch.setenv("SARVAM_API_KEY", FAKE_KEY)
    monkeypatch.setenv("VOICE_LOCAL_MODEL_DIR", str(Path(get_settings().database_path).parent / "no-model"))
    get_settings.cache_clear()
    fake = FakeSarvam()
    client.app.state.voice_cloud_transport = httpx.MockTransport(fake)
    client.fake = fake
    yield client
    get_settings.cache_clear()


def _post(client, token, cid, audio=EN_WAV, language="en", engine="cloud", key=None, ctype="audio/wav"):
    key = key or str(uuid.uuid4())
    return client.post(
        f"/api/v1/cases/{cid}/voice/transcriptions?language={language}&engine={engine}&idempotency_key={key}",
        content=audio,
        headers={**auth(token), "Content-Type": ctype},
    )


@pytest.fixture
def anm(voice_client):
    return token_for(voice_client, "anm")


def _consented_case(client, token, voice_cloud=True):
    cid = new_case(client, token)
    assert grant_consent(client, token, cid, voice_cloud=voice_cloud).status_code == 200
    return cid


# ── feature flags ────────────────────────────────────────────────────────────────────────────────


def test_voice_disabled_by_default_makes_no_calls(client):
    anm = token_for(client, "anm")
    cid = new_case(client, anm)
    grant_consent(client, anm, cid, voice_cloud=True)
    resp = _post(client, anm, cid)
    assert resp.status_code == 404 and resp.json()["error"]["code"] == "FEATURE_DISABLED"
    caps = client.get("/api/v1/voice/capabilities", headers=auth(anm)).json()
    assert caps["voice_enabled"] is False and caps["engines"]["cloud"]["ready"] is False
    assert FAKE_KEY not in json.dumps(caps)


def test_cloud_disabled_flag_blocks_cloud_even_with_key(voice_client, anm, monkeypatch):
    monkeypatch.setenv("VOICE_CLOUD_STT_ENABLED", "0")
    get_settings.cache_clear()
    cid = _consented_case(voice_client, anm)
    resp = _post(voice_client, anm, cid)
    assert resp.status_code == 503 and resp.json()["error"]["details"]["reason"] == "cloud_not_enabled"
    assert voice_client.fake.calls == []


# ── happy path: transcript, provenance, candidates, read-back, prefill, triage ──────────────────


def test_cloud_transcription_end_to_end(voice_client, anm):
    cid = _consented_case(voice_client, anm)
    resp = _post(voice_client, anm, cid)
    assert resp.status_code == 200, resp.text
    t = resp.json()
    assert t["status"] == "completed" and t["engine"] == "cloud" and t["model_id"] == "saaras:v4"
    assert t["transcript_status"] == "machine transcript — not checked"
    assert t["speech_segments"], "VAD segments recorded"
    fields = {c["field"]: c for c in t["candidates"]}
    temp = fields["temp"]
    assert temp["heard_text"] == "102 degrees Fahrenheit"
    assert temp["normalized"] == {"temp_c": (102 - 32) * 5 / 9}
    assert "38.89 °C" in temp["readback_text"] and temp["resolution"] is None
    assert fields["symptom_duration"]["raw_value"] == 3

    # request to the provider: fixed host, key only in header, selected language sent
    req = voice_client.fake.calls[0]
    assert req.url == "https://api.sarvam.ai/speech-to-text"
    assert req.headers["api-subscription-key"] == FAKE_KEY
    body = req.content
    assert b'name="language_code"\r\n\r\nen-IN' in body and b'name="model"\r\n\r\nsaaras:v4' in body

    # nothing is pre-filled before a reviewer decision
    pre = voice_client.get(f"/api/v1/cases/{cid}/voice/prefill", headers=auth(anm)).json()
    assert pre["vitals"] == {} and pre["conflicts"] == {}

    ok = voice_client.post(f"/api/v1/cases/{cid}/voice/candidates/{temp['candidate_id']}/readback", json={"outcome": "confirmed"}, headers=auth(anm))
    assert ok.status_code == 200 and ok.json()["resolution"]["outcome"] == "confirmed"
    pre = voice_client.get(f"/api/v1/cases/{cid}/voice/prefill", headers=auth(anm)).json()
    assert pre["vitals"] == {"temp_c": (102 - 32) * 5 / 9}
    src = pre["values"]["temp"]["sources"][0]
    assert src["type"] == "voice_transcript" and src["transcript_chars"] == [temp["char_start"], temp["char_end"]]

    # the pre-filled value goes through the EXISTING triage endpoint unchanged
    result = submit_triage(voice_client, anm, cid, vitals={"temp_c": pre["vitals"]["temp_c"]})
    assert result.status_code == 200


def test_correction_replaces_misheard_value_and_is_labelled_manual(voice_client, anm):
    voice_client.fake.transcript = "temperature 100.2"  # heard; patient actually said 102
    cid = _consented_case(voice_client, anm)
    cand = _post(voice_client, anm, cid).json()["candidates"][0]
    assert "decimal_ambiguity" in cand["flags"]
    resp = voice_client.post(f"/api/v1/cases/{cid}/voice/candidates/{cand['candidate_id']}/readback",
                             json={"outcome": "corrected", "value": 102, "unit": "f"}, headers=auth(anm))
    assert resp.status_code == 200
    pre = voice_client.get(f"/api/v1/cases/{cid}/voice/prefill", headers=auth(anm)).json()
    assert pre["vitals"]["temp_c"] == (102 - 32) * 5 / 9
    assert pre["values"]["temp"]["sources"][0]["type"] == "voice_manual_correction"


@pytest.mark.parametrize("outcome", ["rejected", "unsure"])
def test_rejected_or_unsure_leave_field_missing(voice_client, anm, outcome):
    cid = _consented_case(voice_client, anm)
    cand = next(c for c in _post(voice_client, anm, cid).json()["candidates"] if c["field"] == "temp")
    voice_client.post(f"/api/v1/cases/{cid}/voice/candidates/{cand['candidate_id']}/readback", json={"outcome": outcome}, headers=auth(anm))
    pre = voice_client.get(f"/api/v1/cases/{cid}/voice/prefill", headers=auth(anm)).json()
    assert "temp_c" not in pre["vitals"]


def test_unknown_unit_cannot_be_confirmed_only_corrected(voice_client, anm):
    voice_client.fake.transcript = "temperature 60"
    cid = _consented_case(voice_client, anm)
    cand = _post(voice_client, anm, cid).json()["candidates"][0]
    assert cand["can_confirm"] is False
    url = f"/api/v1/cases/{cid}/voice/candidates/{cand['candidate_id']}/readback"
    assert voice_client.post(url, json={"outcome": "confirmed"}, headers=auth(anm)).json()["error"]["code"] == "CORRECTION_REQUIRED"
    bad = voice_client.post(url, json={"outcome": "corrected", "value": 60}, headers=auth(anm))
    assert bad.status_code == 400 and bad.json()["error"]["details"]["reason"] == "unit_required"


def test_conflicting_confirmed_values_are_not_merged(voice_client, anm):
    cid = _consented_case(voice_client, anm)
    for text in ("pulse 80", "pulse 120"):
        voice_client.fake.transcript = text
        cand = _post(voice_client, anm, cid).json()["candidates"][0]
        voice_client.post(f"/api/v1/cases/{cid}/voice/candidates/{cand['candidate_id']}/readback", json={"outcome": "confirmed"}, headers=auth(anm))
    pre = voice_client.get(f"/api/v1/cases/{cid}/voice/prefill", headers=auth(anm)).json()
    assert "pulse" not in pre["vitals"] and len(pre["conflicts"]["pulse"]) == 2


def test_patient_can_record_but_not_confirm(voice_client):
    patient = token_for(voice_client, "patient")
    cid = _consented_case(voice_client, patient)
    t = _post(voice_client, patient, cid)
    assert t.status_code == 200
    cand = t.json()["candidates"][0]
    resp = voice_client.post(f"/api/v1/cases/{cid}/voice/candidates/{cand['candidate_id']}/readback", json={"outcome": "confirmed"}, headers=auth(patient))
    assert resp.status_code == 403
    assert voice_client.get(f"/api/v1/cases/{cid}/voice/prefill", headers=auth(patient)).status_code == 403


def test_readback_body_rejects_values_on_confirm_and_extra_fields(voice_client, anm):
    cid = _consented_case(voice_client, anm)
    cand = _post(voice_client, anm, cid).json()["candidates"][0]
    url = f"/api/v1/cases/{cid}/voice/candidates/{cand['candidate_id']}/readback"
    assert voice_client.post(url, json={"outcome": "confirmed", "value": 99}, headers=auth(anm)).status_code == 400
    assert voice_client.post(url, json={"outcome": "confirmed", "note": "free text"}, headers=auth(anm)).status_code == 400
    assert voice_client.post(url, json={"outcome": "maybe"}, headers=auth(anm)).status_code == 400


# ── consent ──────────────────────────────────────────────────────────────────────────────────────


def test_cloud_without_voice_cloud_consent_is_denied_before_any_upload(voice_client, anm):
    cid = _consented_case(voice_client, anm, voice_cloud=False)
    resp = _post(voice_client, anm, cid)
    assert resp.status_code == 403 and resp.json()["error"]["code"] == "CONSENT_REQUIRED"
    assert voice_client.fake.calls == []
    denied = [r for r in audit_rows(voice_client) if r["case_id"] == cid and r["action"] == "consent_denied"]
    assert denied and "voice_cloud" in denied[-1]["details_json"]


def test_no_triage_consent_denies_any_voice(voice_client, anm):
    cid = new_case(voice_client, anm)
    assert _post(voice_client, anm, cid).status_code == 403
    assert voice_client.fake.calls == []


def test_consent_withdrawn_during_processing_discards_result(voice_client, anm):
    cid = _consented_case(voice_client, anm)
    original = voice_client.fake.__call__

    def withdraw_mid_flight(request):
        # Simulate the ANM withdrawing voice_cloud while the provider call is in flight.
        with sqlite3.connect(get_settings().database_path) as c:
            c.execute(
                "INSERT INTO consent_events (event_id, case_id, purpose, action, notice_version, language, notice_review_status, method, actor_id, actor_role, created_at) "
                "VALUES (?, ?, 'voice_cloud', 'withdrawn', 'v', 'en', 'r', 'staff_attested_verbal', 'a', 'anm', 't')",
                (str(uuid.uuid4()), cid),
            )
        return original(request)

    voice_client.app.state.voice_cloud_transport = httpx.MockTransport(withdraw_mid_flight)
    resp = _post(voice_client, anm, cid)
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "CONSENT_WITHDRAWN"
    with sqlite3.connect(get_settings().database_path) as c:
        row = c.execute("SELECT status, failure_code, transcript_raw FROM voice_transcriptions WHERE case_id = ?", (cid,)).fetchone()
        assert row == ("failed", "consent_changed", None)
        assert c.execute("SELECT count(*) FROM voice_candidates WHERE case_id = ?", (cid,)).fetchone() == (0,)


def test_withdrawn_triage_blocks_readback_and_prefill(voice_client, anm):
    cid = _consented_case(voice_client, anm)
    cand = _post(voice_client, anm, cid).json()["candidates"][0]
    withdraw(voice_client, anm, cid, "triage")
    assert voice_client.post(f"/api/v1/cases/{cid}/voice/candidates/{cand['candidate_id']}/readback", json={"outcome": "confirmed"}, headers=auth(anm)).status_code == 403
    assert voice_client.get(f"/api/v1/cases/{cid}/voice/prefill", headers=auth(anm)).status_code == 403


# ── routing and failures: explicit, no fallback ─────────────────────────────────────────────────


def test_local_engine_never_calls_cloud_and_reports_not_installed(voice_client, anm, monkeypatch):
    monkeypatch.setenv("VOICE_LOCAL_ASR_ENABLED", "1")
    get_settings.cache_clear()
    cid = _consented_case(voice_client, anm)
    resp = _post(voice_client, anm, cid, language="or", engine="local")
    assert resp.status_code == 503 and resp.json()["error"]["details"]["reason"] == "local_model_not_installed"
    en = _post(voice_client, anm, cid, language="en", engine="local")
    assert en.status_code == 422 and en.json()["error"]["code"] == "LANGUAGE_UNSUPPORTED"
    assert voice_client.fake.calls == []


@pytest.mark.parametrize(
    "fake, status, reason",
    [
        (FakeSarvam(exc=httpx.ReadTimeout("t")), 504, "cloud_timeout"),
        (FakeSarvam(status=429), 429, "cloud_rate_limited"),
        (FakeSarvam(status=503), 503, "cloud_unavailable"),
        (FakeSarvam(status=403), 503, "cloud_auth_failed"),
        (FakeSarvam(exc=httpx.ConnectError("c")), 503, "cloud_unreachable"),
    ],
)
def test_provider_failures_are_explicit_and_never_green(voice_client, anm, fake, status, reason):
    voice_client.app.state.voice_cloud_transport = httpx.MockTransport(fake)
    cid = _consented_case(voice_client, anm)
    resp = _post(voice_client, anm, cid)
    assert resp.status_code == status and resp.json()["error"]["details"] == {"reason": reason}
    assert "secret" not in resp.text and FAKE_KEY not in resp.text
    row = [r for r in audit_rows(voice_client) if r["case_id"] == cid][-1]
    assert row["action"] == "voice_transcription_failed" and reason in row["details_json"]
    pre = voice_client.get(f"/api/v1/cases/{cid}/voice/prefill", headers=auth(anm)).json()
    assert pre["vitals"] == {}  # nothing inferred from a failure


def test_no_speech_skips_the_engine(voice_client, anm):
    cid = _consented_case(voice_client, anm)
    resp = _post(voice_client, anm, cid, audio=to_wav(np.zeros(16000 * 2, dtype=np.float32)))
    assert resp.status_code == 200 and resp.json()["status"] == "no_speech" and resp.json()["candidates"] == []
    assert voice_client.fake.calls == []


def test_empty_transcript_is_not_evidence(voice_client, anm):
    voice_client.fake.transcript = "   "
    cid = _consented_case(voice_client, anm)
    resp = _post(voice_client, anm, cid)
    assert resp.json()["status"] == "empty_transcript" and resp.json()["candidates"] == []


@pytest.mark.parametrize(
    "audio, ctype, status",
    [(b"not audio", "audio/wav", 400), (EN_WAV, "application/json", 415), (EN_WAV, "multipart/form-data", 415), (b"\0" * (31 * 32000 + 5000), "audio/wav", 413)],
)
def test_bad_uploads_rejected_before_any_engine(voice_client, anm, audio, ctype, status):
    cid = _consented_case(voice_client, anm)
    assert _post(voice_client, anm, cid, audio=audio, ctype=ctype).status_code == status
    assert voice_client.fake.calls == []


# ── idempotency ──────────────────────────────────────────────────────────────────────────────────


def test_retry_with_same_key_returns_same_result_without_resending(voice_client, anm):
    cid = _consented_case(voice_client, anm)
    key = str(uuid.uuid4())
    a = _post(voice_client, anm, cid, key=key).json()
    b = _post(voice_client, anm, cid, key=key).json()
    assert a["transcription_id"] == b["transcription_id"] and len(voice_client.fake.calls) == 1
    assert len([r for r in audit_rows(voice_client) if r["case_id"] == cid and r["action"] == "voice_transcribed"]) == 1


def test_same_key_different_audio_conflicts(voice_client, anm):
    cid = _consented_case(voice_client, anm)
    key = str(uuid.uuid4())
    _post(voice_client, anm, cid, key=key)
    other = (FIXTURES / "en_pulse_oxygen.wav").read_bytes()
    resp = _post(voice_client, anm, cid, audio=other, key=key)
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert len(voice_client.fake.calls) == 1


def test_concurrent_duplicate_sends_audio_once(voice_client, anm):
    """Two simultaneous requests with one key: exactly one provider call; the other gets IN_PROGRESS
    (or the stored result if it arrives after completion)."""
    import threading

    cid = _consented_case(voice_client, anm)
    key = str(uuid.uuid4())
    gate = threading.Event()
    fake = voice_client.fake

    def slow(request):
        gate.wait(5)
        return fake(request)

    voice_client.app.state.voice_cloud_transport = httpx.MockTransport(slow)
    results = []
    threads = [threading.Thread(target=lambda: results.append(_post(voice_client, anm, cid, key=key))) for _ in range(2)]
    for t in threads:
        t.start()
    import time

    time.sleep(1.0)
    gate.set()
    for t in threads:
        t.join(10)
    codes = sorted(r.status_code for r in results)
    assert len(fake.calls) == 1
    assert codes == [200, 409]  # the second request arrived while the first was pending
    assert [r.json()["error"]["code"] for r in results if r.status_code == 409] == ["IN_PROGRESS"]


# ── privacy: audit and storage ───────────────────────────────────────────────────────────────────


def test_audit_holds_no_transcript_values_or_audio(voice_client, anm):
    cid = _consented_case(voice_client, anm)
    cand = next(c for c in _post(voice_client, anm, cid).json()["candidates"] if c["field"] == "temp")
    voice_client.post(f"/api/v1/cases/{cid}/voice/candidates/{cand['candidate_id']}/readback", json={"outcome": "corrected", "value": 103, "unit": "f"}, headers=auth(anm))
    import re

    blob = json.dumps([r for r in audit_rows(voice_client) if r["case_id"] == cid])
    # Remove random identifiers and hashes first: a UUID or SHA-256 can contain "102" by chance.
    blob = re.sub(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{64}", "<id>", blob)
    blob = re.sub(r"\d{4}-\d{2}-\d{2}T[\d:.]+(\+00:00|Z)?", "<ts>", blob)
    for leaked in ("fever", "Fahrenheit", "102", "103", "chest", "RIFF"):
        assert leaked not in blob, leaked
    verify = voice_client.post("/api/v1/audit/verify", headers=auth(token_for(voice_client, "supervisor"))).json()
    assert verify["ok"] is True


def test_no_audio_bytes_stored_in_database(voice_client, anm):
    cid = _consented_case(voice_client, anm)
    _post(voice_client, anm, cid)
    raw = Path(get_settings().database_path).read_bytes()
    speech = EN_WAV[44 + 16000 * 2 * 2: 44 + 16000 * 2 * 2 + 256]  # 2 s in: inside the utterance, not silence
    assert len(set(speech)) > 16
    assert speech not in raw and b"RIFF" not in raw


def test_supervisor_cannot_read_transcripts(voice_client, anm):
    cid = _consented_case(voice_client, anm)
    tid = _post(voice_client, anm, cid).json()["transcription_id"]
    sup = token_for(voice_client, "supervisor")
    assert voice_client.get(f"/api/v1/cases/{cid}/voice/transcriptions/{tid}", headers=auth(sup)).status_code == 404
    mo = token_for(voice_client, "mo")
    assert voice_client.get(f"/api/v1/cases/{cid}/voice/transcriptions/{tid}", headers=auth(mo)).status_code == 200


def test_other_accounts_case_is_404(voice_client, anm):
    cid = _consented_case(voice_client, anm)
    other = token_for(voice_client, "anm_other")
    assert _post(voice_client, other, cid).status_code == 404
    assert voice_client.fake.calls == []


# ── TTS ──────────────────────────────────────────────────────────────────────────────────────────


def test_tts_returns_wav_built_server_side(voice_client, anm):
    cid = _consented_case(voice_client, anm)
    cand = _post(voice_client, anm, cid).json()["candidates"][0]
    resp = voice_client.post(f"/api/v1/cases/{cid}/voice/candidates/{cand['candidate_id']}/tts", headers=auth(anm))
    assert resp.status_code == 200 and resp.content.startswith(b"RIFF")
    sent = json.loads(voice_client.fake.calls[-1].content)
    assert sent["text"] == cand["readback_text"] and sent["language_code"] == "en-IN" and sent["model"] == "bulbul:v3"


def test_tts_unavailable_is_explicit(voice_client, anm, monkeypatch):
    monkeypatch.setenv("VOICE_TTS_ENABLED", "0")
    get_settings.cache_clear()
    cid = _consented_case(voice_client, anm)
    cand = _post(voice_client, anm, cid).json()["candidates"][0]
    calls = len(voice_client.fake.calls)
    resp = voice_client.post(f"/api/v1/cases/{cid}/voice/candidates/{cand['candidate_id']}/tts", headers=auth(anm))
    assert resp.status_code == 503 and resp.json()["error"]["code"] == "TTS_UNAVAILABLE"
    assert len(voice_client.fake.calls) == calls


def test_tts_requires_voice_cloud_consent(voice_client, anm):
    cid = _consented_case(voice_client, anm)
    cand = _post(voice_client, anm, cid).json()["candidates"][0]
    withdraw(voice_client, anm, cid, "voice_cloud")
    calls = len(voice_client.fake.calls)
    resp = voice_client.post(f"/api/v1/cases/{cid}/voice/candidates/{cand['candidate_id']}/tts", headers=auth(anm))
    assert resp.status_code == 403 and len(voice_client.fake.calls) == calls


# ── Final council regressions (backend) ──────────────────────────────────────────────────────────


def _row(cid):
    with sqlite3.connect(get_settings().database_path) as c:
        return c.execute("SELECT status, failure_code FROM voice_transcriptions WHERE case_id = ?", (cid,)).fetchone()


def test_started_is_audited_before_the_engine_runs(voice_client, anm):
    cid = _consented_case(voice_client, anm)
    seen = []

    def record_audit_state(request):
        seen.extend(r["action"] for r in audit_rows(voice_client) if r["case_id"] == cid)
        return voice_client.fake(request)

    voice_client.app.state.voice_cloud_transport = httpx.MockTransport(record_audit_state)
    _post(voice_client, anm, cid)
    assert "voice_transcription_started" in seen  # committed before audio left


def test_failure_while_finalising_never_leaves_row_pending(voice_client, anm, monkeypatch):
    from app import audit

    cid = _consented_case(voice_client, anm)
    real = audit.record

    async def failing(conn, **kw):
        if kw["action"] == "voice_transcribed":
            raise RuntimeError("audit storage unavailable")
        return await real(conn, **kw)

    monkeypatch.setattr(audit, "record", failing)
    key = str(uuid.uuid4())
    assert _post(voice_client, anm, cid, key=key).status_code == 500
    assert _row(cid) == ("failed", "interrupted")
    monkeypatch.setattr(audit, "record", real)
    retry = _post(voice_client, anm, cid, key=key)
    assert retry.status_code == 200 and retry.json()["status"] == "failed"  # never IN_PROGRESS forever; new key to retry


def test_stale_pending_row_is_expired_as_abandoned(voice_client, anm):
    cid = _consented_case(voice_client, anm)
    key = str(uuid.uuid4())
    with sqlite3.connect(get_settings().database_path) as c:
        seq = c.execute("SELECT max(seq) FROM consent_events WHERE case_id = ?", (cid,)).fetchone()[0]
        from app.voice.audio import validate_wav

        sha = validate_wav(EN_WAV, 30).sha256
        c.execute(
            "INSERT INTO voice_transcriptions (transcription_id, case_id, created_by, idempotency_key, audio_sha256, language, engine, status, audio_duration_ms, consent_seq, created_at) "
            "VALUES (?, ?, (SELECT created_by FROM cases WHERE case_id = ?), ?, ?, 'en', 'cloud', 'pending', 1000, ?, '2026-01-01T00:00:00+00:00')",
            (str(uuid.uuid4()), cid, cid, key, sha, seq),
        )
    resp = _post(voice_client, anm, cid, key=key)
    assert resp.status_code == 200 and resp.json()["status"] == "failed" and _row(cid) == ("failed", "abandoned")
    assert voice_client.fake.calls == []


def test_transcripts_not_readable_after_triage_withdrawal(voice_client, anm):
    cid = _consented_case(voice_client, anm)
    tid = _post(voice_client, anm, cid).json()["transcription_id"]
    withdraw(voice_client, anm, cid, "triage")
    assert voice_client.get(f"/api/v1/cases/{cid}/voice/transcriptions/{tid}", headers=auth(anm)).status_code == 403
    assert voice_client.get(f"/api/v1/cases/{cid}/voice/transcriptions", headers=auth(anm)).status_code == 403


def test_prefill_reports_undecided_and_unsure_values(voice_client, anm):
    voice_client.fake.transcript = "temperature 38.5. pulse 88"
    cid = _consented_case(voice_client, anm)
    cands = {c["field"]: c for c in _post(voice_client, anm, cid).json()["candidates"]}
    voice_client.post(f"/api/v1/cases/{cid}/voice/candidates/{cands['pulse']['candidate_id']}/readback", json={"outcome": "unsure"}, headers=auth(anm))
    pre = voice_client.get(f"/api/v1/cases/{cid}/voice/prefill", headers=auth(anm)).json()
    assert pre["vitals"] == {}
    assert sorted((u["field"], u["state"]) for u in pre["unresolved"]) == [("pulse", "unsure"), ("temp", "undecided")]


def test_decision_on_a_stale_screen_is_refused(voice_client, anm):
    cid = _consented_case(voice_client, anm)
    cand = next(c for c in _post(voice_client, anm, cid).json()["candidates"] if c["field"] == "temp")
    url = f"/api/v1/cases/{cid}/voice/candidates/{cand['candidate_id']}/readback"
    first = voice_client.post(url, json={"outcome": "corrected", "value": 103, "unit": "f"}, headers=auth(anm)).json()
    stale = voice_client.post(url, json={"outcome": "confirmed"}, headers=auth(anm))  # screen still shows "undecided"
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "STALE_DECISION"
    ok = voice_client.post(url, json={"outcome": "rejected", "supersedes": first["resolution"]["event_id"]}, headers=auth(anm))
    assert ok.status_code == 200 and ok.json()["resolution"]["outcome"] == "rejected"


def test_patient_cannot_trigger_tts_egress(voice_client):
    patient = token_for(voice_client, "patient")
    cid = _consented_case(voice_client, patient)
    cand = _post(voice_client, patient, cid).json()["candidates"][0]
    calls = len(voice_client.fake.calls)
    assert voice_client.post(f"/api/v1/cases/{cid}/voice/candidates/{cand['candidate_id']}/tts", headers=auth(patient)).status_code == 403
    assert len(voice_client.fake.calls) == calls


def test_flagged_value_cannot_be_confirmed_via_api(voice_client, anm):
    voice_client.fake.transcript = "yesterday temperature was 104"
    cid = _consented_case(voice_client, anm)
    cand = _post(voice_client, anm, cid).json()["candidates"][0]
    assert cand["can_confirm"] is False
    resp = voice_client.post(f"/api/v1/cases/{cid}/voice/candidates/{cand['candidate_id']}/readback", json={"outcome": "confirmed"}, headers=auth(anm))
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "CORRECTION_REQUIRED"


def test_noise_only_transcript_is_empty_not_completed(voice_client, anm):
    voice_client.fake.transcript = "े"  # live on-device output for a noisy clip: a lone vowel sign
    cid = _consented_case(voice_client, anm)
    assert _post(voice_client, anm, cid).json()["status"] == "empty_transcript"
