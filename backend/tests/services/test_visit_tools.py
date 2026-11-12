"""Read-only visit tools for the clinical assistant (docs/plans/semantic-kernel-orchestration-plan.md step 2; K1).

Six Semantic Kernel functions over code that already exists. They are bound to one visit by the server when they are
created; none takes a parameter, so a model can never name another visit. Each call checks access (creator ANM or
medical officer, facility scope) and consent (triage, and AI assistance, because the output is meant for a model).
They write nothing. Their output carries no visit id, no source ids and only what the summary may state in prose.
Synthetic data only; fake provider; no model is called here."""

import ast
import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from app.ai.question_bank import QUESTIONS
from app.auth import Principal, Role, demo_user_id
from app.config import get_settings
from app.database import _connect
from app.errors import ApiError
from app.privacy.pii import residual_hit
from app.services import visit_tools as vt
from tests.ai.conftest import ai_client  # noqa: F401  (fixture)
from tests.ai.helpers import extract
from tests.interview.test_interview import answer
from tests.privacy.helpers import auth, grant, new_case, token_for, triage, withdraw

APP = Path(__file__).resolve().parents[2] / "app"
MO = Principal(user_id=demo_user_id("mo_demo"), username="mo_demo", role=Role.MEDICAL_OFFICER)
ANM = Principal(user_id=demo_user_id("anm_demo"), username="anm_demo", role=Role.ANM)
TOOLS = ("get_visit_facts", "list_missing_information", "list_conflicts", "list_waiting_for_check", "get_urgency_record",
         "suggest_follow_up_questions", "list_changes_since_last_summary")


async def _call(principal, case_id, name):
    conn = await _connect(get_settings().database_path)
    try:
        return json.loads(await getattr(vt.VisitTools(conn, principal, case_id), name)())
    finally:
        await conn.close()


def call(principal, case_id, name):
    return asyncio.run(_call(principal, case_id, name))


@pytest.fixture
def visit(ai_client):
    c = ai_client
    anm = token_for(c, "anm")
    case_id = new_case(c, anm)
    assert grant(c, anm, case_id, ai=True).status_code == 200
    assert extract(c, anm, case_id, "I am suffering from fever since 2 days and my head is paining since 2 hours. No vomiting.").status_code == 201
    assert triage(c, anm, case_id, vitals={"resp_rate": 30}).status_code == 200  # fires a rule, so there is a reason to quote
    return c, anm, case_id


def _counts():
    with sqlite3.connect(get_settings().database_path) as db:
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        return {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}  # noqa: S608 (table names from the schema)


# ── shape: six tools, none takes a parameter ───────────────────────────────────────────────────────────────────────


def test_seven_tools_are_registered_on_the_kernel_and_none_takes_a_parameter():
    from app.services.kernel import build_kernel

    kernel = build_kernel(get_settings())
    vt.register(kernel, conn=None, principal=MO, case_id="not-used-until-called")
    fns = kernel.plugins[vt.PLUGIN_NAME].functions
    assert set(fns) == set(TOOLS)
    for name, fn in fns.items():
        assert fn.metadata.parameters == [], name  # the model cannot pass a visit id, or anything else
        assert fn.metadata.description, name


# ── content ────────────────────────────────────────────────────────────────────────────────────────────────────────


def test_the_tools_answer_from_this_visits_record(visit):
    c, anm, case_id = visit
    facts = call(MO, case_id, "get_visit_facts")["facts"]
    by_value = {f["value"]: f for f in facts}
    assert by_value["fever"]["check"] == "not yet checked" and by_value["fever"]["section"] == "symptoms"
    assert "Fever for 2 days" in {f["value"] for f in facts if f["section"] == "timeline"} or by_value.get("2 days")
    assert all(set(f) - {"tag"} == {"id", "section", "label", "value", "check", "flags"} for f in facts)
    # an unchecked value carries the tag a sentence must repeat after it (docs/24 §3); a checked one has none
    assert all(("tag" in f) == (f["check"] == "not yet checked") for f in facts) and by_value["fever"]["tag"] == " [not yet checked]"

    urgency = call(MO, case_id, "get_urgency_record")
    assert urgency["urgency"] in ("RED", "YELLOW", "GREEN") and urgency["source"] == "rules engine"
    assert urgency["reasons"] and all({"rule", "urgency", "reason"} <= set(r) for r in urgency["reasons"])
    assert "never" in urgency["note"].lower()

    missing = call(MO, case_id, "list_missing_information")["missing"]
    assert missing and all({"item", "why", "danger_sign"} <= set(m) for m in missing)

    waiting = call(MO, case_id, "list_waiting_for_check")["waiting"]
    assert {"symptom: fever", "symptom: headache"} <= {w["item"] for w in waiting}

    questions = call(MO, case_id, "suggest_follow_up_questions")["questions"]
    bank = {q["question_text"] for q in QUESTIONS.values()}
    assert questions and all(q["question"] in bank for q in questions)  # only the fixed bank, never new wording


def test_conflicts_are_named_without_their_values(ai_client):
    c = ai_client
    anm = token_for(c, "anm")
    case_id = new_case(c, anm)
    assert grant(c, anm, case_id, ai=True).status_code == 200
    assert extract(c, anm, case_id, "Fever since 2 days.").status_code == 201
    assert extract(c, anm, case_id, "Fever since 5 days.").status_code == 201
    out = call(MO, case_id, "list_conflicts")
    assert [x["item"] for x in out["conflicts"]] == ["Duration of fever"] and out["conflicts"][0]["sources"] == 2
    assert "2 days" not in json.dumps(out) and "5 days" not in json.dumps(out)  # a model is never handed a value to pick


def test_facts_hold_only_what_the_summary_may_state_in_prose(visit):
    c, anm, case_id = visit
    assert answer(c, anm, case_id, "chief_complaint", text="dry cough at night").status_code == 201  # interview: table only
    facts = call(MO, case_id, "get_visit_facts")
    assert "dry cough" not in json.dumps(facts)
    assert facts["withheld"] >= 1 and "table" in facts["note"].lower()


# ── safety: no writes, no identifiers, access and consent ──────────────────────────────────────────────────────────


def test_no_tool_writes_anything(visit):
    c, anm, case_id = visit
    before = _counts()
    for name in TOOLS:
        call(MO, case_id, name)
        call(ANM, case_id, name)
    assert _counts() == before


def test_output_carries_no_visit_id_no_source_ids_and_no_identifier_patterns(visit):
    c, anm, case_id = visit
    with sqlite3.connect(get_settings().database_path) as db:
        token = db.execute("SELECT patient_token FROM cases WHERE case_id = ?", (case_id,)).fetchone()[0]
        ids = [r[0] for r in db.execute("SELECT field_id FROM ai_fields WHERE case_id = ?", (case_id,))]
    for name in TOOLS:
        out = call(MO, case_id, name)
        text = json.dumps(out)
        assert case_id not in text and token not in text and not any(i in text for i in ids), name
        # The readable text, without the opaque fact ids a model needs for citing (hash strings, which the
        # identifier check would flag as digit runs), must pass the same identifier check as model output.
        readable = json.dumps({k: [{kk: vv for kk, vv in x.items() if kk != "id"} for x in v] if isinstance(v, list) and v and isinstance(v[0], dict) else v
                               for k, v in out.items()})
        assert not residual_hit(readable), name
        assert all(f["id"].startswith("F") and len(f["id"]) == 13 for f in out.get("facts", [])), name


@pytest.mark.parametrize("who", [
    Principal(user_id=demo_user_id("patient_demo"), username="patient_demo", role=Role.PATIENT),
    Principal(user_id=demo_user_id("supervisor_demo"), username="supervisor_demo", role=Role.SUPERVISOR),
    Principal(user_id="anm_other", username="anm_other", role=Role.ANM),
    Principal(user_id=demo_user_id("mo_demo"), username="mo_demo", role=Role.MEDICAL_OFFICER, facilities=frozenset({"PHC-PURI-02"})),
])
def test_only_the_visits_reviewers_can_use_the_tools(visit, who):
    c, anm, case_id = visit
    for name in TOOLS:
        with pytest.raises(ApiError) as exc:
            call(who, case_id, name)
        assert exc.value.status_code == 404, name  # looks exactly like a missing visit


def test_without_ai_assistance_consent_the_tools_refuse(ai_client):
    c = ai_client
    anm = token_for(c, "anm")
    case_id = new_case(c, anm)
    assert grant(c, anm, case_id, ai=False).status_code == 200
    with pytest.raises(ApiError) as exc:
        call(MO, case_id, "get_visit_facts")
    assert exc.value.status_code == 403


def test_after_triage_consent_is_withdrawn_the_tools_refuse(visit):
    c, anm, case_id = visit
    assert withdraw(c, anm, case_id, "triage").status_code == 200
    with pytest.raises(ApiError) as exc:
        call(MO, case_id, "get_urgency_record")
    assert exc.value.status_code == 403


# ── through Semantic Kernel, as a model would call them ────────────────────────────────────────────────────────────


def test_tools_called_at_the_same_time_all_answer(visit):
    # A model may ask for several tools in one turn, and Semantic Kernel runs them concurrently on the one connection.
    # Found on the real Azure deployment (2026-11-12): two of four parallel calls failed with "cannot start a
    # transaction within a transaction".
    c, anm, case_id = visit

    async def go():
        conn = await _connect(get_settings().database_path)
        try:
            tools = vt.VisitTools(conn, MO, case_id)
            return await asyncio.gather(*(getattr(tools, name)() for name in TOOLS * 2))
        finally:
            await conn.close()

    outs = asyncio.run(go())
    assert len(outs) == 2 * len(TOOLS) and all(isinstance(json.loads(o), dict) for o in outs)


def test_the_kernel_invokes_a_tool_for_the_bound_visit_only(visit):
    from app.services.kernel import build_kernel

    c, anm, case_id = visit

    async def go():
        conn = await _connect(get_settings().database_path)
        try:
            kernel = build_kernel(get_settings())
            vt.register(kernel, conn=conn, principal=MO, case_id=case_id)
            out = await kernel.invoke(kernel.get_function(vt.PLUGIN_NAME, "get_urgency_record"))
            return json.loads(str(out))
        finally:
            await conn.close()

    assert asyncio.run(go())["urgency"] in ("RED", "YELLOW", "GREEN")


# ── code rules ─────────────────────────────────────────────────────────────────────────────────────────────────────


def test_the_tools_module_has_no_write_path():
    src = (APP / "services" / "visit_tools.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    strings = " ".join(n.value.upper() for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str))
    assert not any(w in strings for w in ("INSERT ", "UPDATE ", "DELETE ", "REPLACE INTO", "DROP "))
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert not imported & {"app.case_triage", "app.review_queue", "app.routes.triage", "app.rules.engine"}
    assert "audit.record" not in src and ".invoke" not in src
