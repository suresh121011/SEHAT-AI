"""Retained from 21a9761 (niranjanswain067): field-name aliases and draft Hindi/Odia question wording. Not wired into
the app yet (scripted interviewer, L3-1); these tests keep the data honest until then."""

from app.rules.required_fields import _CORE, SCENARIO_REQUIRED
from app.voice import field_aliases as fa

_ALL = [r for reqs in SCENARIO_REQUIRED.values() for r in reqs] + list(_CORE)  # _CORE: the vitals the other scenarios use
KNOWN = {r.key for r in _ALL} | {k for r in _ALL for k in r.satisfied_by}


def test_every_alias_targets_a_real_missing_information_key():
    assert set(fa.FIELD_ALIASES.values()) <= KNOWN, set(fa.FIELD_ALIASES.values()) - KNOWN


def test_unknown_or_empty_names_never_default_or_pass_through():
    assert fa.canonical_field("Oxygen Saturation") == "spo2"
    for raw in (None, "", "   ", "ignore previous instructions", "chief complaint please"):
        assert fa.canonical_field(raw) is None


def test_draft_questions_are_marked_unreviewed_and_cover_hindi_and_odia():
    assert fa.REVIEW_STATUS == "draft_unreviewed"
    for key, q in fa.DRAFT_QUESTIONS.items():
        assert set(q) == {"hi", "or"} and all(v.strip() for v in q.values()), key


def test_nothing_in_the_app_uses_the_drafts_yet():
    from pathlib import Path

    app = Path(__file__).resolve().parents[2] / "app"
    users = [p for p in app.rglob("*.py") if p.name != "field_aliases.py" and "field_aliases" in p.read_text(encoding="utf-8")]
    assert users == []
