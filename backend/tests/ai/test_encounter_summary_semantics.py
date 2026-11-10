"""Clinical-summary semantics (final remediation plan S1, S2, S3, S4). Synthetic data only; AI = fake provider.

A clinician acts on the written summary, so a symptom the patient denied must never read as present, a sentence must
never contradict the polarity of the facts it cites, and the text must not claim who spoke. Engineering tests only:
the wording is not clinically validated.
"""

import pytest

from app.ai import encounter_summary as es
from app.ai.fake_provider import FakeProvider
from tests.ai.helpers import extract
from tests.privacy.helpers import auth, grant, new_case, token_for

URL = "/api/v1/cases/{}/encounter-summary"


@pytest.fixture
def ai_case(ai_client):
    anm = token_for(ai_client, "anm")
    case_id = new_case(ai_client, anm)
    assert grant(ai_client, anm, case_id, ai=True).status_code == 200
    return ai_client, anm, case_id


def _summary(c, token, case_id):
    r = c.post(URL.format(case_id), headers=auth(token))
    assert r.status_code == 201, r.text
    return r.json()["summary"]


def _text(summary) -> str:
    return " ".join(s["text"] for s in summary["narrative"])


def _negated_across_clauses(out):
    """The model says "fever" is negated, quoting a two-clause span: grounding cannot confirm the negation, keeps the
    mention as present and flags `negation_conflict` (grounding.py _negation)."""
    for s in out["symptoms"]:
        if s["name"] == "fever":
            s["negated"] = True
            s["evidence"] = [{"segment_id": s["evidence"][0]["segment_id"], "quote": "Cough, no fever"}]
    return out


# ── S1: a flagged negation never becomes "reports: fever" ─────────────────────────────────────────────────────

def test_negation_conflict_symptom_is_withheld_from_the_text_not_written_as_present(ai_case):
    c, anm, case_id = ai_case
    c.app.state.ai_provider = FakeProvider(perturb={i: _negated_across_clauses for i in range(3)})
    assert extract(c, anm, case_id, text="Cough, no fever.").status_code == 201
    s = _summary(c, anm, case_id)
    fever = [f for f in s["facts"].values() if f["section"] == "symptoms" and f["label"].lower() == "fever"]
    assert fever and all("negation_conflict" in f["flags"] for f in fever), fever
    text = _text(s).lower()
    assert "fever" not in text, text  # neither "reports fever" nor "denies fever": a person must decide
    withheld = [w for w in s["withheld"] if set(w["fact_ids"]) & {f["fact_id"] for f in fever}]
    assert withheld and withheld[0]["reason"] == "negation_or_time_uncertain", s["withheld"]
    assert "cough" in text  # the unflagged symptom is still written


def test_a_reviewer_decision_releases_the_flagged_fact(ai_case):
    c, anm, case_id = ai_case
    c.app.state.ai_provider = FakeProvider(perturb={i: _negated_across_clauses for i in range(3)})
    view = extract(c, anm, case_id, text="Cough, no fever.").json()
    fld = next(f for f in view["fields"] if f["field"].startswith("symptom:fever"))
    r = c.post(f"/api/v1/cases/{case_id}/ai/fields/{fld['field_id']}/review",
               json={"outcome": "corrected", "corrected": {"negated": True}}, headers=auth(anm))
    assert r.status_code == 200, r.text
    text = _text(_summary(c, anm, case_id))
    assert "denied at intake: fever" in text.lower()
    assert "reported at intake: fever" not in text.lower()


# ── S4: a denied symptom renders as denied, never as present ──────────────────────────────────────────────────

def test_denied_symptom_is_written_as_denied_only(ai_case):
    c, anm, case_id = ai_case
    extract(c, anm, case_id, text="Cough for 2 days. No fever.")
    s = _summary(c, anm, case_id)
    lines = [x["text"] for x in s["narrative"] if x["section"] == "symptoms"]
    denied = [ln for ln in lines if "denied" in ln.lower()]
    present = [ln for ln in lines if ln.lower().startswith("reported at intake")]
    assert denied and "fever" in denied[0].lower(), lines
    assert all("fever" not in ln.lower() for ln in present), lines


# ── S2: neutral attribution ───────────────────────────────────────────────────────────────────────────────────

def test_summary_does_not_claim_the_patient_was_the_speaker(ai_case):
    c, anm, case_id = ai_case
    extract(c, anm, case_id, text="Cough for 2 days. No fever.")
    text = _text(_summary(c, anm, case_id))
    assert "Patient reports" not in text and "Patient denies" not in text
    assert "Reported at intake:" in text and "Denied at intake:" in text


# ── S3: the gate itself blocks a polarity mismatch and checks tags per fact ───────────────────────────────────

def _fact(fid, key, display, provenance="ai_accepted", flags=()):
    return es.Fact(fact_id=fid, section="symptoms", key=key, label=display, display=display, value={}, provenance=provenance,
                   trust=es.PROVENANCE[provenance]["trust"], sources=[{"type": "test"}], flags=list(flags))


def test_gate_blocks_a_reported_sentence_citing_a_denied_fact():
    facts = {"d": _fact("d", "symptom:fever:denied", "fever")}
    passed, blocked = es.gate([{"section": "symptoms", "fact_ids": ["d"], "text": "Reported at intake: fever."}], facts)
    assert passed == [] and blocked[0].reason == "polarity_mismatch"


def test_gate_blocks_a_denied_sentence_citing_a_present_fact():
    facts = {"p": _fact("p", "symptom:fever:present", "fever")}
    passed, blocked = es.gate([{"section": "symptoms", "fact_ids": ["p"], "text": "Denied at intake: fever."}], facts)
    assert passed == [] and blocked[0].reason == "polarity_mismatch"


def test_gate_blocks_a_fact_with_uncertain_negation_in_prose():
    facts = {"p": _fact("p", "symptom:fever:present", "fever", flags=["negation_conflict"])}
    passed, blocked = es.gate([{"section": "symptoms", "fact_ids": ["p"], "text": "Reported at intake: fever."}], facts)
    assert passed == [] and blocked[0].reason == "negation_or_time_uncertain"


def test_gate_requires_the_unchecked_tag_for_every_cited_fact():
    tag = es.PROVENANCE["ai_unchecked"]["tag"]
    assert tag, "ai_unchecked must carry a visible tag"
    facts = {"a": _fact("a", "symptom:cough:present", "cough", "ai_unchecked"), "b": _fact("b", "symptom:fever:present", "fever", "ai_unchecked")}
    one_tag = f"Reported at intake: cough{tag}, fever."
    passed, blocked = es.gate([{"section": "symptoms", "fact_ids": ["a", "b"], "text": one_tag}], facts)
    assert passed == [] and blocked[0].reason == "missing_unchecked_tag"
    both = f"Reported at intake: cough{tag}, fever{tag}."
    passed, blocked = es.gate([{"section": "symptoms", "fact_ids": ["a", "b"], "text": both}], facts)
    assert blocked == [] and len(passed) == 1
