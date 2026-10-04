"""Fake structured provider: a deterministic keyword/regex extractor (docs/16 §2). NOT an LLM.

It is the offline tier for development and demos while no model is available, and the test double for the
whole Phase 6 path. Because it is deterministic, every MAKER pass agrees with the others unless a test
(or the explicit `demo_disagreement` mode) perturbs a pass; voting is only exercised that way.

Modes:
- `honest`: extract what the keyword tables find, with verbatim quotes;
- `adversarial`: additionally invent a value with a fabricated quote, mark every red flag negated
  without a cue and suggest GREEN — used to prove grounding and raise-only reject it;
- `demo_disagreement`: pass 2 changes the first measurement it found (critical value → disputed);
- `perturb={pass_index: fn}`: tests replace a pass's output (dict → dict, or a raw string such as
  invalid JSON).
"""

import json
import re
from collections.abc import Callable

from app.ai.adapter import StructuredProvider

_I = re.IGNORECASE
_CLAUSE = re.compile(r"[.;!?\n]|,|\bbut\b", _I)
_NEG = re.compile(r"\b(?:no|not|never|without|denies|denied|deny|negative for|nil)\b", _I)

SYMPTOMS: list[tuple[str, str]] = [
    ("chest pain", r"chest pain|pain in (?:the )?chest"),
    ("abdominal pain", r"(?:abdominal|stomach|belly|abdomen) pain|pain in (?:the )?(?:abdomen|stomach|belly)"),
    ("headache", r"headache|head ache"),
    ("fever", r"fever|febrile"),
    ("cough", r"cough"),
    ("vomiting", r"vomit(?:ing|ed|s)?|throwing up"),
    ("diarrhoea", r"diarrh(?:o)?ea|loose (?:motions?|stools?)"),
    ("breathlessness", r"breathless(?:ness)?|shortness of breath|short of breath|difficulty (?:in )?breathing"),
    ("bleeding", r"bleeding"),
    ("rash", r"rash"),
    ("dizziness", r"dizz(?:y|iness)|giddiness"),
    ("body ache", r"body ?aches?|myalgia"),
    ("joint pain", r"joint pains?"),
    ("seizure", r"seizures?|convulsions?|fits"),
    ("fainting", r"faint(?:ed|ing)?|syncope|passed out|lost consciousness"),
    ("weakness", r"weakness"),
    ("swelling", r"swelling|swollen"),
    ("burning urination", r"burning (?:urine|urination|micturition)"),
    ("reduced fetal movement", r"(?:reduced|less|decreased) (?:fetal|foetal|baby) movements?"),
]
# Mentions that map to an ATP red-flag row. A mention is a candidate for the health worker's red-flag
# screen, never a rule result (the rules engine only acts on flags a human enters).
RED_FLAGS: list[tuple[str, str]] = [
    ("chest_pain_acute_24h", r"chest pain|pain in (?:the )?chest"),
    ("active_seizure", r"seizures?|convulsions?|fits"),
    ("syncope", r"faint(?:ed|ing)?|syncope|passed out|lost consciousness"),
    ("sob_acute_12h", r"breathless(?:ness)?|shortness of breath|short of breath|difficulty (?:in )?breathing"),
    ("active_bleeding", r"heavy bleeding|bleeding heavily|profuse bleeding|active bleeding"),
    ("severe_pain", r"severe (?:\w+ )?pain"),
    ("sudden_headache", r"sudden (?:severe )?headache|worst headache"),
    ("sudden_abdominal_pain", r"sudden (?:severe )?(?:abdominal|stomach) pain"),
    ("poisoning_envenomation", r"poison(?:ing)?|snake ?bite|insecticide|pesticide"),
    ("allergic_reaction", r"allergic reaction|anaphylaxis"),
    ("audible_wheeze", r"wheez(?:e|ing)"),
    ("stridor", r"stridor"),
    ("urinary_retention", r"(?:cannot|can't|unable to) pass urine|urinary retention"),
]
_V = r"\s*(?:is|was|of|:|=|at)?\s*"
MEASUREMENTS: list[tuple[str, str]] = [
    ("bp", rf"\b(?:bp|blood pressure){_V}(\d{{2,3}})\s*(?:/|over)\s*(\d{{2,3}})(\s*mm ?hg)?"),
    ("temp", rf"\b(?:temp(?:erature)?|fever(?: of)?){_V}(\d{{2,3}}(?:\.\d+)?)\s*(°\s*[cf]\b|degrees?(?:\s*[cf]\b)?|[cf]\b)?"),
    ("spo2", rf"\b(?:spo2|sp02|oxygen saturation|o2 sat(?:uration)?|saturation|sats?){_V}(\d{{2,3}})\s*(%)?"),
    ("pulse", rf"\b(?:pulse(?: rate)?|heart rate|hr){_V}(\d{{2,3}})\s*(bpm|/min)?"),
    ("resp_rate", rf"\b(?:resp(?:iratory)? rate|rr|breathing rate){_V}(\d{{1,2}})\s*(/min|breaths)?"),
    ("hb", rf"\b(?:hb|ha?emoglobin){_V}(\d{{1,2}}(?:\.\d+)?)\s*(g/dl)?"),
    ("platelets", rf"\bplatelets?(?: count)?{_V}(\d[\d,]*(?:\.\d+)?)\s*(lakhs?|k\b|/ul|/µl|/cumm)?"),
    ("creatinine", rf"\bcreatinine{_V}(\d{{1,2}}(?:\.\d+)?)\s*(mg/dl)?"),
    ("troponin", rf"\btroponin(?: [it])?{_V}(\d+(?:\.\d+)?)\s*(ng/ml|ng/l)?"),
    ("glucose", rf"\b(?:glucose|blood sugar|sugar|rbs|fbs){_V}(\d{{2,3}})\s*(mg/dl)?"),
    ("pain_severity", r"\bpain(?: score| severity)?\s*(?:is|of|:)?\s*(\d{1,2})\s*(?:/|out of)\s*10"),
]
DURATION = re.compile(r"\b(?:for|since|past|last)\s+(?:the\s+)?((?:\d+|one|two|three|four|five|six|seven|ten|a|an)\s+(?:hours?|days?|weeks?|months?))\b", _I)
ONSET = re.compile(r"\b(sudden(?:ly)?|gradual(?:ly)?)\b", _I)
MEDICATION = re.compile(
    r"\b(?:taking|takes|took|on|given|started)\s+(paracetamol|ibuprofen|metformin|amlodipine|aspirin|insulin|iron|folic acid|ors|atenolol|"
    r"telmisartan|losartan|glimepiride|amoxicillin|azithromycin|cetirizine|pantoprazole|omeprazole)\b"
    r"(?:\s+(\d+(?:\.\d+)?\s*(?:mg|ml|units?)))?(?:\s+(once daily|twice daily|thrice daily|daily|od|bd|tds|at night))?",
    _I,
)


def _clause_start(text: str, pos: int) -> int:
    start = 0
    for m in _CLAUSE.finditer(text, 0, pos):
        start = m.end()
    return start


def _mention(text: str, m: re.Match) -> tuple[bool, int]:
    """(negated, quote_start): a negation cue earlier in the same clause negates; the quote then starts at
    the cue so the evidence itself shows the negation."""
    lo = _clause_start(text, m.start())
    cues = list(_NEG.finditer(text, lo, m.start()))
    if cues:
        return True, cues[-1].start()
    return False, m.start()


def _num(s: str) -> float:
    v = float(s.replace(",", ""))
    return int(v) if v.is_integer() else v


def extract(segments: tuple[tuple[str, str], ...]) -> dict:
    out = {"chief_complaint": None, "onset": None, "duration": None, "symptoms": [], "measurements": [], "medications": [], "red_flags": [], "urgency_suggestion": None}
    seen_sym: set[str] = set()
    seen_flag: set[str] = set()
    for sid, text in segments:
        matches = sorted((m for _, pattern in SYMPTOMS for m in re.finditer(rf"\b(?:{pattern})\b", text, _I)), key=lambda m: m.start())
        for m in matches:  # in text order, so the chief complaint is the first symptom mentioned
            name = m.group(0).lower()
            if name in seen_sym:
                continue
            seen_sym.add(name)
            negated, qs = _mention(text, m)
            ev = [{"segment_id": sid, "quote": text[qs:m.end()]}]
            out["symptoms"].append({"name": name, "negated": negated, "evidence": ev})
            if out["chief_complaint"] is None and not negated:
                out["chief_complaint"] = {"value": name, "evidence": ev}
        for flag, pattern in RED_FLAGS:
            for m in re.finditer(rf"\b(?:{pattern})\b", text, _I):
                if flag in seen_flag:
                    break
                seen_flag.add(flag)
                negated, qs = _mention(text, m)
                out["red_flags"].append({"flag": flag, "negated": negated, "evidence": [{"segment_id": sid, "quote": text[qs:m.end()]}]})
        for name, pattern in MEASUREMENTS:
            for m in re.finditer(pattern, text, _I):
                if _mention(text, m)[0]:
                    continue
                unit = None
                if name == "bp":
                    value, value2, unit = _num(m.group(1)), _num(m.group(2)), "mmHg"
                else:
                    value, value2 = _num(m.group(1)), None
                    if m.lastindex and m.lastindex >= 2 and m.group(2):
                        unit = re.sub(r"\s+", "", m.group(2)).replace("°", "").replace("degrees", "").replace("degree", "").upper() or None
                        unit = {"C": "C", "F": "F", "%": "%"}.get(unit, m.group(2).strip())
                out["measurements"].append({"name": name, "value": value, "value2": value2, "unit": unit, "evidence": [{"segment_id": sid, "quote": m.group(0).strip()}]})
        if out["duration"] is None and (m := DURATION.search(text)):
            out["duration"] = {"value": m.group(1), "evidence": [{"segment_id": sid, "quote": m.group(0)}]}
        if out["onset"] is None and (m := ONSET.search(text)):
            out["onset"] = {"value": m.group(1).lower(), "evidence": [{"segment_id": sid, "quote": m.group(0)}]}
        for m in MEDICATION.finditer(text):
            if _mention(text, m)[0]:
                continue
            out["medications"].append({"name": m.group(1).lower(), "dose": m.group(2), "frequency": m.group(3), "evidence": [{"segment_id": sid, "quote": m.group(0)}]})
    alarms = [r for r in out["red_flags"] if not r["negated"]]
    if alarms:
        out["urgency_suggestion"] = {"level": "RED", "evidence": alarms[0]["evidence"]}
    return out


Perturb = Callable[[dict], "dict | str"]


class FakeProvider(StructuredProvider):
    name = "fake"
    kind = "deterministic keyword extractor (not an LLM)"
    model_id = "fake-keyword-extractor-2026-10-03.1"
    cloud = False

    def __init__(self, mode: str = "honest", perturb: dict[int, Perturb] | None = None):
        if mode not in ("honest", "adversarial", "demo_disagreement"):
            raise ValueError("unknown fake provider mode")
        self.mode = mode
        self.perturb = perturb or {}
        self.calls: list[dict] = []  # for tests: what each pass received (redacted segments only)

    def describe(self) -> dict:
        return {**super().describe(), "mode": self.mode}

    async def _generate(self, segments, *, temperature: float, pass_index: int) -> str:
        self.calls.append({"pass_index": pass_index, "temperature": temperature, "segments": segments})
        out = extract(segments)
        if self.mode == "adversarial":
            first = segments[0][0] if segments else "S1"
            out["measurements"].append({"name": "spo2", "value": 99, "value2": None, "unit": "%", "evidence": [{"segment_id": first, "quote": "SpO2 99% on room air"}]})
            for r in out["red_flags"]:
                r["negated"] = True
            out["urgency_suggestion"] = {"level": "GREEN", "evidence": [{"segment_id": first, "quote": "patient is completely fine"}]}
        if self.mode == "demo_disagreement" and pass_index == 1 and out["measurements"]:
            m = out["measurements"][0]
            m["value"] = m["value"] + 1  # the quote no longer supports it: grounding drops this pass's value
        fn = self.perturb.get(pass_index)
        if fn is not None:
            result = fn(out)
            return result if isinstance(result, str) else json.dumps(result)
        return json.dumps(out)
