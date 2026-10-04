"""Phase 8 reviewer dashboard checks over the browser's HTTP path: Next.js session cookie -> /api/backend proxy ->
FastAPI. NOT a browser test: it does not render pages or run client JavaScript (layout, focus, dialogs and the
countdown need the manual/browser walkthrough in docs/17 §9). Synthetic values only; shared demo accounts; never
point it at real data. Seeds RED, YELLOW and GREEN cases, so use a scratch database.

    # backend (from backend/):  ENVIRONMENT=development DATABASE_PATH=/tmp/sehat-review.db AI_PROVIDER=fake ../.venv/bin/python -m uvicorn app.main:app --port 8000
    # frontend (from frontend/): npx next dev -p 3000
    # then (from backend/):      ../.venv/bin/python scripts/e2e_review_ui_check.py [http://localhost:3000]
"""

import sys

import httpx

FE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:3000"
results: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok))
    print(("PASS " if ok else "FAIL ") + name + (f" — {detail}" if detail else ""), flush=True)


def session(user: str, role: str) -> httpx.Client:
    c = httpx.Client(base_url=FE, timeout=120)
    c.post("/api/session", json={"username": user, "role": role}).raise_for_status()
    return c


def new_case(anm: httpx.Client, scenario: str = "opd") -> str:
    cid = anm.post("/api/backend/cases", json={"scenario": scenario, "facility_code": "PHC-KHURDA-01"}).json()["case_id"]
    v = anm.get("/api/backend/consent/notice?language=en").json()["version"]
    anm.post(f"/api/backend/cases/{cid}/consent", json={"decision": "grant", "include_ai_assist": False, "include_voice_cloud": False, "language": "en", "notice_version": v}).raise_for_status()
    return cid


GREEN = {"scenario": "opd", "age_years": 34, "pregnant": False, "vitals": {"resp_rate": 16, "spo2": 98, "on_supplemental_oxygen": False, "pulse": 78, "sbp": 122, "dbp": 80, "temp_c": 36.8, "consciousness": "A"},
         "red_flag_screen_completed": True, "red_flags_present": [], "suspected_infection": False}
RED = {**GREEN, "vitals": {**GREEN["vitals"], "spo2": 84, "resp_rate": 30}}
MISSING = {"scenario": "opd", "age_years": 51, "pregnant": False, "vitals": {"pulse": 96}, "red_flag_screen_completed": False, "red_flags_present": [], "suspected_infection": False}


def main() -> int:
    anm = session("anm_demo", "anm")
    mo = session("mo_demo", "medical_officer")
    sup = session("supervisor_demo", "supervisor")

    r = mo.get("/dashboard", follow_redirects=False)
    check("1a MO can open /dashboard (middleware)", r.status_code == 200, str(r.status_code))
    r = mo.get("/dashboard/governance", follow_redirects=False)
    check("1b MO is redirected away from /dashboard/governance (supervisor only)", r.status_code in (302, 307), str(r.status_code))
    r = sup.get("/dashboard/governance", follow_redirects=False)
    check("1c supervisor can open /dashboard/governance", r.status_code == 200, str(r.status_code))
    r = anm.get("/dashboard", follow_redirects=False)
    check("2 ANM is redirected away from /dashboard", r.status_code in (302, 307), str(r.status_code))

    ids = {}
    for name, body in (("green", GREEN), ("red", RED), ("missing", MISSING)):
        cid = new_case(anm)
        r = anm.post(f"/api/backend/cases/{cid}/triage", json=body)
        ids[name] = (cid, r.json()["run_id"], r.json()["result"]["urgency"])
    check("3 seeded rules results", ids["red"][2] == "RED" and ids["green"][2] == "GREEN" and ids["missing"][2] != "GREEN", str({k: v[2] for k, v in ids.items()}))

    q = mo.get("/api/backend/triage/queue").json()
    order = [i["case_id"] for i in q["items"]]
    mine = [c for c in order if c in {v[0] for v in ids.values()}]
    check("4 queue order RED → YELLOW → GREEN", mine == [ids["red"][0], ids["missing"][0], ids["green"][0]], str([i["priority_urgency"] for i in q["items"] if i["case_id"] in mine]))
    check("5 queue item carries no AI score", not any(k for i in q["items"] for k in i if "confidence" in k or k.startswith("ai_")))
    red_item = next(i for i in q["items"] if i["case_id"] == ids["red"][0])
    check("6 RED escalation pending, server deadline, no notification", red_item["escalation"]["state"] == "pending" and red_item["escalation"]["notification_sent"] is False)

    cid, run, _ = ids["red"]
    cv = mo.get(f"/api/backend/triage/{cid}").json()
    check("7 case review has rules result, input, reasons", cv["latest"]["result"]["urgency"] == "RED" and cv["latest"]["input"]["vitals"]["spo2"] == 84 and len(cv["override_reasons"]) >= 2)
    cf = mo.get(f"/api/backend/cases/{cid}/triage/runs/{run}/counterfactuals").json()
    after = mo.get(f"/api/backend/triage/{cid}").json()
    check("8 counterfactuals computed and the case is unchanged", cf["status"] == "computed" and after["latest"]["triage_run_id"] == run and after["latest"]["rules_urgency"] == "RED")

    r = mo.patch(f"/api/backend/triage/{cid}/sign-off", json={"triage_run_id": run})
    check("9 sign-off without confirm is rejected", r.status_code == 400, str(r.status_code))
    r = sup.patch(f"/api/backend/triage/{cid}/sign-off", json={"triage_run_id": run, "confirm": True})
    check("10 supervisor cannot sign off", r.status_code == 403, str(r.status_code))
    r = mo.patch(f"/api/backend/triage/{cid}/override", json={"triage_run_id": run, "new_urgency": "YELLOW", "reason_code": "made_up", "confirm": True})
    check("11 unknown reason code rejected by the backend", r.status_code == 400, str(r.status_code))
    r = mo.patch(f"/api/backend/triage/{cid}/override", json={"triage_run_id": run, "new_urgency": "YELLOW", "reason_code": "clinical_reassessment", "confirm": True})
    check("12 lowering override recorded", r.status_code == 200 and r.json()["rules_urgency"] == "RED", str(r.status_code))
    q = mo.get("/api/backend/triage/queue").json()
    item = next(i for i in q["items"] if i["case_id"] == cid)
    check("13 lowered case keeps RED queue position", item["priority_urgency"] == "RED" and item["effective_urgency"] == "YELLOW")
    r = mo.post(f"/api/backend/triage/{cid}/acknowledge", json={"triage_run_id": run})
    check("14 RED acknowledged, notification_sent false", r.status_code == 200 and r.json()["notification_sent"] is False, str(r.status_code))

    # Correct vitals -> new run; acting on the old run is stale.
    fixed = {**cv["latest"]["input"], "vitals": {**cv["latest"]["input"]["vitals"], "spo2": 97, "resp_rate": 16}}
    r = mo.post(f"/api/backend/cases/{cid}/triage", json=fixed)
    check("15a re-run without the current run id → 409 EXPECTED_RUN_REQUIRED", r.status_code == 409 and r.json()["error"]["code"] == "EXPECTED_RUN_REQUIRED", str(r.status_code))
    r = mo.post(f"/api/backend/cases/{cid}/triage?expected_run_id=none", json=fixed)
    check("15b re-run from a stale view → 409 STALE_TRIAGE_RUN", r.status_code == 409 and r.json()["error"]["code"] == "STALE_TRIAGE_RUN", str(r.status_code))
    latest = mo.get(f"/api/backend/cases/{cid}").json()["latest_triage_run_id"]
    r = mo.post(f"/api/backend/cases/{cid}/triage?expected_run_id={latest}", json=fixed)
    check("15 corrected re-run recorded as a new run", r.status_code == 200 and r.json()["run_id"] != run, str(r.status_code))
    r = mo.patch(f"/api/backend/triage/{cid}/sign-off", json={"triage_run_id": run, "confirm": True})
    check("16 sign-off on the superseded run → 409 STALE_TRIAGE_RUN", r.status_code == 409 and r.json()["error"]["code"] == "STALE_TRIAGE_RUN")
    hist = mo.get(f"/api/backend/triage/{cid}").json()
    check("17 history keeps both runs and the earlier override", len(hist["runs"]) == 2 and any(e["kind"] == "override" and e["triage_run_id"] == run for e in hist["review_events"]))
    new_run = hist["latest"]["triage_run_id"]
    r = mo.patch(f"/api/backend/triage/{cid}/sign-off", json={"triage_run_id": new_run, "confirm": True})
    r2 = mo.patch(f"/api/backend/triage/{cid}/sign-off", json={"triage_run_id": new_run, "confirm": True})
    check("18 sign-off recorded once; duplicate → 409", r.status_code == 200 and r2.status_code == 409)

    # Reviewer correction (docs/17 §3a): bound to the run the reviewer saw; the earlier run is kept.
    cid2, run2, _ = ids["green"]
    inp = mo.get(f"/api/backend/triage/{cid2}").json()["latest"]["input"]
    body = {"expected_triage_run_id": run2, "input": {**inp, "vitals": {**inp["vitals"], "spo2": 91}}, "reason_code": "remeasured", "reason_text": None, "confirm": True}
    r = mo.post(f"/api/backend/triage/{cid2}/corrections", json=body)
    check("22 correction recorded as a new linked run", r.status_code == 201 and r.json()["corrects_run_id"] == run2, str(r.status_code))
    r2 = mo.post(f"/api/backend/triage/{cid2}/corrections", json=body)
    check("23 a second correction of the same run → 409 STALE_TRIAGE_RUN", r2.status_code == 409 and r2.json()["error"]["code"] == "STALE_TRIAGE_RUN")
    prov = mo.get(f"/api/backend/triage/{cid2}").json()["latest"]["field_provenance"]
    check("24 field provenance only for the corrected field", prov.get("vitals.spo2", {}).get("source") == "reviewer_correction" and prov.get("*", {}).get("source") == "unchanged_from_previous_run")
    bad = mo.post(f"/api/backend/triage/{cid2}/corrections", json={**body, "expected_triage_run_id": r.json()["triage_run_id"], "reason_code": "nope"})
    check("25 unknown correction reason rejected by the backend", bad.status_code == 400, str(bad.status_code))
    trav = mo.get("/api/backend/%2E%2E/%2E%2E/docs")
    check("26 proxy refuses path traversal", trav.status_code in (400, 404), str(trav.status_code))

    g = sup.get("/api/backend/audit/governance").json()
    check("19 governance: denominators and definitions present", g["overrides"]["denominator"] == g["sample_size"] and "÷" in g["overrides"]["definition"] and g["overrides"]["events"] >= 1)
    text = str(g)
    check("20 governance has no case ids or tokens", all(v[0] not in text for v in ids.values()) and "PT-" not in text)
    r = mo.get("/api/backend/audit/governance")
    check("27 MO cannot read governance", r.status_code == 403, str(r.status_code))

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
