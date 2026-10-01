"""Phase 5 checklist (docs/14 §9.3) over the browser's HTTP path: Next.js session cookie -> same-origin proxy ->
FastAPI -> the real local OCR engines. NOT a browser: rendering, on-screen highlight placement, keyboard and focus
are not covered. Synthetic fixtures only; uses the shared demo accounts; never point it at real data.

    # backend (from backend/), with a scratch database:
    ENVIRONMENT=development DATABASE_PATH=/tmp/sehat-e2e.db OCR_ENABLED=1 OCR_SURYA_ENABLED=1 \
      OCR_CHANDRA_ENABLED=1 OCR_RETENTION_DAYS=none OCR_DOCUMENT_DIR=/tmp/sehat-e2e-docs \
      ../.venv/bin/python -m uvicorn app.main:app --port 8000
    # frontend (from frontend/):  npx next dev -p 3100
    # then (from backend/):       ../.venv/bin/python scripts/e2e_ocr_proxy_check.py /tmp/sehat-e2e.db [http://localhost:3100]
"""
import io
import sqlite3
import sys
import time
import uuid

import httpx
from PIL import Image

from pathlib import Path

DB = sys.argv[1]
FE = sys.argv[2] if len(sys.argv) > 2 else "http://localhost:3100"
FIX = str(Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "ocr") + "/"
results = []


def check(name, ok, detail=""):
    results.append((name, "PASS" if ok else "FAIL", detail))
    print(("PASS " if ok else "FAIL ") + name + (f" — {detail}" if detail else ""), flush=True)


def session(user, role):
    c = httpx.Client(base_url=FE, timeout=600, follow_redirects=False)
    r = c.post("/api/session", json={"username": user, "role": role})
    assert r.status_code == 200, r.text
    return c


anm = session("anm_demo", "anm")
check("1 login as ANM via /api/session (httpOnly cookie)", "sehat_session" in anm.cookies)

page = anm.get("/intake/documents?case=x")
check("2 /intake/documents page served to ANM (middleware)", page.status_code == 200 and "<html" in page.text.lower(), f"HTTP {page.status_code}")

case = anm.post("/api/backend/cases", json={"scenario": "opd", "facility_code": "PHC-KHURDA-01"}).json()["case_id"]
notice = anm.get("/api/backend/consent/notice?language=en").json()
stale = anm.post(f"/api/backend/cases/{case}/consent", json={"decision": "grant", "include_ai_assist": False, "include_voice_cloud": False, "language": "en", "notice_version": "2026-10-01.1"})
check("3a stale consent version refused", stale.status_code == 409 and stale.json()["error"]["code"] == "NOTICE_VERSION_STALE", stale.json()["error"]["code"])
check("3b notice discloses documents", any("picture of each page" in p for p in notice["paragraphs"]), notice["version"])
up0 = anm.post("/api/backend/intake/document", data={"case_id": case, "document_type": "lab_report", "idempotency_key": str(uuid.uuid4())},
               files={"file": ("a.png", open(FIX + "cbc_low_platelet.png", "rb").read(), "image/png")})
check("3c upload blocked without consent", up0.status_code == 403 and up0.json()["error"]["code"] == "CONSENT_REQUIRED")
ok = anm.post(f"/api/backend/cases/{case}/consent", json={"decision": "grant", "include_ai_assist": False, "include_voice_cloud": False, "language": "en", "notice_version": notice["version"]})
check("3d consent recorded under current notice", ok.status_code == 200)


def upload(client, fixture, dtype="lab_report", data=None):
    t = time.time()
    r = client.post("/api/backend/intake/document", data={"case_id": case, "document_type": dtype, "idempotency_key": str(uuid.uuid4())},
                    files={"file": (fixture, data if data is not None else open(FIX + fixture, "rb").read(), "application/octet-stream")})
    return r, time.time() - t


r, dt = upload(anm, "cbc_low_platelet.png")
lab = r.json()
check("4 upload synthetic low-platelet lab report through proxy (multipart)", r.status_code == 200 and lab["status"] == "completed", f"{dt:.1f}s engines={lab.get('engines')}")
check("5 all values machine_read (nothing reviewed without action)", all(f["review_status"] == "machine_read" for f in lab["fields"]))

for name, data, code in (("6a unsupported type (GIF)", b"GIF89a" + b"x" * 200, "UNSUPPORTED_MEDIA_TYPE"),
                         ("6b malformed PNG", b"\x89PNG\r\n\x1a\n" + b"junk" * 50, "DOCUMENT_INVALID")):
    rr, _ = upload(anm, "bad.bin", data=data)
    check(name, rr.json()["error"]["code"] == code, rr.json()["error"]["code"])
big = b"\x89PNG\r\n\x1a\n" + b"0" * (11 * 1024 * 1024)
rr, _ = upload(anm, "big.png", data=big)
check("6c oversized (>10 MB) refused", rr.status_code == 413, f"HTTP {rr.status_code}")
pg = Image.new("L", (200, 200), "white")
buf = io.BytesIO()
pg.save(buf, format="PDF", save_all=True, append_images=[pg] * 5)
rr, _ = upload(anm, "six.pdf", data=buf.getvalue())
check("6d too many PDF pages refused", rr.json()["error"].get("details", {}).get("reason") == "too_many_pages", str(rr.json()["error"]))
blur = io.BytesIO()
from PIL import ImageFilter
Image.open(FIX + "cbc_low_platelet.png").filter(ImageFilter.GaussianBlur(3)).save(blur, format="PNG")
rr, _ = upload(anm, "blur.png", data=blur.getvalue())
check("6e blurry photo -> retake feedback", rr.status_code == 422 and "blurry" in rr.json()["error"]["details"]["reasons"])

img = anm.get(f"/api/backend/cases/{case}/documents/{lab['document_id']}/pages/0/image")
check("7a page image via proxy: PNG, no-store, nosniff", img.status_code == 200 and img.headers.get("content-type") == "image/png"
      and img.headers.get("cache-control") == "no-store" and img.headers.get("x-content-type-options") == "nosniff", str(dict(img.headers)))
plt = next(f for f in lab["fields"] if f["analyte_key"] == "platelets")
w, h = Image.open(io.BytesIO(img.content)).size
roles = {r["role"]: r["bbox"] for r in plt["regions"]}
inside = all(0 <= b[0] < b[2] <= w and 0 <= b[1] < b[3] <= h for b in roles.values())
check("7b platelet regions per role, inside the served page", set(roles) == {"name", "value", "unit", "range", "flag"} and inside, f"page {w}x{h}")

doc_id = lab["document_id"]
cnf = anm.post(f"/api/backend/cases/{case}/documents/fields/{plt['field_id']}/review",
               json={"outcome": "confirmed", "shown_png_sha256": plt["page_png_sha256"], "shown_regions_sha256": plt["regions_sha256"]})
check("8a confirm blocked before attestation", cnf.status_code == 409 and cnf.json()["error"]["code"] == "ATTESTATION_REQUIRED")
att = anm.post(f"/api/backend/cases/{case}/documents/{doc_id}/attestation", json={"answer": "matches"})
check("8b attestation recorded", att.status_code == 200 and att.json()["attestation"]["answer"] == "matches")
cnf = anm.post(f"/api/backend/cases/{case}/documents/fields/{plt['field_id']}/review",
               json={"outcome": "confirmed", "shown_png_sha256": plt["page_png_sha256"], "shown_regions_sha256": plt["regions_sha256"]})
check("8c confirm platelets", cnf.status_code == 200 and cnf.json()["review_status"] == "confirmed")
hb = next(f for f in lab["fields"] if f["analyte_key"] == "hemoglobin")
cor = anm.post(f"/api/backend/cases/{case}/documents/fields/{hb['field_id']}/review",
               json={"outcome": "corrected", "correction": {"result": {"value": "11.3", "comparator": "<"}, "unit": "g/dL"},
                     "shown_png_sha256": hb["page_png_sha256"], "shown_regions_sha256": hb["regions_sha256"]})
check("9a correction with '<' accepted", cor.status_code == 200, cor.text[:200])
reload = anm.get(f"/api/backend/cases/{case}/documents/{doc_id}").json()
hb2 = next(f for f in reload["fields"] if f["field_id"] == hb["field_id"])
check("9b '<' and unit persist after reload; machine reading kept", (hb2["reviewed_value"]["comparator"], hb2["reviewed_value"]["value"], hb2["reviewed_value"]["unit"]) == ("<", "11.3", "g/dL")
      and hb2["value"]["raw"] == "11.2" and hb2["review_status"] == "corrected")

rv = anm.get(f"/api/backend/cases/{case}/documents/reviewed").json()
names = {v["name"]: v for v in rv["values"]}
check("10a reviewed view: only confirmed/corrected, with source", set(names) == {"platelets", "hemoglobin"} and names["hemoglobin"]["basis"] == "reviewer_entered_from_paper", str(sorted(names)))
check("10b reviewed view says it never changes triage", "never change triage" in rv["note"])
with sqlite3.connect(DB) as c:
    runs = c.execute("SELECT count(*) FROM triage_runs WHERE case_id = ?", (case,)).fetchone()[0]
check("10c no triage run written by OCR/review", runs == 0, f"triage_runs={runs}")

r2, dt2 = upload(anm, "two_page_scan.pdf")
d2 = r2.json()
pages = {f["page_index"] for f in d2["fields"]}
imgs = [anm.get(f"/api/backend/cases/{case}/documents/{d2['document_id']}/pages/{i}/image").status_code for i in (0, 1, 2)]
check("11a 2-page PDF: fields on both pages, page images 0/1 served, 2 -> 404", pages == {0, 1} and imgs == [200, 200, 404], f"{dt2:.1f}s {imgs}")
disputed = [f for f in d2["fields"] if f["disputed"]]
check("11b engine disagreement visible (disputed rows, both readings)", all(len({x["engine"] for x in f["readings"]}) >= 2 for f in disputed), f"{len(disputed)} disputed: " + ", ".join(f"{f['name_raw']}" for f in disputed))
hbs = next((f for f in d2["fields"] if f["analyte_key"] == "hbsag"), None)
check("11c missing unit kept missing (flag), no fabricated unit", hbs is not None and "unit_missing" in hbs["flags"] and hbs["unit"]["key"] is None)

r3, dt3 = upload(anm, "rx_handwritten.png", "prescription")
d3 = r3.json()
meds = {f["drug_raw"]: f for f in d3["fields"]}
check("12 prescription via Chandra: meds + disputes for differing readings", r3.status_code == 200 and "Pantoprazole" in meds, f"{dt3:.1f}s " + "; ".join(f"{k}:{'dispute' if v['disputed'] else v['band']}" for k, v in meds.items()))

sup = session("supervisor_demo", "supervisor")
pat = session("patient_demo", "patient")
mo = session("mo_demo", "medical_officer")
check("13a supervisor cannot read document or image", sup.get(f"/api/backend/cases/{case}/documents/{doc_id}").status_code == 404
      and sup.get(f"/api/backend/cases/{case}/documents/{doc_id}/pages/0/image").status_code == 404)
pr, _ = upload(pat, "cbc_low_platelet.png")
check("13b patient cannot upload (ANM only in Phase 5)", pr.status_code == 403)
anon = httpx.Client(base_url=FE).get(f"/api/backend/cases/{case}/documents/{doc_id}/pages/0/image")
check("13c no session -> refused", anon.status_code == 401, f"HTTP {anon.status_code}")
mo_api = mo.get(f"/api/backend/cases/{case}/documents/{doc_id}")
mo_page = mo.get(f"/intake/documents?case={case}")
check("13d MO can read via API; intake page redirects MO (known Phase 8 gap)", mo_api.status_code == 200 and mo_page.status_code in (302, 307, 308), f"page HTTP {mo_page.status_code}")

dl = anm.delete(f"/api/backend/cases/{case}/documents/{d3['document_id']}")
again = anm.delete(f"/api/backend/cases/{case}/documents/{d3['document_id']}")
gone = anm.get(f"/api/backend/cases/{case}/documents/{d3['document_id']}/pages/0/image")
check("14 delete via proxy, idempotent, image gone", dl.status_code == 200 and again.json()["already_deleted"] and gone.status_code == 404)

passed = sum(1 for r in results if r[1] == "PASS")
print("\nSUMMARY", passed, "PASS /", len(results))
sys.exit(0 if passed == len(results) else 1)
