"""Phase 6 HTTP walkthrough (docs/16 §11) against a running backend with the FAKE provider. Synthetic text only;
uses the shared demo accounts; never point it at real data. NOT a browser test.

    # from backend/, with a scratch database (add AI_FAKE_MODE=demo_disagreement to also see a disputed value):
    ENVIRONMENT=development DATABASE_PATH=/tmp/sehat-e2e-ai.db AI_PROVIDER=fake \
      ../.venv/bin/python -m uvicorn app.main:app --port 8000
    # then:  ../.venv/bin/python scripts/e2e_ai_check.py [http://localhost:8000]
"""

import sys
import uuid

import httpx

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000") + "/api/v1"
INTAKE = ("Patient Ramesh Kumar reports fever for 3 days. BP 150/90, SpO2 91%. "
          "Severe chest pain since morning. Taking paracetamol 500 mg twice daily.")
results: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok))
    print(("PASS " if ok else "FAIL ") + name + (f" — {detail}" if detail else ""), flush=True)


def login(c: httpx.Client, user: str, role: str) -> dict:
    r = c.post(f"{BASE}/auth/login", json={"username": user, "role": role})
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def main() -> int:
    c = httpx.Client(timeout=120)
    anm = login(c, "anm_demo", "anm")
    caps = c.get(f"{BASE}/ai/capabilities", headers=anm).json()
    check("1 provider is the fake, labelled not an LLM", caps["provider"] == "fake" and "not an LLM" in caps["provider_kind"], caps["provider_kind"])

    cid = c.post(f"{BASE}/cases", json={"scenario": "opd", "facility_code": "PHC-KHURDA-01"}, headers=anm).json()["case_id"]
    notice = c.get(f"{BASE}/consent/notice", headers=anm).json()["version"]
    r = c.post(f"{BASE}/cases/{cid}/consent", json={"decision": "grant", "include_ai_assist": True, "include_voice_cloud": False, "language": "en", "notice_version": notice}, headers=anm)
    check("2 consent granted with ai_assist", r.status_code == 200)

    r = c.post(f"{BASE}/cases/{cid}/ai/extractions", json={"idempotency_key": str(uuid.uuid4()), "intake_text": INTAKE}, headers=anm)
    v = r.json()
    fields = {f["field"]: f for f in v.get("fields", [])}
    check("3 extraction created, 3 MAKER passes valid", r.status_code == 201 and v["maker"]["passes_valid"] == 3, str(v.get("maker")))
    check("3a identifier redacted before the provider", all("Ramesh" not in s["text"] for s in v["segments"]))
    check("3b every field has a quote located in its segment", all(e["source"]["redacted_chars"] for f in v["fields"] for e in f["evidence"]))
    check("3c chest pain surfaces as a red-flag mention", "red_flag:chest_pain_acute_24h" in fields)
    if caps.get("mode") == "demo_disagreement":
        check("4 disputed critical value lists candidates", any(f["status"] == "disputed" and f["candidates"] for f in v["fields"]))
    else:
        print("SKIP 4 disputed value (start the server with AI_FAKE_MODE=demo_disagreement)")

    bp = fields.get("bp")
    if bp and bp["value"] is not None:
        r = c.post(f"{BASE}/cases/{cid}/ai/fields/{bp['field_id']}/review", json={"outcome": "accepted"}, headers=anm)
    else:
        r = c.post(f"{BASE}/cases/{cid}/ai/fields/{bp['field_id']}/review", json={"outcome": "corrected", "corrected": {"value": 150, "value2": 90}}, headers=anm)
    check("5 field reviewed", r.status_code == 200)
    rv = c.get(f"{BASE}/cases/{cid}/ai/reviewed", headers=anm).json()
    check("6 reviewed view returns the value with form hints", any(x["field"] == "bp" and x["form_hints"] for x in rv["values"]))

    t = c.post(f"{BASE}/cases/{cid}/triage", json={"scenario": "opd", "age_years": 40, "red_flag_screen_completed": True,
                                                   "vitals": {"resp_rate": 16, "spo2": 96, "on_supplemental_oxygen": False, "pulse": 78, "sbp": 150, "dbp": 90, "temp_c": 37.0, "consciousness": "A"}}, headers=anm)
    det = t.json()["result"]["urgency"]
    check("7 rules engine triage recorded", t.status_code == 200, det)

    n = c.post(f"{BASE}/cases/{cid}/ai/notes", json={"extraction_id": v["extraction_id"]}, headers=anm).json()
    u = n["urgency"]
    check("8 note: rules urgency first, AI may only raise", u["deterministic_urgency"] == det and (u["final"]["final_urgency"] == det or u["raise_requires_human_action"]), f"{det} -> {u['final']['final_urgency']}")
    check("8a every claim cites a field", all(cl["field_ids"] for cl in n["claims"]))
    check("8b sign-off required, not for clinical use", n["requires_sign_off"] and not n["clinical_use_allowed"])

    c.post(f"{BASE}/cases/{cid}/consent/withdraw", json={"purpose": "ai_assist"}, headers=anm)
    r = c.get(f"{BASE}/cases/{cid}/ai/extractions/{v['extraction_id']}", headers=anm)
    check("9 after withdrawing ai_assist, stored output is not served", r.status_code == 403)

    sup = login(c, "supervisor_demo", "supervisor")
    events = c.get(f"{BASE}/audit/{cid}", headers=sup).json()["events"]
    check("10 audit events carry no clinical values or quotes", not any(x in str(e["details"]) for e in events for x in ("150", "paracetamol", "chest", "Ramesh")))
    a = c.post(f"{BASE}/audit/verify", headers=sup)
    check("10a audit chain verifies", a.status_code == 200 and a.json().get("ok") is True, a.text[:120])

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
