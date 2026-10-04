"""Phase 7 intake UI checks over the browser's HTTP path: Next.js session cookie -> /api/backend proxy -> FastAPI.
NOT a browser test: it does not render pages or run client JavaScript (layout, focus and SVG interaction need a
manual walkthrough). Synthetic text only; shared demo accounts; never point it at real data.

    # backend (from backend/), scratch database, fake AI provider:
    ENVIRONMENT=development DATABASE_PATH=/tmp/sehat-ui.db AI_PROVIDER=fake ../.venv/bin/python -m uvicorn app.main:app --port 8000
    # frontend (from frontend/):  npx next dev -p 3000
    #   (Not `next start`: in production the session cookie is Secure, which browsers accept on http://localhost
    #    but this HTTP client does not send over plain http.)
    # then (from backend/):       ../.venv/bin/python scripts/e2e_intake_ui_check.py [http://localhost:3000]
"""

import re
import sys
import uuid

import httpx

FE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:3000"
results: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok))
    print(("PASS " if ok else "FAIL ") + name + (f" — {detail}" if detail else ""), flush=True)


def session(user: str, role: str) -> httpx.Client:
    c = httpx.Client(base_url=FE, timeout=120)
    r = c.post("/api/session", json={"username": user, "role": role})
    r.raise_for_status()
    return c


def main() -> int:
    anm = session("anm_demo", "anm")
    cid = anm.post("/api/backend/cases", json={"scenario": "opd", "facility_code": "PHC-KHURDA-01"}).json()["case_id"]
    check("1 case created via proxy (no name or ID collected)", bool(cid))

    for path in ["/intake", "/intake/consent", "/intake/voice", "/intake/body-map", "/intake/documents", "/intake/follow-up", "/intake/review"]:
        r = anm.get(f"{path}?case={cid}")
        check(f"2 page {path} serves", r.status_code == 200 and not re.search(r"Unhandled Runtime Error|Build Error|Module not found", r.text), str(r.status_code))

    early = anm.post(f"/api/backend/cases/{cid}/triage", json={"scenario": "opd", "pregnant": False, "vitals": {}, "red_flag_screen_completed": False, "red_flags_present": [], "suspected_infection": False})
    check("3 triage refused before consent", early.status_code == 403, early.json().get("error", {}).get("code", ""))

    v = anm.get("/api/backend/consent/notice?language=en").json()["version"]
    r = anm.post(f"/api/backend/cases/{cid}/consent", json={"decision": "grant", "include_ai_assist": True, "include_voice_cloud": False, "language": "en", "notice_version": v})
    check("4 consent recorded (triage + AI assistance; online speech not granted)", r.status_code == 200 and r.json()["consent"]["voice_cloud"] != "granted")

    ext = anm.post(f"/api/backend/cases/{cid}/ai/extractions", json={"idempotency_key": str(uuid.uuid4()), "intake_text": "Fever for 3 days. SpO2 91%. No chest pain.", "include_voice": True, "include_ocr_reviewed": True})
    e = ext.json()
    check("5 follow-up questions come from the backend, danger sign first", ext.status_code == 201 and e["follow_up_questions"] and e["follow_up_questions"][0]["is_danger_sign"], str([q["field_name"] for q in e.get("follow_up_questions", [])]))
    spo2 = next((f for f in e["fields"] if f["field"] == "spo2"), None)
    hints_before = anm.get(f"/api/backend/cases/{cid}/ai/reviewed").json()
    check("6 no form hint before a human accepts the AI value", not any(x["form_hints"] for x in hints_before["values"] if x["field"] == "spo2"))
    anm.post(f"/api/backend/cases/{cid}/ai/fields/{spo2['field_id']}/review", json={"outcome": "accepted", "supersedes": None})
    hints = anm.get(f"/api/backend/cases/{cid}/ai/reviewed").json()
    check("7 accepted AI value becomes a form hint (still not submitted)", any(h["form_field"] == "vitals.spo2" for x in hints["values"] for h in x["form_hints"]))
    check("8 red flags never become automatic hints for the form", not any(h["form_field"] == "red_flags_present" and x["field"] == "red_flag:chest_pain_acute_24h" for x in hints["values"] for h in x["form_hints"]))

    bad = anm.post(f"/api/backend/cases/{cid}/triage", json={"scenario": "opd", "pregnant": False, "vitals": {"spo2": "91"}, "red_flag_screen_completed": True, "red_flags_present": [], "suspected_infection": False})
    err = bad.json().get("error", {})
    check("9 strict types: a string vital is rejected with a field path the form maps back", bad.status_code == 400 and err.get("details", {}).get("errors", [{}])[0].get("field") == "vitals.spo2")

    payload = {"scenario": "opd", "pregnant": False, "age_years": 34, "vitals": {"spo2": 91, "pulse": 112, "sbp": 150, "dbp": 90, "resp_rate": 24, "temp_c": 39.04, "on_supplemental_oxygen": False, "consciousness": "A"},
               "red_flag_screen_completed": True, "red_flags_present": [], "suspected_infection": False}
    patient = session("patient_demo", "patient")
    check("10 patient account cannot submit triage", patient.post(f"/api/backend/cases/{cid}/triage", json=payload).status_code in (403, 404))
    t = anm.post(f"/api/backend/cases/{cid}/triage", json=payload)
    res = t.json().get("result", {})
    check("11 health worker submits triage; urgency comes from the rules", t.status_code == 200 and res.get("urgency") in ("RED", "YELLOW", "GREEN") and "sign off" in res.get("disclaimer", ""), f"{res.get('urgency')} {[r['rule_id'] for r in res.get('triggered_rules', [])]}")
    cf = anm.get(f"/api/backend/cases/{cid}/triage/runs/{t.json()['run_id']}/counterfactuals")
    check("12 counterfactuals load for the recorded run", cf.status_code == 200 and cf.json()["status"] == "computed")

    w = anm.post(f"/api/backend/cases/{cid}/consent/withdraw", json={"purpose": "ai_assist"})
    after = anm.get(f"/api/backend/cases/{cid}/ai/reviewed")
    check("13 after withdrawing AI assistance, AI values are no longer served", w.status_code == 200 and after.status_code == 403)

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
