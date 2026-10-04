"""Deterministic follow-up questions for missing information (docs/09 §6.5). English only: Hindi/Odia
wording needs native-speaker and clinical review first (docs/13), so those return `not_available_pending_review`.
Questions are prompts for the health worker to ask; answers are entered on the triage form by a human.
"""

YES_NO = ["yes", "no", "not sure"]

QUESTIONS: dict[str, dict] = {
    "red_flag_screen": {"question_text": "Has the red-flag screen been done? Any chest pain, difficulty breathing, fits, fainting, heavy bleeding, severe pain or confusion?",
                        "response_options": ["none present", "one or more present", "not checked yet"]},
    "danger_signs": {"question_text": "Any pregnancy danger signs: bleeding, severe headache or blurred vision, fits, high fever, leaking fluid, or reduced baby movements?",
                     "response_options": ["none present", "one or more present", "not checked yet"]},
    "chief_complaint": {"question_text": "What is the main problem today?", "response_options": []},
    "duration": {"question_text": "Since when has this problem been present?", "response_options": ["less than 1 day", "1-3 days", "4-7 days", "more than 1 week"]},
    "severity": {"question_text": "How bad is the pain or discomfort, from 0 (none) to 10 (worst)?", "response_options": [str(i) for i in range(11)]},
    "spo2": {"question_text": "Please measure and record SpO2 (%).", "response_options": []},
    "bp": {"question_text": "Please measure and record blood pressure (mmHg).", "response_options": []},
    "bp_reading_1": {"question_text": "Please measure and record a first blood pressure reading (mmHg).", "response_options": []},
    "bp_reading_2": {"question_text": "Please take a second blood pressure reading after a few minutes' rest (mmHg).", "response_options": []},
    "pulse": {"question_text": "Please measure and record the pulse (beats per minute).", "response_options": []},
    "resp_rate": {"question_text": "Please count and record the breathing rate (breaths per minute).", "response_options": []},
    "temp": {"question_text": "Please measure and record the temperature (°C).", "response_options": []},
    "lmp": {"question_text": "What was the first day of the last menstrual period?", "response_options": []},
    "edd": {"question_text": "What is the expected date of delivery, if known?", "response_options": []},
    "gravida_parity": {"question_text": "How many pregnancies, including this one, and how many births?", "response_options": []},
    "hb": {"question_text": "Is a recent haemoglobin (Hb) result available?", "response_options": YES_NO},
    "blood_sugar": {"question_text": "Is a recent blood sugar result available?", "response_options": YES_NO},
    "current_drugs": {"question_text": "Which medicines is the patient taking now?", "response_options": []},
    "adherence": {"question_text": "In the last week, were any doses missed?", "response_options": ["no doses missed", "some missed", "most missed", "not sure"]},
}

MAX_QUESTIONS = 5


def questions_for(missing) -> list[dict]:
    """`missing` is the ordered list from app.rules.required_fields.missing (danger signs first)."""
    out = []
    for r in missing[:MAX_QUESTIONS]:
        q = QUESTIONS.get(r.key)
        if q is None:
            continue
        out.append({"field_name": r.key, "label": r.label, **q, "is_danger_sign": r.danger_sign, "priority": r.priority, "language": "en",
                    "translations": {"hi": "not_available_pending_review", "or": "not_available_pending_review"}})
    return out
