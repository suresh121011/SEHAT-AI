"""Gemini 2.0 Flash Multimodal Live (Duplex Voice Follow-Up) integration (docs/20).

Enables real-time, bidirectional spoken follow-up with patients in Hindi and Odia.
- Uses Gemini Multimodal Live API over WebSockets (16 kHz PCM audio in, 24 kHz PCM audio out).
- Performs live function calling (`submit_missing_field`) as the patient speaks.
- Adheres strictly to SEHAT AI non-diagnostic guardrails: cannot diagnose, cannot prescribe.
- Voice entries are tagged with provenance and status `pending_review` (human sign-off mandatory).
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Literal

from app.errors import ApiError

logger = logging.getLogger("sehat.voice.gemini_live")

LIVE_API_ENDPOINT = "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContent"

TOOL_DECLARATIONS = [
    {
        "name": "submit_missing_field",
        "description": "Submit a clinical measurement or symptom detail directly extracted from the patient's spoken answer.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "field_name": {
                    "type": "STRING",
                    "description": "Canonical field name, e.g. spo2, pulse, resp_rate, bp, temp, duration, pain_severity, chief_complaint, lmp, edd, gravida_parity",
                },
                "value": {
                    "type": "STRING",
                    "description": "Extracted numerical or descriptive value (e.g., '98', '3 days', 'severe')",
                },
                "unit": {
                    "type": "STRING",
                    "description": "Measurement unit if applicable (e.g., '%', 'bpm', 'mmHg', 'deg F', 'days')",
                },
                "patient_phrase": {
                    "type": "STRING",
                    "description": "The exact vernacular phrase spoken by the patient in Hindi or Odia",
                },
            },
            "required": ["field_name", "value"],
        },
    },
    {
        "name": "finish_intake",
        "description": "Conclude the missing information follow-up conversation when all questions are answered or patient wishes to stop.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "reason": {
                    "type": "STRING",
                    "enum": ["all_fields_collected", "patient_finished", "patient_interrupted"],
                }
            },
            "required": ["reason"],
        },
    },
]

FIELD_NORMALIZATION_MAP: dict[str, str] = {
    "oxygen": "spo2",
    "sp_o2": "spo2",
    "o2_sat": "spo2",
    "oxygen_saturation": "spo2",
    "spo2": "spo2",
    "bp": "bp",
    "blood_pressure": "bp",
    "blood_pressure_systolic": "bp",
    "bp_systolic": "bp",
    "pulse": "pulse",
    "heart_rate": "pulse",
    "pulse_rate": "pulse",
    "heartrate": "pulse",
    "temp": "temp",
    "temperature": "temp",
    "fever": "temp",
    "fever_temp": "temp",
    "resp_rate": "resp_rate",
    "respiratory_rate": "resp_rate",
    "respiration_rate": "resp_rate",
    "breathing_rate": "resp_rate",
    "pain_severity": "pain_severity",
    "severity": "pain_severity",
    "pain": "pain_severity",
    "duration": "duration",
    "how_long": "duration",
    "danger_signs": "danger_signs",
    "maternal_danger_screen": "danger_signs",
    "lmp": "lmp",
    "last_menstrual_period": "lmp",
    "edd": "edd",
    "expected_delivery_date": "edd",
    "gravida_parity": "gravida_parity",
    "hb": "hb",
    "haemoglobin": "hb",
    "hemoglobin": "hb",
    "chief_complaint": "chief_complaint",
    "complaint": "chief_complaint",
}


def normalize_field_name(raw_name: str | None) -> str:
    """Normalizes any model field name synonym to canonical Layer 4 schema key."""
    if not raw_name:
        return "chief_complaint"
    key = raw_name.strip().lower().replace(" ", "_").replace("-", "_")
    return FIELD_NORMALIZATION_MAP.get(key, key)


VERNACULAR_QUESTIONS: dict[str, dict[str, str]] = {
    "spo2": {
        "hi": "क्या आपका ऑक्सीजन स्तर (SpO2) नापा गया है?",
        "or": "ଆପଣଙ୍କର ଅମ୍ଳଜାନ (SpO2) ମପା ହୋଇଛି କି?",
    },
    "duration": {
        "hi": "यह परेशानी कितने दिनों से हो रही है?",
        "or": "ଏହି ସମସ୍ୟା କେତେ ଦିନ ହେଲା ହେଉଛି?",
    },
    "temp": {
        "hi": "बुखार कितना है, क्या थर्मामीटर से नापा गया था?",
        "or": "ଜ୍ୱର କେତେ ଅଛି କିମ୍ବା କେତେ ଡିଗ୍ରୀ ମପା ହୋଇଥିଲା?",
    },
    "pain_severity": {
        "hi": "दर्द कितना तेज है? (हल्का, मध्यम या बहुत तेज?)",
        "or": "ଯନ୍ତ୍ରଣା କେତେ ଅଧିକ ହେଉଛି? (ସ୍ୱଳ୍ପ, ମଧ୍ୟମ ନା ପ୍ରବଳ?)",
    },
    "bp": {
        "hi": "क्या आपका ब्लड प्रेशर (बीपी) नापा गया है?",
        "or": "ଆପଣଙ୍କର ରକ୍ତଚାପ (ବିପି) ମପା ହୋଇଛି କି?",
    },
    "pulse": {
        "hi": "नाड़ी या नब्ज कितनी चल रही है?",
        "or": "ନାଡ଼ିର ଗତି କେତେ ଅଛି କିମ୍ବା ମପା ହୋଇଛି କି?",
    },
    "danger_signs": {
        "hi": "क्या गर्भावस्था में कोई ब्लीडिंग, तेज सिरदर्द या पेट में गंभीर दर्द है?",
        "or": "ଗର୍ଭାବସ୍ଥାରେ କୌଣସି ରକ୍ତସ୍ରାବ, ମୁଣ୍ଡବିନ୍ଧା କିମ୍ବା ଅତ୍ୟଧିକ ପେଟ ଯନ୍ତ୍ରଣା ହେଉଛି କି?",
    },
    "lmp": {
        "hi": "अंतिम मासिक धर्म (LMP) की तारीख क्या थी?",
        "or": "ଶେଷ ଋତୁସ୍ରାବ (LMP) ର ତାରିଖ କ'ଣ ଥିଲା?",
    },
    "chief_complaint": {
        "hi": "आपकी मुख्य समस्या या तकलीफ क्या है?",
        "or": "ଆପଣଙ୍କର ମୁଖ୍ୟ ସମସ୍ୟା ବା କଷ୍ଟ କ'ଣ ଅଟେ?",
    },
}

_PROHIBITED_DIAGNOSTIC_PATTERNS = [
    r"\b(you have|diagnosed with|suffering from)\b.*\b(pneumonia|malaria|dengue|typhoid|sepsis|tuberculosis|infection)\b",
    r"\b(take|prescribe|prescribing)\b.*\b(paracetamol|antibiotic|ibuprofen|medicine|drug|tablet)\b",
    r"(आपको|तुम्हें).*(बीमारी है|रोग है|निमोनिया|मलेरिया|डेंगू|टाइफाइड)",
    r"(दवा|दवाई|गोली|पैरासिटामोल|एंटीबायोटिक).*(लें|खाएं|पीएं)",
    r"(ଆପଣଙ୍କୁ|ତୁମକୁ).*(ରୋଗ ହୋଇଛି|ନିମୋନିଆ|ମ୍ୟାଲେରିଆ|ଡେଙ୍ଗୁ|ଟାଇଫଏଡ)",
    r"(ଔଷଧ|ଦବା|ପାରାସିଟାମଲ).*(ଖାଆନ୍ତୁ|ନିଅନ୍ତୁ)",
]


def filter_non_diagnostic_speech(text: str, language: Literal["hi", "or"] = "or") -> tuple[bool, str]:
    """Scans text for prohibited diagnostic or prescribing assertions.
    Returns (is_violation, safe_text). If violation found, returns safe disclaimer.
    """
    import re

    lower = text.lower()
    for pat in _PROHIBITED_DIAGNOSTIC_PATTERNS:
        if re.search(pat, lower, re.IGNORECASE):
            disclaimer = (
                "ମୁଁ କୌଣସି ଔଷଧ କିମ୍ବା ରୋଗ ନିର୍ଣ୍ଣୟ ଦେଇପାରିବି ନାହିଁ। ଡାକ୍ତର ବାବୁ ଏହା ଯାଞ୍ଚ କରିବେ।"
                if language == "or"
                else "मैं कोई दवा या निदान नहीं बता सकता। डॉक्टर साहब इसकी जांच करेंगे।"
            )
            return True, disclaimer
    return False, text


def build_system_instruction(language: Literal["hi", "or"], missing_fields: list[dict[str, Any]]) -> str:
    """Builds the non-diagnostic duplex conversational prompt in Hindi or Odia."""
    fields_desc = []
    for f in missing_fields:
        label = f.get("label") or f.get("field_name")
        danger = " (URGENT DANGER SIGN)" if f.get("is_danger_sign") else ""
        fields_desc.append(f"- {f.get('field_name')}: {label}{danger}")
    fields_list_str = "\n".join(fields_desc) if fields_desc else "- General health status and symptoms"

    suggested_phrases = []
    for f in missing_fields:
        norm = normalize_field_name(f.get("field_name"))
        q_dict = VERNACULAR_QUESTIONS.get(norm)
        if q_dict and language in q_dict:
            suggested_phrases.append(f"- {norm}: {q_dict[language]}")
    suggested_block = ""
    if suggested_phrases:
        hdr = "Recommended natural phrasing:"
        suggested_block = f"\n\n{hdr}\n" + "\n".join(suggested_phrases)

    if language == "or":
        lang_prompt = (
            "ତୁମେ ଓଡ଼ିଆ ଭାଷାରେ କଥାବାର୍ତ୍ତା କରିବ। ଭାଷା ସରଳ, ସ୍ପଷ୍ଟ ଏବଂ ସହାନୁଭୂତିଶୀଳ ହେବା ଉଚିତ। "
            "ଗୋଟିଏ ଥରରେ କେବଳ ଗୋଟିଏ ପ୍ରଶ୍ନ ପଚାର। ରୋଗୀଙ୍କ ଉତ୍ତର ଶୁଣିବା ପରେ ପରବର୍ତ୍ତୀ ପ୍ରଶ୍ନ ପଚାର।"
        )
        non_diag_guard = (
            "କୌଣସି ପରିସ୍ଥିତିରେ ରୋଗ ନିର୍ଣ୍ଣୟ କର ନାହିଁ କିମ୍ବା ଔଷଧ ସୁପାରିଶ କର ନାହିଁ। "
            "ଯଦି ରୋଗୀ ପଚାରନ୍ତି କ'ଣ ଔଷଧ ଖାଇବେ, କୁହନ୍ତୁ: 'ମୁଁ କୌଣସି ଔଷଧ କିମ୍ବା ରୋଗ ନିର୍ଣ୍ଣୟ ଦେଇପାରିବି ନାହିଁ, ଡାକ୍ତର ବାବୁ ଏହା ଯାଞ୍ଚ କରିବେ।'"
        )
    else:
        lang_prompt = (
            "आप हिंदी भाषा में बात करेंगे। भाषा सरल, स्पष्ट और सहानुभूतिपूर्ण होनी चाहिए। "
            "एक बार में केवल एक ही प्रश्न पूछें। मरीज का उत्तर सुनने के बाद ही अगला प्रश्न पूछें।"
        )
        non_diag_guard = (
            "किसी भी परिस्थिति में बीमारी का निदान (diagnosis) न करें और न ही कोई दवा बताएं। "
            "यदि मरीज दवा या सलाह मांगे, तो कहें: 'मैं कोई दवा या निदान नहीं बता सकता। डॉक्टर साहब इसकी जांच करेंगे।'"
        )

    return f"""You are SEHAT AI's spoken conversational intake assistant in an Odisha rural healthcare clinic.
Your objective is to collect missing patient information through a warm, calm, two-way spoken conversation.

Language requirement:
{lang_prompt}

Safety and Clinical Boundaries (NON-NEGOTIABLE):
{non_diag_guard}

Missing information that must be collected from the patient:
{fields_list_str}{suggested_block}

Operational instructions:
1. Greet the patient warmly and explain you need to ask a few quick follow-up questions to help the doctor.
2. Ask about the missing items one by one. Prioritize danger signs and chief complaints.
3. As soon as the patient speaks an answer containing a measurement, duration, or symptom detail, immediately call the tool `submit_missing_field`.
4. Once all missing items have been addressed or if the patient indicates they want to stop, call `finish_intake` and thank them politely.
"""


def build_setup_message(model: str, language: Literal["hi", "or"], missing_fields: list[dict[str, Any]]) -> dict[str, Any]:
    """Builds the initial setup handshake payload for Gemini Multimodal Live."""
    system_text = build_system_instruction(language, missing_fields)
    voice_name = "Aoede" if language == "hi" else "Kore"

    return {
        "setup": {
            "model": f"models/{model}",
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {
                        "prebuiltVoiceConfig": {
                            "voiceName": voice_name,
                        }
                    }
                },
            },
            "systemInstruction": {
                "parts": [{"text": system_text}],
            },
            "tools": [
                {
                    "functionDeclarations": TOOL_DECLARATIONS,
                }
            ],
        }
    }


def build_realtime_audio_chunk(pcm_bytes: bytes) -> dict[str, Any]:
    """Wraps raw 16kHz PCM audio chunk into Gemini realtimeInput message."""
    return {
        "realtimeInput": {
            "mediaChunks": [
                {
                    "mimeType": "audio/pcm;rate=16000",
                    "data": base64.b64encode(pcm_bytes).decode("ascii"),
                }
            ]
        }
    }


def build_tool_response(call_id: str, function_name: str, output: dict[str, Any]) -> dict[str, Any]:
    """Wraps tool execution response back to Gemini Live."""
    return {
        "toolResponse": {
            "functionResponses": [
                {
                    "response": {"output": output},
                    "id": call_id,
                }
            ]
        }
    }


@dataclass
class GeminiLiveEvent:
    event_type: Literal["audio", "tool_call", "text", "turn_complete", "error"]
    audio_data: bytes | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_args: dict[str, Any] = field(default_factory=dict)
    text: str | None = None
    error_message: str | None = None


def parse_gemini_message(raw_msg: str | bytes) -> list[GeminiLiveEvent]:
    """Parses incoming WebSocket message from Gemini Multimodal Live."""
    events: list[GeminiLiveEvent] = []
    try:
        data = json.loads(raw_msg)
    except Exception as exc:
        events.append(GeminiLiveEvent(event_type="error", error_message=f"Invalid JSON from Gemini: {exc}"))
        return events

    # 1. Check for server error
    if "error" in data:
        err = data["error"]
        msg = err.get("message") if isinstance(err, dict) else str(err)
        events.append(GeminiLiveEvent(event_type="error", error_message=msg))
        return events

    # 2. Check for tool calls
    tool_call = data.get("toolCall")
    if tool_call and "functionCalls" in tool_call:
        for fc in tool_call["functionCalls"]:
            events.append(
                GeminiLiveEvent(
                    event_type="tool_call",
                    tool_call_id=fc.get("id"),
                    tool_name=fc.get("name"),
                    tool_args=fc.get("args") or {},
                )
            )

    # 3. Check for model turn content (Audio & Text)
    server_content = data.get("serverContent")
    if server_content:
        model_turn = server_content.get("modelTurn")
        if model_turn and "parts" in model_turn:
            for part in model_turn["parts"]:
                # Audio part
                inline_data = part.get("inlineData")
                if inline_data and "data" in inline_data:
                    audio_b64 = inline_data["data"]
                    try:
                        pcm_bytes = base64.b64decode(audio_b64)
                        events.append(GeminiLiveEvent(event_type="audio", audio_data=pcm_bytes))
                    except Exception as e:
                        logger.warning("Failed to decode audio part: %s", e)

                # Text part
                text = part.get("text")
                if text:
                    events.append(GeminiLiveEvent(event_type="text", text=text))

        if server_content.get("turnComplete"):
            events.append(GeminiLiveEvent(event_type="turn_complete"))

    return events


class GeminiLiveSession:
    """Manages an active bidirectional duplex session with Gemini Multimodal Live."""

    def __init__(self, api_key: str, model: str = "gemini-2.0-flash-exp", test_transport: Any = None):
        self.api_key = api_key
        self.model = model
        self.test_transport = test_transport
        self._ws: Any = None

    async def connect(self, language: Literal["hi", "or"], missing_fields: list[dict[str, Any]]) -> None:
        setup_payload = build_setup_message(self.model, language, missing_fields)
        if self.test_transport is not None:
            self._ws = self.test_transport
            await self._ws.send(json.dumps(setup_payload))
            return

        import websockets

        url = f"{LIVE_API_ENDPOINT}?key={self.api_key}"
        self._ws = await websockets.connect(url)
        await self._ws.send(json.dumps(setup_payload))

    async def send_audio(self, pcm_bytes: bytes) -> None:
        if self._ws is not None:
            payload = build_realtime_audio_chunk(pcm_bytes)
            await self._ws.send(json.dumps(payload))

    async def send_tool_result(self, call_id: str, function_name: str, output: dict[str, Any]) -> None:
        if self._ws is not None:
            payload = build_tool_response(call_id, function_name, output)
            await self._ws.send(json.dumps(payload))

    async def receive_events(self) -> AsyncIterator[GeminiLiveEvent]:
        if self._ws is None:
            return
        async for msg in self._ws:
            for ev in parse_gemini_message(msg):
                yield ev

    async def close(self) -> None:
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
