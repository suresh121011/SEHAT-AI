"""Voice consent before the audio body is read (final remediation plan V2) and transcript reads inside one consistent
snapshot with the consent re-check (F7). Cloud provider mocked; synthetic audio only."""

from tests.privacy.helpers import audit_rows, auth, new_case, token_for, withdraw
from tests.privacy.helpers import grant as grant_consent
from tests.voice.test_api import _post, voice_client  # noqa: F401  (fixture)

OVERSIZED = b"\0" * (5 * 1024 * 1024)  # far over the 30 s cap: would be 413 if the body were read first


def test_withdrawn_cloud_consent_is_refused_before_the_body_is_read(voice_client):
    anm = token_for(voice_client, "anm")
    cid = new_case(voice_client, anm)
    assert grant_consent(voice_client, anm, cid, voice_cloud=True).status_code == 200
    withdraw(voice_client, anm, cid, "voice_cloud")
    r = _post(voice_client, anm, cid, audio=OVERSIZED)
    assert r.status_code == 403, r.text  # consent first, not AUDIO_TOO_LARGE
    assert voice_client.fake.calls == []
    assert any(e["action"] == "consent_denied" for e in audit_rows(voice_client))


def test_missing_cloud_consent_is_refused_before_the_body_is_read(voice_client):
    anm = token_for(voice_client, "anm")
    cid = new_case(voice_client, anm)
    assert grant_consent(voice_client, anm, cid, voice_cloud=False).status_code == 200
    assert _post(voice_client, anm, cid, audio=OVERSIZED).status_code == 403
    assert voice_client.fake.calls == []


def test_another_users_case_is_404_before_the_body_is_read(voice_client):
    anm = token_for(voice_client, "anm")
    cid = new_case(voice_client, anm)
    assert grant_consent(voice_client, anm, cid, voice_cloud=True).status_code == 200
    other = token_for(voice_client, "anm_other")
    assert _post(voice_client, other, cid, audio=OVERSIZED).status_code == 404
    assert voice_client.fake.calls == []


def test_with_consent_an_oversized_body_is_still_413(voice_client):
    anm = token_for(voice_client, "anm")
    cid = new_case(voice_client, anm)
    assert grant_consent(voice_client, anm, cid, voice_cloud=True).status_code == 200
    r = _post(voice_client, anm, cid, audio=OVERSIZED)
    assert r.status_code == 413 and voice_client.fake.calls == []


def test_transcripts_are_not_listed_after_triage_consent_is_withdrawn(voice_client):
    anm = token_for(voice_client, "anm")
    cid = new_case(voice_client, anm)
    assert grant_consent(voice_client, anm, cid, voice_cloud=True).status_code == 200
    t = _post(voice_client, anm, cid).json()
    assert voice_client.get(f"/api/v1/cases/{cid}/voice/transcriptions", headers=auth(anm)).status_code == 200
    withdraw(voice_client, anm, cid, "triage")
    for url in (f"/api/v1/cases/{cid}/voice/transcriptions", f"/api/v1/cases/{cid}/voice/transcriptions/{t['transcription_id']}"):
        r = voice_client.get(url, headers=auth(anm))
        assert r.status_code == 403 and "transcript" not in r.text.lower().replace("transcription", "")


def test_same_key_retry_after_withdrawal_returns_nothing(voice_client):
    """V4: a stored result is not handed back through the idempotency path once consent is withdrawn."""
    anm = token_for(voice_client, "anm")
    cid = new_case(voice_client, anm)
    assert grant_consent(voice_client, anm, cid, voice_cloud=True).status_code == 200
    key = "3f1d1c3e-1111-4a2b-9c3d-000000000001"
    assert _post(voice_client, anm, cid, key=key).status_code == 200
    calls = len(voice_client.fake.calls)
    withdraw(voice_client, anm, cid, "voice_cloud")
    r = _post(voice_client, anm, cid, key=key)
    assert r.status_code == 403 and "fever" not in r.text.lower()
    assert len(voice_client.fake.calls) == calls


def test_no_transcript_text_reaches_the_logs(voice_client, caplog):
    """V4: logs carry ids and reason codes only."""
    import logging

    anm = token_for(voice_client, "anm")
    cid = new_case(voice_client, anm)
    assert grant_consent(voice_client, anm, cid, voice_cloud=True).status_code == 200
    with caplog.at_level(logging.DEBUG):
        assert _post(voice_client, anm, cid).status_code == 200
    logged = " ".join(r.getMessage() for r in caplog.records).lower()
    assert "temperature is 102" not in logged and "three days" not in logged
