"""Phase 6 P2: local IndicTrans2 translation path, with a mocked translator (the real model is gated and not
downloaded in CI). Verifies routing, source links and failure handling — not translation quality."""

import json

import pytest

from app.ai.fake_provider import FakeProvider
from app.ai.translate import MODEL_ID, MODEL_REVISION, REMOTE_CODE, build_translator
from tests.ai.helpers import add_transcript, by_field, extract
from tests.privacy.helpers import grant, new_case, token_for

HINDI = "मरीज़ को तीन दिन से बुखार है। SpO2 88 है।"


class MockTranslator:
    model_id = "mock-indictrans2"

    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    async def translate_transcript(self, text, language):
        self.calls.append(language)
        if self.fail:
            raise RuntimeError("model crashed")
        from app.ai.inputs import split_sentences

        canned = ["The patient has had fever for three days.", "SpO2 is 88."]
        return list(zip(split_sentences(text), canned))


@pytest.fixture
def setup(ai_client):
    tok = token_for(ai_client, "anm")
    cid = new_case(ai_client, tok)
    grant(ai_client, tok, cid, ai=True)
    ai_client.app.state.ai_provider = provider = FakeProvider()
    return tok, cid, provider


def test_translated_segments_are_flagged_and_link_to_the_original_sentence(ai_client, setup):
    tok, cid, provider = setup
    tid = add_transcript(cid, HINDI, language="hi")
    ai_client.app.state.translator = MockTranslator()
    v = extract(ai_client, tok, cid, text=None).json()
    assert v["skipped_sources"] == []
    spo2 = by_field(v)["spo2"]
    assert "machine_translated_unreviewed" in spo2["flags"]
    src = spo2["evidence"][0]["source"]
    assert src["type"] == "transcript_translated" and src["transcription_id"] == tid and src["machine_translated_unreviewed"]
    a, b = src["original_chars"]
    assert HINDI[a:b] == "SpO2 88 है।"
    assert all("बुखार" not in t for call in provider.calls for _, t in call["segments"])  # only English reaches the provider


def test_translation_failure_skips_the_source_with_a_reason(ai_client, setup):
    tok, cid, _ = setup
    tid = add_transcript(cid, HINDI, language="hi")
    ai_client.app.state.translator = MockTranslator(fail=True)
    r = extract(ai_client, tok, cid, text="BP 120/80.")
    assert r.status_code == 201
    assert r.json()["skipped_sources"] == [{"type": "transcript", "transcription_id": tid, "language": "hi", "reason": "translation_failed"}]


def test_enabled_without_the_pinned_model_refuses_to_start(tmp_path):
    class S:
        translation_model_dir = tmp_path

    with pytest.raises(RuntimeError, match="not downloaded"):
        build_translator(S())
    (tmp_path / "SEHAT_MANIFEST.json").write_text(json.dumps({"repo": MODEL_ID, "revision": "0" * 40}))
    with pytest.raises(RuntimeError, match="pinned revision"):
        build_translator(S())
    (tmp_path / "SEHAT_MANIFEST.json").write_text(json.dumps({"repo": MODEL_ID, "revision": MODEL_REVISION}))
    with pytest.raises(RuntimeError, match="no file hashes"):
        build_translator(S())
    import hashlib

    files = {}
    for name in REMOTE_CODE + ("config.json",):
        (tmp_path / name).write_text(f"# synthetic {name}")
        files[name] = hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()
    (tmp_path / "SEHAT_MANIFEST.json").write_text(json.dumps({"repo": MODEL_ID, "revision": MODEL_REVISION, "files": files}))
    assert build_translator(S()).model_id.startswith(MODEL_ID)
    (tmp_path / "modeling_indictrans.py").write_text("import os  # tampered")
    with pytest.raises(RuntimeError, match="does not match"):
        build_translator(S())
    (tmp_path / "modeling_indictrans.py").unlink()
    with pytest.raises(RuntimeError, match="missing"):
        build_translator(S())


def test_capabilities_report_translation_state(ai_client, setup):
    tok, _, _ = setup
    from tests.privacy.helpers import auth

    assert ai_client.get("/api/v1/ai/capabilities", headers=auth(tok)).json()["translation"] == "disabled"
    ai_client.app.state.translator = MockTranslator()
    caps = ai_client.get("/api/v1/ai/capabilities", headers=auth(tok)).json()
    assert caps["translation"] == "enabled" and caps["input_languages"] == ["en", "hi", "or"]


# ── Live (opt-in; needs the gated model downloaded with scripts/download_translation_models.py) ──────────


@pytest.mark.live
@pytest.mark.skipif(__import__("os").environ.get("RUN_LIVE_TRANSLATION_TESTS") != "1", reason="set RUN_LIVE_TRANSLATION_TESTS=1 after downloading IndicTrans2")
@pytest.mark.parametrize("language,text", [("hi", HINDI), ("or", "ରୋଗୀଙ୍କୁ ତିନି ଦିନ ହେଲା ଜ୍ୱର ହେଉଛି।")])
def test_live_indictrans2_translates_hi_and_or(language, text):
    import asyncio

    from app.config import get_settings

    get_settings.cache_clear()
    translator = build_translator(get_settings())
    pieces = asyncio.run(translator.translate_transcript(text, language))
    assert pieces and all(english.strip() and english.isascii() for _, english in pieces)
    print(language, [english for _, english in pieces])  # for the human running the test: quality is NOT asserted
