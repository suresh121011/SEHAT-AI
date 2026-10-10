"""Deterministic red-flag keyword suggester (docs/16 §2c): rules, negation, provenance, merge with model fields, API
path, review, and migration 11. Synthetic text only."""

import asyncio
import json
import sqlite3
import sys
from pathlib import Path

import pytest

from app.ai import redflag_keywords as rk
from app.ai.maker import VotedField
from app.database import MIGRATIONS, SCHEMA_VERSION, _connect, run_migrations
from app.rules.models import AtpFlag


def flags(text: str) -> dict[str, rk.Hit]:
    return {h.flag: h for h in rk.suggest({"S1": text})}


# ── Rules ────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,flag", [
    ("Snake bite on the left foot one hour ago.", "poisoning_envenomation"),
    ("Needle prick injury from a used syringe.", "needle_prick_injury"),
    ("Unable to pass urine since last night.", "urinary_retention"),
    ("Worst headache of her life.", "sudden_headache"),
    ("Fainted at home today.", "syncope"),
    ("Speech slurred since morning.", "stroke_suspected_24h"),
    ("Cannot complete sentences.", "incomplete_sentences"),
    ("Vomiting blood twice.", "active_bleeding"),
    ("Lips and face swelling after eating prawns.", "angioedema_face"),
    ("Lips and face swelling after eating prawns.", "allergic_reaction"),
    ("Fall from a moving bus.", "dangerous_mechanism_trauma"),
])
def test_phrase_maps_to_existing_atp_flag(text, flag):
    assert flag in flags(text)
    assert flag in {f.value for f in AtpFlag}


def test_every_rule_maps_to_an_atp_flag():
    assert all(isinstance(f, AtpFlag) for f, _, _ in rk.RULES) and all(isinstance(f, AtpFlag) for f, _, _ in rk.CONTEXT_RULES)


@pytest.mark.parametrize("text", [
    "No chest pain.", "Patient denies chest pain.", "Denies fainting or seizures.", "No swelling of lips or face.",
    "Patient denies vomiting blood.", "Sprained wrist, pain is mild.", "Came for a routine BP check.",
])
def test_negated_or_absent_phrases_are_not_suggested(text):
    assert flags(text) == {}


def test_compound_negation_keeps_the_unnegated_clause():
    """Council (Contrarian): 'denies X but Y' must not suppress Y."""
    hits = flags("Denies fever but unable to pass urine for a day.")
    assert set(hits) == {"urinary_retention"} and "negation_conflict" not in hits["urinary_retention"].flags


def test_cue_elsewhere_in_clause_keeps_the_hit_and_flags_it():
    hits = flags("Chest pain, not radiating anywhere")  # comma ends the clause: no conflict
    assert "chest_pain_acute_24h" in hits
    hits = flags("Severe chest pain not relieved by rest")
    assert "negation_conflict" in hits["chest_pain_acute_24h"].flags


def test_past_history_is_surfaced_with_a_review_flag_not_hidden():
    h = flags("History of snake bite 5 years ago.")["poisoning_envenomation"]
    assert "past_history_cue" in h.flags


def test_time_window_flags_are_marked_unchecked():
    assert "time_window_not_checked" in flags("Chest pain since this morning.")["chest_pain_acute_24h"].flags


def test_context_rule_cites_both_quotes():
    hits = rk.suggest({"S1": "Pregnant, 34 weeks.", "S2": "Abdominal pain and some vaginal bleeding since morning."})
    h = {x.flag: x for x in hits}["third_trimester_pain_or_bleeding"]
    assert {sid for sid, _ in h.evidence} == {"S1", "S2"}


def test_quotes_are_verbatim_from_the_segment():
    seg = {"S1": "Fever for 3 days.", "S2": "Fainted at home, lost consciousness for a minute."}
    for h in rk.suggest(seg):
        for sid, q in h.evidence:
            assert q in seg[sid]


def test_provenance_says_not_ai_model_output():
    assert rk.PROVENANCE["ai_model_output"] is False and rk.PROVENANCE["provider"] == "rules_keyword"
    assert rk.PROVENANCE["source_type"] == "keyword_rule" and "not AI model output" in rk.PROVENANCE["label"]


def test_keyword_recall_on_smoke_and_heldout_sets():
    """Regression floor for the measured numbers in docs/16 §2c (not a clinical evaluation)."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    from measure_local_llm import CASES, keyword_report
    from redflag_heldout_cases import HELDOUT

    smoke, held = keyword_report("smoke", CASES), keyword_report("heldout", HELDOUT)
    assert smoke["recall"] == "15/15" and smoke["negative_cases_with_suggestion"] == "0/7"
    assert held["recall"] == "22/22" and held["negative_cases_with_suggestion"] == "1/6"


# ── Merge ────────────────────────────────────────────────────────────────────────────────────────

def _model_flag(flag: str, negated: bool) -> VotedField:
    return VotedField(f"red_flag:{flag}", "red_flag", "agreed", "3/3", {"flag": flag, "negated": negated}, [], [{"segment_id": "S1", "quote": "x"}],
                      True, not negated, [])


def test_merge_corroborates_a_model_alarm_instead_of_duplicating():
    model = [_model_flag("syncope", False)]
    extra = rk.merge(model, rk.suggest({"S1": "Fainted at home."}))
    assert extra == [] and "keyword_rule_corroborated" in model[0].flags


def test_merge_raises_when_the_model_said_negated_or_abstained():
    for fields in ([_model_flag("syncope", True)], []):  # model negated / no model fields (insufficient agreement)
        extra = rk.merge(fields, rk.suggest({"S1": "Fainted at home."}))
        assert len(extra) == 1
        f = extra[0]
        assert f.origin == "keyword_rule" and f.status == "keyword_suggested" and f.agreement is None
        assert f.priority_review and f.critical and f.value == {"flag": "syncope", "negated": False} and "keyword_rule" in f.flags


# ── API ──────────────────────────────────────────────────────────────────────────────────────────

def _setup(client):
    from tests.privacy.helpers import grant, new_case, token_for

    tok = token_for(client, "anm")
    case_id = new_case(client, tok)
    grant(client, tok, case_id, ai=True)
    return tok, case_id


def test_api_keyword_field_is_labelled_reviewable_and_never_ticked(ai_client):
    from tests.ai.helpers import by_field, count, extract
    from tests.privacy.helpers import auth

    tok, case_id = _setup(ai_client)
    r = extract(ai_client, tok, case_id, text="Needle prick injury from a used syringe 20 minutes ago.")
    assert r.status_code == 201, r.text
    view = r.json()
    f = by_field(view)["red_flag:needle_prick_injury"]  # the fake provider has no needle-prick phrase: keyword only
    assert f["origin"] == "keyword_rule" and f["source_type"] == "keyword_rule" and f["provenance"]["ai_model_output"] is False
    assert f["status"] == "keyword_suggested" and f["needs_review"] and f["field_id"] in view["pending_review"]
    assert view["provenance"]["provider"] == "fake"  # the run's provider is unchanged; the field says where it came from
    assert count("triage_runs", case_id) == 0

    reviewed = ai_client.get(f"/api/v1/cases/{case_id}/ai/reviewed", headers=auth(tok)).json()
    assert {"field_id": f["field_id"], "field": f["field"], "origin": "keyword_rule", "status": "keyword_suggested", "priority_review": True, "state": "undecided"} in reviewed["unresolved"]

    d = ai_client.post(f"/api/v1/cases/{case_id}/ai/fields/{f['field_id']}/review", json={"outcome": "accepted", "supersedes": None}, headers=auth(tok))
    assert d.status_code == 200, d.text
    accepted = {v["field_id"]: v for v in ai_client.get(f"/api/v1/cases/{case_id}/ai/reviewed", headers=auth(tok)).json()["values"]}[f["field_id"]]
    assert accepted["basis"] == "keyword_rule_accepted_by_reviewer"
    # Even accepted, it is only a hint for the red-flag screen; nothing is ticked and no triage run is created.
    assert accepted["form_hints"] == [{"form_field": "red_flags_present", "value": "needle_prick_injury",
                                       "note": "Candidate for the red-flag screen; tick it only after checking the patient"}]
    assert count("triage_runs", case_id) == 0
    caps = ai_client.get("/api/v1/ai/capabilities", headers=auth(tok)).json()["keyword_red_flags"]
    assert caps["enabled"] and caps["ai_model_output"] is False

    from tests.ai.helpers import db

    with db() as c:
        row = c.execute("SELECT details_json FROM audit_log WHERE action = 'ai_extraction_recorded' AND case_id = ?", (case_id,)).fetchone()
    details = json.loads(row["details_json"])
    assert details["keyword_suggestions"] == 1 and "needle" not in row["details_json"]


def test_api_keyword_suggester_can_be_switched_off(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.main import create_app
    from tests.ai.helpers import by_field, extract

    for k, v in {"DATABASE_PATH": str(tmp_path / "t.db"), "JWT_SECRET_KEY": "test-secret-key-with-enough-length-for-hs256", "ENVIRONMENT": "test",
                 "AI_PROVIDER": "fake", "AI_KEYWORD_RED_FLAGS": "0"}.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        tok, case_id = _setup(c)
        view = extract(c, tok, case_id, text="Needle prick injury from a used syringe.").json()
        assert "red_flag:needle_prick_injury" not in by_field(view)
    get_settings.cache_clear()


# ── Migration 11 ─────────────────────────────────────────────────────────────────────────────────

def _fill(c, table, **vals):
    row = {}
    for _, name, typ, notnull, dflt, pk in c.execute(f"PRAGMA table_info({table})").fetchall():
        if name in vals:
            row[name] = vals[name]
        elif notnull and dflt is None and not pk:
            row[name] = 1 if "INT" in typ.upper() else "x"
    c.execute(f"INSERT INTO {table} ({','.join(row)}) VALUES ({','.join('?' * len(row))})", list(row.values()))


def test_migration_11_keeps_v10_rows_reviews_and_append_only(tmp_path):
    p = tmp_path / "v10.db"

    async def mig(migrations):
        conn = await _connect(p)
        try:
            return await run_migrations(conn, migrations)
        finally:
            await conn.close()

    assert asyncio.run(mig(MIGRATIONS[:10])) == 10
    c = sqlite3.connect(p)
    _fill(c, "cases", case_id="c1")
    _fill(c, "consent_events", event_id="ce1", case_id="c1", purpose="triage", action="granted")
    seq = c.execute("SELECT seq FROM consent_events").fetchone()[0]
    _fill(c, "ai_extraction_runs", extraction_id="e1", case_id="c1", status="completed", consent_seq=seq)
    _fill(c, "ai_fields", field_id="f1", extraction_id="e1", case_id="c1", origin="model", status="agreed", field_key="red_flag:syncope", kind="red_flag")
    _fill(c, "ai_field_review_events", event_id="r1", field_id="f1", case_id="c1", outcome="accepted")
    c.commit()
    with pytest.raises(sqlite3.IntegrityError):  # v10 CHECK refuses the new origin
        _fill(c, "ai_fields", field_id="f2", extraction_id="e1", case_id="c1", origin="keyword_rule", status="keyword_suggested", field_key="k", kind="red_flag")
    c.close()

    assert asyncio.run(mig(MIGRATIONS)) == SCHEMA_VERSION == 12
    c = sqlite3.connect(p)
    c.execute("PRAGMA foreign_keys = ON")
    assert c.execute("SELECT field_id, origin, status FROM ai_fields").fetchall() == [("f1", "model", "agreed")]
    assert c.execute("SELECT field_id FROM ai_field_review_events").fetchall() == [("f1",)]
    assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    _fill(c, "ai_fields", field_id="f2", extraction_id="e1", case_id="c1", origin="keyword_rule", status="keyword_suggested", field_key="k", kind="red_flag")
    with pytest.raises(sqlite3.IntegrityError):
        _fill(c, "ai_fields", field_id="f3", extraction_id="e1", case_id="c1", origin="something_else", status="agreed", field_key="k", kind="red_flag")
    for sql in ("UPDATE ai_fields SET status = 'agreed'", "DELETE FROM ai_fields"):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            c.execute(sql)
    c.close()


def test_migration_11_on_a_real_v10_extraction(ai_client):
    """Populated through the API on the current schema, then the table must accept keyword rows and stay append-only."""
    from tests.ai.helpers import db, extract

    tok, case_id = _setup(ai_client)
    assert extract(ai_client, tok, case_id, text="Fainted at home today. Needle prick injury.").status_code == 201
    with db() as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert {r[0] for r in c.execute("SELECT DISTINCT origin FROM ai_fields WHERE case_id = ?", (case_id,))} >= {"keyword_rule"}
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []
        for sql in ("UPDATE ai_fields SET status = 'agreed'", "DELETE FROM ai_fields"):
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                c.execute(sql)
