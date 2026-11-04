"""Real-app browser check for P0-1, P0-2 and H1-3: a real synthetic lab report through the real local OCR engines, the
documents screen ("What to check, in order", C/U/R keys, auto-advance) and the medical officer's encounter summary
(unmatched documents, machine agreement, withheld content, evidence status). Synthetic sample only; scratch database
only; never point it at real data or the shared demo database.

Setup (from sehat-ai/; nothing is installed globally; S is any scratch directory):

    S=/tmp/sehat-ui && mkdir -p $S/docs
    # backend (from backend/): scratch database and document folder, fake AI, real OCR engines from .env
    ENVIRONMENT=development DATABASE_PATH=$S/sehat-ui.db OCR_DOCUMENT_DIR=$S/docs AI_PROVIDER=fake MEDGEMMA_ENABLED=0 \\
      VOICE_ENABLED=0 GUARDRAILS_ENABLED=0 TRANSLATION_ENABLED=0 AI_CLOUD_ENABLED=0 \\
      ../.venv/bin/python -m uvicorn app.main:app --port 8000
    # frontend (from frontend/):   npx next dev -p 3000      (dev, not `next start`: the session cookie must not be Secure)
    # Playwright in a throwaway venv, driving the installed Google Chrome (no browser download):
    uv venv $S/pw && uv pip install --python $S/pw/bin/python playwright
    # run (from backend/):
    $S/pw/bin/python scripts/e2e_real_app_browser_check.py [http://localhost:3000] [path/to/report.jpeg]
    # cleanup: stop both servers (lsof -ti:8000 -sTCP:LISTEN | xargs kill; same for 3000) and delete $S

Expected noise: with VOICE_ENABLED=0 the case workspace's voice requests answer 404 (feature disabled); those console
errors are ignored. Real OCR takes up to a few minutes per page on a laptop.
"""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

FE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:3000"
REPORT = sys.argv[2] if len(sys.argv) > 2 else str(Path(__file__).resolve().parents[2] / "public" / "med_img" / "test imag.jpeg")
OUT = Path.cwd()
TRIAGE = {"scenario": "opd", "age_years": 40, "red_flag_screen_completed": True,
          "vitals": {"resp_rate": 16, "spo2": 98, "on_supplemental_oxygen": False, "pulse": 78, "sbp": 124, "dbp": 80, "temp_c": 36.8, "consciousness": "A"}}
results: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok))
    print(("PASS " if ok else "FAIL ") + name + (f" — {detail}" if detail else ""), flush=True)


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1400, "height": 1000})
        errors: list[str] = []
        page.on("console", lambda m: m.type == "error" and errors.append(m.text))
        api = lambda path: f"{FE}/api/backend/{path}"  # noqa: E731
        login = lambda user, role: page.request.post(f"{FE}/api/session", data={"username": user, "role": role}).ok  # noqa: E731
        queue = lambda: page.request.get(api(f"cases/{cid}/verification-queue")).json()  # noqa: E731

        page.goto(f"{FE}/login")
        assert login("anm_demo", "anm")
        cid = page.request.post(api("cases"), data={"scenario": "opd", "facility_code": "PHC-KHURDA-01"}).json()["case_id"]
        version = page.request.get(api("consent/notice?language=en")).json()["version"]
        assert page.request.post(api(f"cases/{cid}/consent"), data={"decision": "grant", "include_ai_assist": False, "include_voice_cloud": False,
                                                                   "language": "en", "notice_version": version}).ok

        # P0-1 + P0-2: upload through the UI, real OCR, queue order
        page.goto(f"{FE}/intake/documents?case={cid}")
        page.get_by_role("button", name="Lab report (printed)").click()
        page.locator('input[type="file"][accept="image/png,image/jpeg,application/pdf"]').set_input_files(REPORT)
        page.get_by_role("heading", name="What to check, in order").wait_for(timeout=600_000)
        page.screenshot(path=str(OUT / "real_app_1_queue.png"), full_page=True)
        q = queue()
        labels = [(i["priority"], i["label"]) for i in q["items"]]
        check("1 queue: match the document first", labels[:1] and labels[0][0] == "match_document", str(labels[:1]))
        abnormal = [lab for prio, lab in labels if prio == "abnormal_lab"]
        check("2 parser: flagged rows recognised and ranked abnormal (P0-1)", {"SERUM UREA", "SERUM CREATININE"} <= set(abnormal), str(abnormal))
        check("3 parser: no explanation paragraph became a row", not any("is a nitrogenous" in lab or "correlate" in lab.lower() for _, lab in labels))

        page.get_by_role("button", name="Yes, this patient's report").click()
        page.wait_for_timeout(1500)
        q = queue()
        first = next(i for i in q["items"] if i["kind"] == "document_field")
        page.get_by_role("button", name=first["label"]).first.click()
        page.wait_for_timeout(800)
        page.locator("body").click(position={"x": 5, "y": 5})
        page.keyboard.press("c" if first["can_confirm"] else "u")
        page.wait_for_timeout(2000)
        q2 = queue()
        expected = "fewer items" if first["can_confirm"] else "same count (not sure stays queued)"
        changed = q2["counts"]["total"] < q["counts"]["total"] if first["can_confirm"] else q2["counts"]["total"] == q["counts"]["total"]
        check(f"4 key {'C' if first['can_confirm'] else 'U'} recorded a decision on {first['label']}", changed, expected)
        nxt = next((i for i in q2["items"] if i["kind"] == "document_field" and i["ref"]["field_id"] != first["ref"]["field_id"]), None)
        selected = page.locator('li[id^="field-"] button[aria-pressed="true"]').first.inner_text().splitlines()[0]
        check("5 auto-advance opened the next row in queue order", nxt is not None and selected == nxt["label"], f"{selected} / {nxt and nxt['label']}")
        box = page.locator('input[inputmode="decimal"]').first
        if box.count():
            before = queue()["counts"]["total"]
            box.click()
            box.type("cur")
            page.wait_for_timeout(1500)
            check("6 typing c/u/r in a correction box decides nothing", queue()["counts"]["total"] == before)

        # H1-3: the medical officer's summary
        assert page.request.post(api(f"cases/{cid}/triage?expected_run_id=none"), data=TRIAGE).ok
        assert login("mo_demo", "medical_officer")
        page.goto(f"{FE}/dashboard?case={cid}")
        page.get_by_role("button", name="Build summary from all recorded sources").click()
        page.get_by_role("heading", name="Evidence status").wait_for(timeout=120_000)
        page.wait_for_timeout(500)
        page.locator("section[aria-labelledby='encounter-summary-heading']").screenshot(path=str(OUT / "real_app_2_summary.png"))
        s = page.request.get(api(f"cases/{cid}/encounter-summary")).json()["summary"]
        st = s["evidence_status"]
        check("7 evidence counts come from the stored rows", st["observations"]["extracted"] == sum(len(d["fields"]) for d in
              page.request.get(api(f"cases/{cid}/documents")).json()["documents"]), str(st["observations"]))
        check("8 machine-agreed rows are labelled and never written into the text",
              page.get_by_text("OCR engines: Machine-agreed — not yet checked").count() == st["observations"]["machine_agreed_unchecked"]
              and all(f["trust"] != "unchecked" for n in s["narrative"] for f in (s["facts"][i] for i in n["fact_ids"]) if f["section"] == "labs"))
        check("9 withheld values are listed with a reason", page.get_by_text("Withheld pending verification").count() > 0 and bool(s["withheld"]))
        check("10 readiness is not 'ready' while values are unchecked", st["readiness"]["state"] == "incomplete", str(st["readiness"]))
        check("11 medical officer gets no link into the ANM-only documents step",
              page.get_by_role("link", name="Open in verification").count() == 0 and page.get_by_text("Checked on the documents step by the health worker").count() > 0)
        real_errors = [e for e in errors if "404" not in e]  # voice is disabled in this setup: its requests answer 404
        check("12 no browser console errors (voice-disabled 404s excepted)", not real_errors, str(real_errors[:3]))
        browser.close()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed; screenshots in {OUT}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
