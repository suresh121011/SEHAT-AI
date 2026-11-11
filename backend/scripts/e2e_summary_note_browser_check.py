"""Real-app browser check for the summary note (docs/plans/summary-note-all-sources.md): the interview screen, the
flagged-image review, and printing the note. Drives the running app in headless Google Chrome. Synthetic data only;
scratch database only; never point it at real data or the shared demo database.

Two modes (argument 2):
  fake   the image model is the built-in FAKE backend (MEDGEMMA_BACKEND=fake): an ECG upload returns canned text that
         raises a flag, so the flagged-image workflow is deterministic. Labelled as a test fixture, not a model.
  real   the engines configured for the server run for real: local image model, real OCR engines, local speech.
         Checks only that real output arrives, is labelled unchecked and is source-linked. NOT an accuracy test.

Setup (from the repository root; S is any scratch directory; nothing is installed globally):
    # backend (from backend/), scratch database and document folder:
    ENVIRONMENT=development ALLOW_UNSCOPED_DEMO=true ACCOUNT_FACILITIES= DATABASE_PATH=$S/e2e.db OCR_DOCUMENT_DIR=$S/docs \\
      OCR_ENABLED=1 OCR_RETENTION_DAYS=none AI_PROVIDER=fake MEDGEMMA_ENABLED=1 MEDGEMMA_BACKEND=fake VOICE_ENABLED=0 \\
      GUARDRAILS_ENABLED=0 TRANSLATION_ENABLED=0 AI_CLOUD_ENABLED=0 ../.venv/bin/python -m uvicorn app.main:app --port 8010
    # frontend (from frontend/):  NEXT_PUBLIC_API_URL=http://localhost:8010/api/v1 npx next dev -p 3010
    uv venv $S/pw && uv pip install --python $S/pw/bin/python playwright pypdf pillow
    cd $S && $S/pw/bin/python <repo>/backend/scripts/e2e_summary_note_browser_check.py http://127.0.0.1:3010 fake

The native print dialog cannot be driven by automation. This script proves that the Print button calls window.print()
once, what the page looks like under print media, and what Chrome writes for it as an A4 PDF (summary_note.pdf).
"""

import io
import json
import sys
import uuid
import wave
from pathlib import Path

from playwright.sync_api import sync_playwright

FE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:3010"
MODE = sys.argv[2] if len(sys.argv) > 2 else "fake"
OUT = Path.cwd()
FIX = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
TRIAGE = {"scenario": "opd", "age_years": 40, "red_flag_screen_completed": True,
          "vitals": {"resp_rate": 16, "spo2": 98, "on_supplemental_oxygen": False, "pulse": 78, "sbp": 124, "dbp": 80, "temp_c": 36.8, "consciousness": "A"}}
results: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok)))
    print(("PASS " if ok else "FAIL ") + name + (f" — {detail}" if detail else ""), flush=True)


def synthetic_png(kind: str) -> bytes:
    """Procedurally drawn, obviously synthetic pictures (no patient, no real image)."""
    from PIL import Image, ImageDraw, ImageFilter

    img = Image.new("L", (512, 512), 200 if kind == "chest" else 245)
    d = ImageDraw.Draw(img)
    if kind == "chest":
        d.ellipse((70, 90, 250, 430), fill=60)
        d.ellipse((262, 90, 442, 430), fill=60)
        d.rectangle((246, 40, 266, 480), fill=225)
        for y in range(110, 420, 32):
            d.arc((80, y - 40, 250, y + 40), 200, 340, fill=170, width=6)
            d.arc((262, y - 40, 432, y + 40), 200, 340, fill=170, width=6)
        img = img.filter(ImageFilter.GaussianBlur(3))
    else:  # an ECG-like trace on a grid
        for x in range(0, 512, 32):
            d.line((x, 0, x, 512), fill=215)
            d.line((0, x, 512, x), fill=215)
        pts, y0 = [], 256
        for x in range(0, 512, 4):
            ph = x % 96
            pts.append((x, y0 - (90 if ph == 40 else -25 if ph == 44 else 18 if 52 <= ph <= 68 else 0)))
        d.line(pts, fill=20, width=3)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


def silent_wav(seconds: float = 2.0) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * int(16000 * seconds))
    return buf.getvalue()


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1400, "height": 1000})
        errors: list[str] = []
        page.on("console", lambda m: m.type == "error" and errors.append(m.text))
        api = lambda path: f"{FE}/api/backend/{path}"  # noqa: E731
        login = lambda user, role: page.request.post(f"{FE}/api/session", data={"username": user, "role": role}).ok  # noqa: E731

        page.goto(f"{FE}/login")
        assert login("anm_demo", "anm")
        other = page.request.post(api("cases"), data={"scenario": "opd", "facility_code": "PHC-KHURDA-01"}).json()["case_id"]  # a second visit: must never be printed
        cid = page.request.post(api("cases"), data={"scenario": "opd", "facility_code": "PHC-KHURDA-01"}).json()["case_id"]
        version = page.request.get(api("consent/notice?language=en")).json()["version"]
        assert page.request.post(api(f"cases/{cid}/consent"), data={"decision": "grant", "include_ai_assist": True, "include_voice_cloud": False,
                                                                   "language": "en", "notice_version": version}).ok
        r = page.request.post(api(f"cases/{cid}/triage?expected_run_id=none"), data=TRIAGE)
        assert r.ok, r.text()
        caps = page.request.get(api("intake/document/capabilities")).json()
        print(f"mode={MODE} image backend={caps.get('medgemma_backend')} ({caps.get('medgemma_mode')}), synthetic={caps.get('medgemma_synthetic')}", flush=True)
        check("server image backend matches the requested mode", (caps.get("medgemma_backend") == "fake") == (MODE == "fake"), str(caps.get("medgemma_backend")))

        # per-symptom term and duration (fake keyword extractor in both modes; the real model is checked by
        # scripts/eval_symptom_extraction_live.py)
        ext = page.request.post(api(f"cases/{cid}/ai/extractions"), data={"idempotency_key": str(uuid.uuid4()),
                                "intake_text": "I am suffering from fever since 2 days and my head is paining since 2 hours. No vomiting."})
        check("symptoms: extraction ran", ext.status == 201, f"{ext.status} {ext.text()[:120] if ext.status != 201 else ''}")

        # ── 1. Interview screen (Questions step) ────────────────────────────────────────────────────────────────
        page.goto(f"{FE}/intake/follow-up?case={cid}")
        page.get_by_role("heading", name="AI-extracted values: your decision").wait_for(timeout=30000)
        review_text = page.locator("section").filter(has=page.get_by_role("heading", name="AI-extracted values: your decision")).first.inner_text()
        check("symptoms: the review screen shows each symptom with its own duration and the words said",
              "headache (said: head is paining), 2 hours" in review_text and "fever, 2 days" in review_text and "denies vomiting" in review_text)
        page.get_by_role("heading", name="Interview: fixed questions for missing information").wait_for(timeout=30000)
        check("interview: screen shows status and progress", page.get_by_test_id("interview-status").inner_text() == "Not started"
              and page.get_by_test_id("interview-known").inner_text() == "0 of 3")
        page.get_by_role("button", name="Start interview").click()
        page.get_by_role("heading", name="What is the main problem today?").wait_for()
        page.locator("#interview-text").fill("cough and fever")
        page.get_by_role("button", name="Confirm and next").click()
        check("interview: an answer is not saved without the read-back confirmation", "Tick the box" in page.get_by_role("alert").first.inner_text()
              and not page.request.get(api(f"cases/{cid}/interview")).json()["questions"][0]["answered"])
        page.locator("#interview-speaker").select_option("caregiver")
        check("interview: preview shows what will be recorded", "cough and fever" in page.get_by_test_id("interview-preview").inner_text()
              and "a caregiver" in page.get_by_test_id("interview-preview").inner_text())
        page.get_by_label("I read this back and it is what was said.").check()
        page.get_by_role("button", name="Confirm and next").click()
        page.get_by_role("heading", name="Since when has this problem been present?").wait_for()
        page.get_by_text("1-3 days", exact=True).click()
        page.get_by_label("I read this back and it is what was said.").check()
        page.get_by_role("button", name="Confirm and next").click()
        page.get_by_role("heading", name="How bad is the pain or discomfort", exact=False).wait_for()
        page.get_by_role("button", name="Skip this question").click()
        page.get_by_role("heading", name="Answers recorded for this visit").wait_for()
        row = page.get_by_test_id("interview-row-chief_complaint").inner_text()
        check("interview: review lists the confirmed answers with who answered", "cough and fever" in row and "A caregiver" in row and "typed" in row, row.replace("\n", " | ")[:140])
        check("interview: a skipped question stays listed as missing", "Severity" in page.get_by_text("Still missing", exact=True).locator("xpath=..").inner_text())
        page.reload()
        page.get_by_test_id("interview-known").wait_for(timeout=30000)
        check("interview: answers persist after a reload", page.get_by_test_id("interview-known").inner_text() == "2 of 3"
              and page.get_by_test_id("interview-status").inner_text() == "In progress")
        # correction: change an answer; the latest confirmed answer counts
        page.get_by_role("button", name="Review answers").click()
        page.get_by_test_id("interview-row-chief_complaint").get_by_role("button").click()
        page.locator("#interview-text").fill("My name is Ramesh Kumar and my phone is 9876543210")
        page.get_by_label("I read this back and it is what was said.").check()
        page.get_by_role("button", name="Confirm and next").click()
        page.get_by_text("Nothing was saved").first.wait_for(timeout=30000)
        check("interview: an answer that looks like a name or phone number is refused and nothing is saved",
              page.request.get(api(f"cases/{cid}/interview")).json()["questions"][0]["answer"]["text"] == "cough and fever")
        page.locator("#interview-text").fill("dry cough, worse at night")
        page.get_by_label("I read this back and it is what was said.").check()
        page.get_by_role("button", name="Confirm and next").click()
        page.get_by_role("heading", name="Since when has this problem been present?").wait_for()
        page.get_by_role("button", name="Pause").click()
        check("interview: pause keeps what was saved and offers resume", page.get_by_role("button", name="Resume interview").is_visible())
        got = {q["key"]: q["answer"] for q in page.request.get(api(f"cases/{cid}/interview")).json()["questions"]}
        check("interview: the correction is stored (latest answer counts)", got["chief_complaint"]["text"] == "dry cough, worse at night" and got["duration"]["choice"] == "1-3 days"
              and got["severity"] is None)
        page.screenshot(path=str(OUT / f"e2e_{MODE}_1_interview.png"), full_page=True)

        # ── 2. Uploads through the API (the engines run on the server) ───────────────────────────────────────────
        def upload(doc_type: str, name: str, data: bytes, mime: str):
            return page.request.post(api("intake/document"), multipart={"case_id": cid, "document_type": doc_type, "idempotency_key": str(uuid.uuid4()),
                                                                        "file": {"name": name, "mimeType": mime, "buffer": data}}, timeout=900000)

        if MODE == "fake":
            img = upload("ecg_strip", "synthetic_ecg.png", synthetic_png("ecg"), "image/png").json()
            check("flagged image: the FAKE backend's canned ECG text raises a flag that needs review",
                  img.get("status") == "described" and img.get("requires_acknowledgement") is True and img.get("backend") == "fake", json.dumps(img.get("urgency_signals"))[:160])
        else:
            img = upload("chest_xray", "synthetic_chest.png", synthetic_png("chest"), "image/png").json()
            print("REAL image model:", img.get("backend"), img.get("model"), "| fields:", json.dumps(img.get("structured_fields"))[:500], flush=True)
            check("real image model: a description arrived from the configured non-fake backend", img.get("status") == "described" and img.get("backend") not in (None, "fake")
                  and bool(img.get("structured_fields")), f"{img.get('status')} {img.get('backend')} {img.get('failure_code', '')}")
            lab = upload("lab_report", "cbc_low_platelet.png", (FIX / "ocr" / "cbc_low_platelet.png").read_bytes(), "image/png").json()
            rows = [(f.get("analyte_key"), (f.get("value") or {}).get("value"), f.get("review_status")) for f in lab.get("fields", [])]
            print("REAL OCR:", lab.get("status"), "rows:", rows, flush=True)
            check("real OCR: the synthetic lab report was read into rows, each still unchecked", lab.get("status") == "completed" and len(rows) >= 4
                  and all(r[2] == "machine_read" for r in rows), f"{lab.get('status')} {len(rows)} rows")
            truth = {"hemoglobin": "11.2", "platelets": "85000"}
            hits = {k: next((str(r[1]) for r in rows if r[0] == k), None) for k in truth}
            check("real OCR: two known printed values were read exactly (synthetic page; not an accuracy claim)", hits == truth, str(hits))
            assert page.request.post(api(f"cases/{cid}/documents/{lab['document_id']}/attestation"), data={"answer": "matches", "supersedes": None}).ok
            vcaps = page.request.get(api("voice/capabilities"))
            print("voice capabilities:", vcaps.status, vcaps.text()[:300], flush=True)
            q = lambda lang: f"cases/{cid}/voice/transcriptions?language={lang}&engine=local&idempotency_key={uuid.uuid4()}"  # noqa: E731
            hi = page.request.post(api(q("hi")), data=(FIX / "voice" / "hi_fever_102.wav").read_bytes(), headers={"Content-Type": "audio/wav"}, timeout=600000)
            t = hi.json()
            print("REAL speech (hi, local):", hi.status, t.get("status"), t.get("engine"), t.get("model_id"), "|", t.get("transcript_raw"),
                  "| candidates:", [(c["field"], c["display"]) for c in t.get("candidates", [])], flush=True)
            check("real speech: Hindi clip transcribed by the local engine, values left for read-back (none confirmed)", hi.ok and t.get("status") == "completed"
                  and bool(t.get("transcript_raw")) and all(c["resolution"] is None for c in t.get("candidates", [])), f"{hi.status} {t.get('status')}")
            en = page.request.post(api(q("en")), data=(FIX / "voice" / "en_fever_102.wav").read_bytes(), headers={"Content-Type": "audio/wav"}, timeout=600000)
            print("speech (en, local):", en.status, en.text()[:200], flush=True)
            check("real speech: English is refused by the local engine instead of being guessed", en.status >= 400)
            sil = page.request.post(api(q("hi")), data=silent_wav(), headers={"Content-Type": "audio/wav"}, timeout=600000)
            print("speech (silence):", sil.status, sil.text()[:200], flush=True)
            check("real speech: a silent clip gives no transcript", (sil.ok and sil.json().get("status") in ("no_speech", "empty_transcript")) or sil.status in (400, 422),
                  f"{sil.status} {sil.json().get('status') if sil.ok else ''}")
            bad = page.request.post(api(q("hi")), data=b"this is not audio", headers={"Content-Type": "audio/wav"}, timeout=60000)
            check("real speech: bytes that are not audio are refused", bad.status in (400, 415, 422), str(bad.status))

        # ── 3. Medical officer: summary note, flagged image review, print ───────────────────────────────────────
        assert login("mo_demo", "medical_officer")
        run_id = page.request.get(api(f"triage/{cid}")).json()["latest"]["triage_run_id"]
        if MODE == "fake":
            so = page.request.patch(api(f"triage/{cid}/sign-off"), data={"triage_run_id": run_id, "confirm": True})
            check("flagged image: sign-off is refused while the flag is not reviewed", so.status == 409 and so.json()["error"]["code"] == "IMAGE_FINDINGS_NOT_REVIEWED", str(so.status))
        page.goto(f"{FE}/dashboard?case={cid}")
        page.locator("#encounter-note").wait_for(timeout=60000)
        note = page.locator("#encounter-note").inner_text()
        low = note.lower()
        check("summary: built on open, with interview answers as a table section", "interview answers" in low and "dry cough, worse at night" in note and "1-3 days" in note)
        check("summary: each symptom is stated with its own duration", "Fever for 2 days" in note and "Headache for 2 hours" in note)
        check("summary: imaging section names the image and its review state", "imaging" in low and "image on file" in low and "not yet reviewed" in low)
        panel = page.locator("section,div").filter(has=page.get_by_role("heading", name="Encounter summary")).first.inner_text()
        if MODE == "fake":
            check("flagged image: the summary raises it under Needs attention", "carry a flag that nobody has marked as reviewed" in panel)
            check("flagged image: the image, its described text and the flag reason are visible to the doctor",
                  page.get_by_text("ST elevation in leads V1-V4").first.is_visible() and page.get_by_text("Sign-off is blocked until it is").first.is_visible()
                  and page.get_by_role("link", name="Open the image").first.is_visible())
            src = page.request.get(f"{FE}{img['source_image_url']}")
            check("flagged image: the stored source image opens", src.ok and src.headers.get("content-type", "").startswith("image/"), str(src.status))
            page.screenshot(path=str(OUT / "e2e_fake_2_flagged_before.png"), full_page=True)
            box = page.get_by_label("Findings reviewed", exact=False).first
            box.scroll_into_view_if_needed()
            box.click()  # controlled checkbox: it turns on only after the server recorded the review
            page.get_by_text("Out of date").first.wait_for(timeout=30000)
            check("flagged image: recording the review marks the summary out of date", True)
            page.get_by_role("button", name="Refresh summary").click()
            page.get_by_text("marked as reviewed by a clinician").first.wait_for(timeout=30000)
            panel = page.locator("section,div").filter(has=page.get_by_role("heading", name="Encounter summary")).first.inner_text()
            check("flagged image: the refreshed summary shows the reviewed state and no open flag", "carry a flag that nobody" not in panel)
            images = page.request.get(api(f"cases/{cid}/medical-images")).json()["medical_images"]
            check("flagged image: the review persists on the server", images[0]["acknowledged"] is True)
            so = page.request.patch(api(f"triage/{cid}/sign-off"), data={"triage_run_id": run_id, "confirm": True})
            check("flagged image: sign-off is no longer blocked by the image", not (so.status == 409 and so.json()["error"]["code"] == "IMAGE_FINDINGS_NOT_REVIEWED"), str(so.status))
            page.screenshot(path=str(OUT / "e2e_fake_3_flagged_after.png"), full_page=True)
        else:
            check("real engines: the lab rows from real OCR are in the note, marked not yet checked", "lab values" in low and "machine-read, not yet checked" in low)
            page.screenshot(path=str(OUT / "e2e_real_2_summary.png"), full_page=True)

        # print: the button calls window.print() once; under print media only the note is on the page
        page.evaluate("""() => { window.__prints = 0; window.print = () => { window.__prints += 1;
            window.__root = document.getElementById('print-root')?.innerText || null;
            window.__cls = document.documentElement.classList.contains('print-note'); }; }""")
        page.get_by_role("button", name="Print").click()
        st = page.evaluate("() => ({n: window.__prints, cls: window.__cls, root: window.__root})")
        check("print: the button calls window.print() exactly once (the native dialog itself is not driven by automation)", st["n"] == 1 and st["cls"] is True)
        root = st["root"] or ""
        check("print: the printed copy holds this visit's note, its status, the visit id and the disclaimer", "Encounter summary" in root and cid in root
              and "Printed" in root and "Not a diagnosis" in root and ("waiting for review" in root or "Accepted" in root or "Draft" in root))
        check("print: no other visit's id is in the printed copy", other not in root)
        page.emulate_media(media="print")
        shown = page.evaluate("""() => [...document.body.children].filter(e => getComputedStyle(e).display !== 'none' && e.tagName !== 'SCRIPT').map(e => e.id || e.tagName)""")
        buttons = page.evaluate("""() => [...document.querySelectorAll('button, nav, aside')].filter(e => e.offsetParent !== null).length""")
        check("print: under print media only the note is laid out (no navigation, buttons or other panels)", shown == ["print-root"] and buttons == 0, f"{shown} buttons={buttons}")
        page.screenshot(path=str(OUT / f"e2e_{MODE}_print_preview.png"), full_page=True)
        page.pdf(path=str(OUT / f"summary_note_{MODE}.pdf"), format="A4", prefer_css_page_size=True)
        try:
            from pypdf import PdfReader

            pdf = PdfReader(str(OUT / f"summary_note_{MODE}.pdf"))
            text = "\n".join(pg.extract_text() or "" for pg in pdf.pages)
            w, h = float(pdf.pages[0].mediabox.width), float(pdf.pages[0].mediabox.height)
            check("print: Chrome writes the note as an A4 PDF with its sections and no screen controls", abs(w - 595) < 3 and abs(h - 842) < 3 and "URGENCY" in text.upper()
                  and "INTERVIEW ANSWERS" in text.upper() and "Copy note" not in text and "Sign off" not in text and "Priority queue" not in text, f"{len(pdf.pages)} page(s), {w:.0f}x{h:.0f}pt")
        except ImportError:
            check("print: PDF text inspected (pypdf not installed)", False)
        page.emulate_media(media="screen")
        page.evaluate("() => window.dispatchEvent(new Event('afterprint'))")
        check("print: after printing the page returns to normal", page.evaluate("() => !document.documentElement.classList.contains('print-note') && !document.getElementById('print-root')"))

        # ── 4. Audit trail (supervisor: metadata only) ──────────────────────────────────────────────────────────
        assert login("supervisor_demo", "supervisor")
        events = page.request.get(api(f"audit/{cid}")).json()["events"]
        actions = [e["action"] for e in events]
        want = ["interview_answer_recorded", "encounter_summary_generated"] + (["medgemma_findings_acknowledged"] if MODE == "fake" else [])
        check("audit: the interview answers, the summary and the image review are recorded", all(a in actions for a in want), str(sorted(set(actions)))[:300])
        blob = json.dumps(events)
        check("audit: the log holds no answer text and no image finding text", "dry cough" not in blob and "ST elevation" not in blob and "cough and fever" not in blob)

        ignorable = ("404", "Failed to load resource")
        real_errors = [e for e in errors if not any(x in e for x in ignorable)]
        check("no unexpected console errors", not real_errors, "; ".join(real_errors)[:300])
        browser.close()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)} / {len(results)} checks passed ({MODE} mode)" + (f"; FAILED: {failed}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
