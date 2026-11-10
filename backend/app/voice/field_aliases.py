"""Field-name aliases and draft Hindi/Odia follow-up question wording (retained from 21a9761, niranjanswain067).

Kept from the removed Gemini Live follow-up (remediation plan §2, §5) as input for the planned scripted voice
interviewer (architecture L3-1). NOTHING in the app imports this module yet:
- `canonical_field` maps a spoken or written field-name synonym to a missing-information key. Unlike the original,
  an empty or unknown name maps to None (never to a default such as "chief_complaint", and never passed through).
- `DRAFT_QUESTIONS` are UNREVIEWED drafts (docs/13 entry IV-hi-or-draft): they must not be shown or spoken to a
  patient until a native speaker and a clinician review them. `REVIEW_STATUS` says so for code that reads them.
"""

REVIEW_STATUS = "draft_unreviewed"

FIELD_ALIASES: dict[str, str] = {
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


def canonical_field(raw_name: str | None) -> str | None:
    """The canonical key for a field-name synonym, or None when the name is empty or not a known synonym."""
    if not raw_name or not raw_name.strip():
        return None
    return FIELD_ALIASES.get(raw_name.strip().lower().replace(" ", "_").replace("-", "_"))


DRAFT_QUESTIONS: dict[str, dict[str, str]] = {
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
