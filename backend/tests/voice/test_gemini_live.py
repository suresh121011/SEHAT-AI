"""Tests for Gemini 2.0 Flash Multimodal Live (Duplex Voice Follow-up) Phase 1."""

import asyncio
import base64
import json
import uuid
import pytest
from starlette.testclient import TestClient

from starlette.websockets import WebSocketDisconnect
from app.auth import Role, create_access_token
from app.config import get_settings
from app.voice import gemini_live
from tests.privacy.helpers import grant, new_case


class FakeGeminiWebSocket:
    """Mock WebSocket server simulating Gemini 2.0 Multimodal Live API responses."""

    def __init__(self, incoming_events: list[dict]):
        self.sent_messages: list[str] = []
        self._queue = asyncio.Queue()
        for event in incoming_events:
            self._queue.put_nowait(json.dumps(event))
        self.closed = False

    async def send(self, data: str) -> None:
        self.sent_messages.append(data)

    async def __aiter__(self):
        while not self.closed:
            try:
                msg = await asyncio.wait_for(self._queue.get(), timeout=2.0)
                yield msg
            except (asyncio.TimeoutError, asyncio.CancelledError):
                break

    async def close(self) -> None:
        self.closed = True


def test_build_setup_message_hindi():
    missing = [{"field_name": "spo2", "label": "SpO2", "is_danger_sign": True}]
    setup = gemini_live.build_setup_message("gemini-2.0-flash-exp", "hi", missing)
    assert setup["setup"]["model"] == "models/gemini-2.0-flash-exp"
    assert setup["setup"]["generationConfig"]["responseModalities"] == ["AUDIO"]
    prompt = setup["setup"]["systemInstruction"]["parts"][0]["text"]
    assert "हिंदी" in prompt
    assert "निदान" in prompt
    assert "spo2" in prompt
    tools = setup["setup"]["tools"][0]["functionDeclarations"]
    assert any(t["name"] == "submit_missing_field" for t in tools)
    assert any(t["name"] == "finish_intake" for t in tools)


def test_build_setup_message_odia():
    missing = [{"field_name": "duration", "label": "Duration", "is_danger_sign": False}]
    setup = gemini_live.build_setup_message("gemini-2.0-flash-exp", "or", missing)
    prompt = setup["setup"]["systemInstruction"]["parts"][0]["text"]
    assert "ଓଡ଼ିଆ" in prompt
    assert "ଔଷଧ" in prompt
    assert "duration" in prompt


def test_parse_gemini_message_tool_call():
    raw = json.dumps({
        "toolCall": {
            "functionCalls": [
                {
                    "id": "call_123",
                    "name": "submit_missing_field",
                    "args": {
                        "field_name": "spo2",
                        "value": "98",
                        "unit": "%",
                        "patient_phrase": "ନଅ ଦଶମିକ ଆଠ",
                    },
                }
            ]
        }
    })
    events = gemini_live.parse_gemini_message(raw)
    assert len(events) == 1
    ev = events[0]
    assert ev.event_type == "tool_call"
    assert ev.tool_call_id == "call_123"
    assert ev.tool_name == "submit_missing_field"
    assert ev.tool_args["field_name"] == "spo2"
    assert ev.tool_args["value"] == "98"


def test_parse_gemini_message_audio():
    dummy_pcm = b"\x00\x01\x02\x03"
    raw = json.dumps({
        "serverContent": {
            "modelTurn": {
                "parts": [
                    {
                        "inlineData": {
                            "mimeType": "audio/pcm;rate=24000",
                            "data": base64.b64encode(dummy_pcm).decode("ascii"),
                        }
                    }
                ]
            },
            "turnComplete": True,
        }
    })
    events = gemini_live.parse_gemini_message(raw)
    assert len(events) == 2
    assert events[0].event_type == "audio"
    assert events[0].audio_data == dummy_pcm
    assert events[1].event_type == "turn_complete"


def test_websocket_auth_rejection(client):
    """Test that connection without valid token closes with 4401."""
    case_id = str(uuid.uuid4())
    with client.websocket_connect(f"/api/v1/cases/{case_id}/voice/live-followup?token=invalid_token") as ws:
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 4401


def test_websocket_feature_disabled(client, monkeypatch):
    """Test rejection when GEMINI_LIVE_ENABLED is false."""
    monkeypatch.setenv("VOICE_ENABLED", "1")
    monkeypatch.setenv("GEMINI_LIVE_ENABLED", "0")
    get_settings.cache_clear()

    token, _ = create_access_token("patient_demo", Role.PATIENT)
    case_id = str(uuid.uuid4())
    with client.websocket_connect(f"/api/v1/cases/{case_id}/voice/live-followup?token={token}") as ws:
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 4404
    get_settings.cache_clear()


def test_websocket_consent_required(client, monkeypatch):
    """Test rejection when voice_cloud consent has not been granted."""
    monkeypatch.setenv("VOICE_ENABLED", "1")
    monkeypatch.setenv("GEMINI_LIVE_ENABLED", "1")
    monkeypatch.setenv("GOOGLE_AI_API_KEY", "test_key")
    get_settings.cache_clear()

    token, _ = create_access_token("anm_demo", Role.ANM)
    # Create case without voice_cloud consent (only triage consent)
    case_id = new_case(client, token=token)
    grant(client, token=token, case_id=case_id, voice_cloud=False)

    with client.websocket_connect(f"/api/v1/cases/{case_id}/voice/live-followup?token={token}") as ws:
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 4403
    get_settings.cache_clear()


def test_websocket_duplex_happy_path(client, monkeypatch):
    """Test full duplex loop: audio ingestion, tool extraction push and clean finish."""
    monkeypatch.setenv("VOICE_ENABLED", "1")
    monkeypatch.setenv("GEMINI_LIVE_ENABLED", "1")
    monkeypatch.setenv("GOOGLE_AI_API_KEY", "test_key")
    get_settings.cache_clear()

    token, _ = create_access_token("anm_demo", Role.ANM)
    case_id = new_case(client, token=token)
    grant(client, token=token, case_id=case_id, voice_cloud=True)

    # Mock Gemini Live WebSocket to yield a tool call then finish
    mock_events = [
        {
            "toolCall": {
                "functionCalls": [
                    {
                        "id": "call_1",
                        "name": "submit_missing_field",
                        "args": {
                            "field_name": "duration",
                            "value": "3 days",
                            "unit": "days",
                            "patient_phrase": "ତିନି ଦିନ ହେଲା",
                        },
                    }
                ]
            }
        },
        {
            "toolCall": {
                "functionCalls": [
                    {
                        "id": "call_2",
                        "name": "finish_intake",
                        "args": {"reason": "all_fields_collected"},
                    }
                ]
            }
        },
    ]
    fake_transport = FakeGeminiWebSocket(mock_events)
    client.app.state.gemini_live_transport = fake_transport

    with client.websocket_connect(f"/api/v1/cases/{case_id}/voice/live-followup?token={token}&language=or") as ws:
        # Send raw audio chunk from client
        ws.send_bytes(b"\x00\x01\x02\x03")

        # Receive tool call event pushed by backend
        msg1 = ws.receive_json()
        assert msg1["event"] == "field_submitted"
        assert msg1["field_name"] == "duration"
        assert msg1["value"] == "3 days"
        assert msg1["patient_phrase"] == "ତିନି ଦିନ ହେଲା"
        assert msg1["status"] == "pending_review"
        assert msg1["source"] == "gemini_live_duplex"
        assert "field_id" in msg1

        # Receive finish event
        msg2 = ws.receive_json()
        assert msg2["event"] == "intake_finished"
        assert msg2["reason"] == "all_fields_collected"

    # Verify fields persisted in DB via REST endpoint
    from tests.privacy.helpers import auth

    res = client.get(f"/api/v1/cases/{case_id}/voice/live-fields", headers=auth(token))
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["case_id"] == case_id
    assert len(data["fields"]) == 1
    stored = data["fields"][0]
    assert stored["field_name"] == "duration"
    assert stored["value"] == "3 days"
    assert stored["unit"] == "days"
    assert stored["patient_phrase"] == "ତିନି ଦିନ ହେଲା"
    assert stored["status"] == "pending_review"
    assert stored["source"] == "gemini_live_duplex"

    get_settings.cache_clear()


def test_normalize_field_name():
    assert gemini_live.normalize_field_name("oxygen_saturation") == "spo2"
    assert gemini_live.normalize_field_name("Blood-Pressure") == "bp"
    assert gemini_live.normalize_field_name("heart rate") == "pulse"
    assert gemini_live.normalize_field_name("fever_temp") == "temp"
    assert gemini_live.normalize_field_name("how_long") == "duration"
    assert gemini_live.normalize_field_name("last_menstrual_period") == "lmp"


def test_non_diagnostic_speech_filter():
    # English diagnostic violation
    violation, text = gemini_live.filter_non_diagnostic_speech("You have pneumonia, please take paracetamol", language="hi")
    assert violation is True
    assert "मैं कोई दवा या निदान नहीं बता सकता" in text

    # Hindi diagnostic violation
    violation, text = gemini_live.filter_non_diagnostic_speech("आपको मलेरिया है, पैरासिटामोल लें", language="hi")
    assert violation is True
    assert "डॉक्टर साहब इसकी जांच करेंगे" in text

    # Odia diagnostic violation
    violation, text = gemini_live.filter_non_diagnostic_speech("ଆପଣଙ୍କୁ ଡେଙ୍ଗୁ ରୋଗ ହୋଇଛି, ଔଷଧ ଖାଆନ୍ତୁ", language="or")
    assert violation is True
    assert "ମୁଁ କୌଣସି ଔଷଧ କିମ୍ବା ରୋଗ ନିର୍ଣ୍ଣୟ ଦେଇପାରିବି ନାହିଁ" in text

    # Safe question (no violation)
    safe_q = "ଆପଣଙ୍କର କାଶ କେତେ ଦିନ ହେଲା ହେଉଛି?"
    violation, text = gemini_live.filter_non_diagnostic_speech(safe_q, language="or")
    assert violation is False
    assert text == safe_q


def test_vernacular_prompt_injection():
    missing = [{"field_name": "spo2", "label": "SpO2"}, {"field_name": "duration", "label": "Duration"}]
    prompt_or = gemini_live.build_system_instruction("or", missing)
    assert "ଅମ୍ଳଜାନ" in prompt_or
    assert "କେତେ ଦିନ ହେଲା" in prompt_or

    prompt_hi = gemini_live.build_system_instruction("hi", missing)
    assert "ऑक्सीजन स्तर" in prompt_hi
    assert "कितने दिनों से" in prompt_hi


def test_websocket_synonym_normalization(client, monkeypatch):
    """Verifies that model synonym 'oxygen_saturation' maps to canonical 'spo2' in live session."""
    monkeypatch.setenv("VOICE_ENABLED", "1")
    monkeypatch.setenv("GEMINI_LIVE_ENABLED", "1")
    monkeypatch.setenv("GOOGLE_AI_API_KEY", "test_key")
    get_settings.cache_clear()

    token, _ = create_access_token("anm_demo", Role.ANM)
    case_id = new_case(client, token=token)
    grant(client, token=token, case_id=case_id, voice_cloud=True)

    mock_events = [
        {
            "toolCall": {
                "functionCalls": [
                    {
                        "id": "call_spo2",
                        "name": "submit_missing_field",
                        "args": {
                            "field_name": "oxygen_saturation",  # Non-canonical synonym
                            "value": "97",
                            "unit": "%",
                            "patient_phrase": "୯୭ ପ୍ରତିଶତ",
                        },
                    }
                ]
            }
        },
        {
            "toolCall": {
                "functionCalls": [
                    {
                        "id": "call_fin",
                        "name": "finish_intake",
                        "args": {"reason": "all_fields_collected"},
                    }
                ]
            }
        },
    ]
    client.app.state.gemini_live_transport = FakeGeminiWebSocket(mock_events)

    with client.websocket_connect(f"/api/v1/cases/{case_id}/voice/live-followup?token={token}&language=or") as ws:
        ws.send_bytes(b"\x00\x01\x02\x03")
        msg = ws.receive_json()
        assert msg["event"] == "field_submitted"
        assert msg["field_name"] == "spo2"  # Normalized to canonical key
        assert msg["value"] == "97"
        ws.receive_json()  # intake_finished

    get_settings.cache_clear()
