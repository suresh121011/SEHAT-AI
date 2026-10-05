"""Document OCR orchestration — architecture §10 pipeline inside the Phase 4 safety shape (docs/14).

    T1 [txn] case access (creator ANM) → triage consent in effect AND granted under a notice version that
             discloses documents → idempotency → `pending` row + audit (before any engine runs)
       outside any txn, bounded concurrency:
         decode/normalise (PDF in a killable subprocess) → quality check (blur/skew/lighting → "retake")
         → route by document type:
             lab_report         → PaddleOCR (words, boxes, scores) + Surya OCR 2 (table-aware second reading)
             prescription       → Chandra OCR 2 (handwriting, layout blocks) + PaddleOCR (geometry, scores)
             discharge_summary  → both paths (architecture "Mixed → both engines")
         → Gödel verification (verify.py) → RxNorm (medications) → page PNGs written to the local store
    T2 [txn] row still pending and consent unchanged → pages + fields + audit, or discard everything

Guarantees and limits:
- Every engine runs on this machine; no document, image or text leaves it. No silent engine fallback:
  an engine that is disabled or fails is recorded per document and its checks report `not_run`.
- OCR output is a machine reading. It never sets urgency, never calls the rules engine and never submits
  triage. A named reviewer must attest the report and decide each row; until then nothing is "reviewed".
- Original upload bytes are never stored (SHA-256 only); re-encoded page PNGs are stored for evidence.
"""

from __future__ import annotations

import hashlib
import json
import re
import statistics
import threading
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import aiosqlite
import anyio
from pydantic import BaseModel, ConfigDict, Field

from app import audit, consent
from app.auth import Principal
from app.config import Settings
from app.consent_notice import DOCUMENT_NOTICE_VERSIONS
from app.database import read_transaction, transaction
from app.errors import ApiError, not_found
from app.ocr import engine as paddle
from app.ocr import files, quality
from app.ocr.extract import LabCandidate, Region, extract_dates, extract_lab, extract_lab_prose, prose_pairs
from app.ocr.lexicon import QUALITATIVE, UNITS
from app.ocr.parse import ParsedRange, ParsedValue, compare_to_range, parse_range
from app.ocr.rx import MedCandidate, RxLine, extract_rx
from app.ocr.rxnorm import load_index
from app.ocr.surya_parse import TableRow, table_rows
from app.ocr.types import PageOCR, valid_bbox
from app.ocr.verify import Check, Verified, band_for, overall_confidence, verify_lab
from app.rules import reference_ranges

PIPELINE_VERSION = "sehat-ocr-p5-2026-10-01"
DocType = Literal["lab_report", "prescription", "discharge_summary"]
MAX_PAGES = files.MAX_PAGES
BUSY_WAIT_S = 30.0
MAX_WAITERS = 2

_slot = threading.BoundedSemaphore(1)
_waiting = 0
_waiting_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def pending_ttl_s(settings: Settings) -> float:
    """Derived, not chosen: longer than the longest legitimate run, so a slow run is never 'abandoned'."""
    per_page = 2 * settings.ocr_page_timeout_s + 30  # worker call(s) + in-process OCR
    worker_starts = 2 * 60  # up to two worker (re)starts, 60 s each (worker_client.start_timeout_s)
    return MAX_PAGES * per_page + files.PDF_TIMEOUT_S + BUSY_WAIT_S + worker_starts + 60


def _size_bucket(n: int) -> str:
    return "lt_1mb" if n < 1_000_000 else ("1_5mb" if n < 5_000_000 else "5_20mb")


# ── engines (injectable for tests: app.state.ocr_engines) ─────────────────────────────────────────


class Engines:
    """Real engines. Tests replace this with a fake that has the same four methods."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def paddle_page(self, png: bytes, page_index: int) -> PageOCR:
        return paddle.read_page(png, page_index, model_dir=self.settings.ocr_model_dir)

    def paddle_reread(self, png: bytes, bbox, zoom: float):
        return paddle.reread_crop(png, bbox, zoom, model_dir=self.settings.ocr_model_dir)

    def _worker(self):
        from app.config import REPO_ROOT
        from app.ocr.worker_client import get_worker

        return get_worker(self.settings.ocr_worker_python, REPO_ROOT / "backend" / "ocr_worker" / "worker.py",
                          self.settings.ocr_model_dir / "run", self.settings.ocr_model_dir)

    def surya_page(self, png: bytes) -> dict:
        return self._worker().call("/surya/page", png, self.settings.ocr_page_timeout_s)

    def chandra_page(self, png: bytes) -> dict:
        return self._worker().call("/chandra/page", png, self.settings.ocr_page_timeout_s)


def check_available(settings: Settings, document_type: DocType) -> None:
    if not settings.ocr_enabled:
        raise ApiError(404, "FEATURE_DISABLED", "Document reading is not enabled on this server")
    if not paddle.model_installed(settings.ocr_model_dir):
        raise ApiError(503, "OCR_UNAVAILABLE", "The document reading model is not installed", {"reason": "model_not_installed"})
    if document_type in ("prescription", "discharge_summary") and not settings.ocr_chandra_enabled:
        raise ApiError(503, "OCR_UNAVAILABLE", "Handwriting reading (Chandra OCR 2) is not enabled on this server", {"reason": "chandra_not_enabled"})


# ── processing (worker thread; no DB) ─────────────────────────────────────────────────────────────


class _Rejected(Exception):
    def __init__(self, status: str, reasons: list[str], quality_json: list[dict]):
        self.status, self.reasons, self.quality_json = status, reasons, quality_json


def chandra_degenerate(blocks: list[dict]) -> bool:
    """A vision-language reader on a page it cannot read can loop, repeating a few labels and inventing the rest
    (2026-10-02: ~90 copies of "Admission Date :"/"Diagnosis :" plus an invented diagnosis on a 375-px page).
    Such output is never used: ≥10 text blocks with under half distinct, or one text repeated ≥8 times."""
    from collections import Counter

    texts = [re.sub(r"\s+", " ", " ".join(_html_lines(b.get("html") or ""))).strip().lower() for b in blocks]
    texts = [t for t in texts if t]
    if len(texts) < 10:
        return False
    counts = Counter(texts)
    return len(counts) / len(texts) < 0.5 or max(counts.values()) >= 8


def _html_lines(html: str) -> list[str]:
    """Visible text lines of a Chandra/Surya block (tags stripped; <br>, <p>, <li>, <tr> break lines)."""
    t = re.sub(r"(?i)<\s*(br|/p|/li|/tr|/h\d|/div)\s*/?>", "\n", html)
    t = re.sub(r"(?i)</t[dh]>", " ", t)
    t = re.sub(r"<[^>]+>", "", t)
    import html as _h

    return [ln.strip() for ln in _h.unescape(t).split("\n") if ln.strip()]


def _paddle_text_in(page: PageOCR, bbox) -> tuple[str, float | None]:
    """PaddleOCR's reading of whatever lies inside a Chandra block (for cross-checking and word scores)."""
    inside = [ln for ln in page.lines if bbox[0] - 8 <= (ln.bbox[0] + ln.bbox[2]) / 2 <= bbox[2] + 8 and bbox[1] - 8 <= (ln.bbox[1] + ln.bbox[3]) / 2 <= bbox[3] + 8]
    scores = [ln.score for ln in inside if ln.score is not None]
    return " ".join(ln.text for ln in sorted(inside, key=lambda l: (l.bbox[1], l.bbox[0]))), (min(scores) if scores else None)


def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _verify_meds(meds: list[MedCandidate], pages: dict[int, PageOCR], rx_index) -> list[dict]:
    out = []
    for m in meds:
        checks: list[Check] = []
        caps: list[str] = []
        readings = [{"engine": "chandra-ocr-2", "text": m.line_text, "bbox": list(m.bbox), "score": None}]
        paddle_text, score = _paddle_text_in(pages[m.page_index], m.bbox) if m.page_index in pages else ("", None)
        disputed = False
        if paddle_text:
            readings.append({"engine": "paddleocr", "text": paddle_text, "bbox": list(m.bbox), "score": score})
            if m.drug_raw and _squash(m.drug_raw) in _squash(paddle_text):
                checks.append(Check("second_engine_agreement", "pass", "drug_name_agreement"))
            else:
                # e.g. Chandra normalised the printed "Amoxycillin" to "Amoxicillin" (seen 2026-10-01)
                checks.append(Check("second_engine_agreement", "fail", "drug_name_differs"))
                disputed = True
            if m.strength_raw:
                # The dose matters as much as the name: Chandra read a handwritten "800mg" as "500mg" while
                # PaddleOCR read something else (2026-10-02). The strength's digits must appear in the second reading.
                digits = re.sub(r"[^0-9.]", "", m.strength_raw)
                if digits and digits in re.sub(r"[^0-9.]", " ", paddle_text).split():
                    checks.append(Check("second_engine_agreement", "pass", "strength_agreement"))
                else:
                    checks.append(Check("second_engine_agreement", "fail", "strength_differs"))
                    disputed = True
        else:
            checks.append(Check("second_engine_agreement", "not_run", "paddleocr_found_no_text"))
            caps.append("single_engine")
        rx = rx_index.match(m.drug_raw, m.strength_raw) if (rx_index and m.drug_raw) else None
        if rx is None:
            checks.append(Check("rxnorm", "not_run", "rxnorm_index_unavailable" if not rx_index else "no_drug_name"))
        else:
            status = {"matched": "pass", "uncertain": "warn", "unknown": "warn"}[rx.status]
            checks.append(Check("rxnorm", status, f"{rx.status}:{rx.reason}"))
            if rx.status != "matched":
                caps.append("rxnorm_" + rx.status)
        checks.append(Check("maker_voting", "not_run", "phase_6"))
        if disputed:
            caps.append("dispute")
        caps += [f for f in m.flags if f in ("drug_name_missing", "dosage_pattern_missing", "fractional_dose")]
        conf = score  # Chandra gives no per-word scores; PaddleOCR's score inside the block is the only signal
        band = band_for(conf, sorted(set(caps)), not m.drug_raw)
        out.append({
            "candidate": m, "checks": checks, "readings": readings, "caps": sorted(set(caps)), "band": band,
            "field_confidence": conf, "disputed": disputed,
            "rxnorm": None if rx is None else {"status": rx.status, "rxcui": rx.rxcui, "name": rx.name, "tty": rx.tty, "similarity": rx.similarity,
                                                 "strength_match": rx.strength_match, "candidates": [list(c) for c in rx.candidates], "reason": rx.reason,
                                                 "source": "NLM RxNorm Current Prescribable Content", "release": rx_index.release if rx_index else None},
        })
    return out


def run_pipeline(data: bytes, document_type: DocType, settings: Settings, engines) -> dict:
    """Pure processing. Returns pages (with PNGs), fields, dates and engine status. Raises _Rejected for
    quality failures and files.DocumentInvalid / paddle.EngineError for invalid input / engine failure."""
    media, stored = files.normalize(data)
    q = [asdict(quality.assess(_rgb(p.png))) for p in stored]
    bad = sorted({r for item in q for r in item["reasons"]})
    if bad:
        raise _Rejected("quality_rejected", bad, q)
    engines_status: dict[str, str] = {}
    pages: dict[int, PageOCR] = {}
    for p in stored:
        pages[p.index] = engines.paddle_page(p.png, p.index)
    engines_status["paddleocr"] = "ok"
    small = []
    for p in stored:
        heights = [ln.bbox[3] - ln.bbox[1] for ln in pages[p.index].lines]
        if quality.text_size_reason(heights):
            small.append(p.index)
            q[p.index]["ok"], q[p.index]["reasons"] = False, list(q[p.index]["reasons"]) + ["text_too_small"]
        q[p.index]["median_line_px"] = float(statistics.median(heights)) if heights else None
    if small:
        raise _Rejected("quality_rejected", ["text_too_small"], q)  # before the slow engines: they misread or invent here
    png_by_index = {p.index: p.png for p in stored}

    lab_fields: list[Verified] = []
    med_fields: list[dict] = []
    rx_lines: list[RxLine] = []
    chandra_rows: dict[int, list] = {}
    prose_cands: list[LabCandidate] = []
    prose_rows: dict[int, list] = {}
    if document_type in ("prescription", "discharge_summary"):
        chandra_pages: dict[int, list] = {}
        for p in stored:
            try:
                blocks = engines.chandra_page(p.png)["blocks"]
                if not isinstance(blocks, list):
                    raise TypeError
            except (KeyError, TypeError, ValueError):
                raise paddle.EngineError("chandra_bad_response", 500) from None
            chandra_pages[p.index] = blocks
        degenerate = any(chandra_degenerate(b) for b in chandra_pages.values())
        if degenerate and document_type == "prescription":
            raise paddle.EngineError("chandra_degenerate_output", 500)  # the only medication reader: fail, never guess
        if degenerate:
            chandra_pages = {}  # discharge summary: drop every Chandra reading; other engines still run
        for p in stored:
            if p.index not in chandra_pages:
                continue
            blocks = chandra_pages[p.index]
            chandra_rows[p.index] = table_rows(blocks, engine="chandra-ocr-2")
            for bi, b in enumerate(blocks):
                if (b.get("label") or "").lower() in ("page-header", "page-footer", "table"):
                    continue
                bbox = tuple(int(x) for x in b["bbox"])
                block_lines = _html_lines(b.get("html", ""))
                for li, text in enumerate(block_lines):
                    rx_lines.append(RxLine(text, bbox, p.index, f"p{p.index}c{bi}l{li}"))
                if document_type == "discharge_summary":
                    # Investigations written as running text (§10A routes discharge summaries to Chandra). Chandra's
                    # reading is primary; PaddleOCR's text inside the same block is the independent second reading.
                    prose_cands += extract_lab_prose(p.index, " ".join(block_lines), bbox, f"p{p.index}c{bi}", "chandra-ocr-2")
                    paddle_text, paddle_score = _paddle_text_in(pages[p.index], bbox)
                    prose_rows.setdefault(p.index, []).extend(
                        TableRow({"name": name, "value": value, "unit": unit}, bbox, paddle_score, "paddleocr")
                        for _key, name, value, unit in prose_pairs(paddle_text))
        engines_status["chandra"] = "failed:degenerate_output" if degenerate else "ok"
    if document_type in ("lab_report", "discharge_summary"):
        second = None
        if settings.ocr_surya_enabled:
            try:
                for p in stored:
                    pages[p.index].table_rows = table_rows(engines.surya_page(p.png)["blocks"])
                engines_status["surya"] = "ok"
                second = "surya-ocr-2"
            except paddle.EngineError as exc:
                engines_status["surya"] = f"failed:{exc.reason}"
                for p in stored:
                    pages[p.index].table_rows = []
            except (KeyError, TypeError, ValueError):
                engines_status["surya"] = "failed:bad_response"
                for p in stored:
                    pages[p.index].table_rows = []
        else:
            engines_status["surya"] = "disabled"
        # "Mixed → both engines → merge" (architecture §10): Chandra's table readings join as another reading
        for i, rows in chandra_rows.items():
            if rows:
                pages[i].table_rows = list(pages[i].table_rows) + rows
                second = second or "chandra-ocr-2"
        if prose_cands:
            for i, rows in prose_rows.items():
                pages[i].table_rows = list(pages[i].table_rows) + rows
            second = second or "paddleocr"
        cands: list[LabCandidate] = []
        for i in sorted(pages):
            cands += extract_lab(pages[i])
        cands = drop_prose_duplicates(cands, prose_cands) + prose_cands
        lab_fields = verify_lab(
            cands, pages, second_engine=second,
            reread=lambda page_index, bbox, zoom: engines.paddle_reread(png_by_index[page_index], bbox, zoom),
            reference=lambda key, value, comp, unit: reference_ranges.evaluate(key, value, comp, unit),
        )
    if document_type in ("prescription", "discharge_summary"):
        med_fields = _verify_meds(extract_rx(rx_lines), pages, load_index(str(settings.ocr_rxnorm_db)))
    dates = extract_dates([pages[i] for i in sorted(pages)])
    if not lab_fields and not med_fields:
        status = "no_text" if not any(pages[i].lines for i in pages) else "completed"
    else:
        status = "completed"
    return {"media": media, "pages": stored, "quality": q, "lab": lab_fields, "meds": med_fields, "dates": dates,
            "engines": engines_status, "status": status, "page_ocr": pages}


def _rgb(png: bytes):
    import io

    import numpy as np
    from PIL import Image

    return np.asarray(Image.open(io.BytesIO(png)).convert("RGB"))


# ── serialisation of fields ───────────────────────────────────────────────────────────────────────


def _region_dict(r: Region) -> dict:
    return {"role": r.role, "page_index": r.page_index, "bbox": list(r.bbox), "line_id": r.line_id, "granularity": r.granularity}


def _lab_payload(v: Verified) -> dict:
    c = v.candidate
    return {
        "name_raw": c.name_raw, "analyte_key": c.analyte_key,
        "value": {"raw": c.value.raw, "kind": c.value.kind, "value": c.value.value, "comparator": c.value.comparator, "qualitative": c.value.qualitative},
        "unit": {"raw": c.unit.raw, "key": c.unit.key},
        "range": {"raw": c.range.raw, "kind": c.range.kind, "low": c.range.low, "high": c.range.high, "unit_key": c.range.unit_key, "qualitative": c.range.qualitative,
                  "low_inclusive": c.range.low_inclusive, "high_inclusive": c.range.high_inclusive},
        "flag_raw": c.flag_raw, "printed_flag": c.printed_flag, "flags": c.flags, "caps": v.caps,
        "printed_range_status": v.printed_range_status, "reference": v.reference,
    }


def _med_payload(f: dict) -> dict:
    m: MedCandidate = f["candidate"]
    return {"line_raw": m.line_text, "form": m.form, "drug_raw": m.drug_raw, "strength_raw": m.strength_raw, "dosage_pattern": m.dosage_pattern,
            "frequency_raw": m.frequency_raw, "duration_raw": m.duration_raw, "flags": m.flags, "caps": f["caps"], "rxnorm": f["rxnorm"]}


def _check_list(checks: list[Check]) -> list[dict]:
    return [{"check": c.check, "status": c.status, "reason": c.reason} for c in checks]


# ── upload ────────────────────────────────────────────────────────────────────────────────────────


async def _finalize_failed(conn, principal, case_id, document_id, reason, request_id, outcome="failure") -> None:
    cur = await conn.execute("UPDATE ocr_documents SET status = 'failed', failure_code = ?, completed_at = ? WHERE document_id = ? AND status = 'pending'",
                             (reason, _now(), document_id))
    if cur.rowcount:
        await audit.record(conn, principal=principal, action="ocr_document_failed", outcome=outcome, case_id=case_id, request_id=request_id,
                           details=audit.OcrFailedDetails(document_id=document_id, reason_code=reason))


def _age_s(iso: str) -> float:
    return (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds()


def drop_prose_duplicates(table_cands: list[LabCandidate], prose_cands: list[LabCandidate]) -> list[LabCandidate]:
    """A discharge summary's "Name: value" line is read by the prose path (Chandra primary, PaddleOCR's text of the
    same block as the second reading). Without a table header, extract_lab's pattern fallback reads the same line
    again, so the reviewer saw one printed value twice and could confirm or contradict it twice (pre-Phase 9
    walkthrough). Drop only those pattern-fallback duplicates; header-anchored table rows are always kept, and
    PaddleOCR's reading still cross-checks the prose value in verify_lab."""
    prose_keys = {(c.page_index, c.analyte_key) for c in prose_cands}
    return [c for c in table_cands if not ("no_column_header" in c.flags and (c.page_index, c.analyte_key) in prose_keys)]


async def upload(conn: aiosqlite.Connection, principal: Principal, case_id: str, data: bytes, document_type: DocType,
                 idempotency_key: str, settings: Settings, request_id: str | None = None, engines=None) -> dict:
    check_available(settings, document_type)
    sha = hashlib.sha256(data).hexdigest()
    try:
        media = files.sniff(data)
    except files.DocumentInvalid as exc:
        if exc.reason == "empty":  # docs/14 §7: an empty file is an invalid document, not an unsupported type
            raise ApiError(400, "DOCUMENT_INVALID", "The file is empty", {"reason": "empty"}) from None
        raise ApiError(415, "UNSUPPORTED_MEDIA_TYPE", "Send a PNG, JPEG or PDF document", {"reason": exc.reason}) from None
    document_id = str(uuid.uuid4())
    denial = None
    existing = None
    authz_seq = 0
    try:
        async with transaction(conn):
            await consent.load_case(conn, principal, case_id, "write")
            snap = await consent.require(conn, case_id, "triage")
            authz_seq = snap.authz_seq
            if await consent.granted_notice_version(conn, case_id, "triage") not in DOCUMENT_NOTICE_VERSIONS:
                existing = "notice"
            else:
                async with conn.execute("SELECT * FROM ocr_documents WHERE case_id = ? AND idempotency_key = ?", (case_id, idempotency_key)) as cur:
                    existing = await cur.fetchone()
                if existing is None:
                    await conn.execute(
                        "INSERT INTO ocr_documents (document_id, case_id, created_by, idempotency_key, upload_sha256, media_type, document_type, byte_size, status, "
                        "pipeline_version, consent_seq, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)",
                        (document_id, case_id, principal.user_id, idempotency_key, sha, media, document_type, len(data), PIPELINE_VERSION, authz_seq, _now()),
                    )
                    await audit.record(conn, principal=principal, action="ocr_document_started", outcome="success", case_id=case_id, request_id=request_id,
                                       details=audit.OcrStartedDetails(document_id=document_id, document_type=document_type, media_type=media,
                                                                       size_bucket=_size_bucket(len(data))))  # type: ignore[arg-type]
                elif existing["status"] == "pending" and _age_s(existing["created_at"]) > pending_ttl_s(settings):
                    await _finalize_failed(conn, principal, case_id, existing["document_id"], "abandoned", request_id)
    except consent.ConsentNotEffective as exc:
        denial = exc
    if denial is not None:
        raise await consent.audit_denied(conn, principal, case_id, denial, request_id)
    if existing == "notice":
        raise ApiError(403, "CONSENT_NOTICE_UPDATE_REQUIRED", "Consent must be recorded again with the current notice, which explains document upload",
                       {"reason": "document_notice_not_accepted"})
    if existing is not None:
        if existing["created_by"] != principal.user_id or existing["upload_sha256"] != sha or existing["document_type"] != document_type:
            raise ApiError(409, "IDEMPOTENCY_CONFLICT", "This request key was already used for a different document")
        view = await document_view(conn, existing["document_id"])
        if view["status"] == "pending":
            raise ApiError(409, "IN_PROGRESS", "This document is still being read")
        return view
    try:
        return await _process(conn, principal, case_id, data, document_type, settings, request_id, engines or Engines(settings), document_id, authz_seq)
    except BaseException:
        with anyio.CancelScope(shield=True):
            try:
                async with transaction(conn):
                    await _finalize_failed(conn, principal, case_id, document_id, "interrupted", request_id)
            except Exception:  # noqa: BLE001 - best effort; the TTL sweep covers what this cannot
                pass
            # A cancelled request may still have let the worker thread write page files: remove them now,
            # unless the row was committed as completed (then they are its evidence).
            try:
                row = await (await conn.execute("SELECT status FROM ocr_documents WHERE document_id = ?", (document_id,))).fetchone()
                if row is None or row[0] != "completed":
                    for f in (settings.ocr_document_dir / case_id).glob(f"{document_id}-*.png"):
                        f.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass
        raise


def _log_internal(exc: BaseException) -> None:
    """Make internal failures diagnosable without patient data: the exception class and the innermost app
    code location only — never the message or arguments, which can contain document text or values."""
    import logging
    import traceback

    app = [f for f in traceback.extract_tb(exc.__traceback__) if "/app/" in f.filename]
    where = f"{Path(app[-1].filename).name}:{app[-1].lineno} in {app[-1].name}" if app else "unknown"
    logging.getLogger("sehat.ocr").error("ocr_pipeline_error type=%s at=%s", type(exc).__name__, where)


def _acquire_slot() -> None:
    global _waiting
    with _waiting_lock:
        if _waiting >= MAX_WAITERS:
            raise paddle.EngineError("ocr_busy")
        _waiting += 1
    try:
        if not _slot.acquire(timeout=BUSY_WAIT_S):
            raise paddle.EngineError("ocr_busy")
    finally:
        with _waiting_lock:
            _waiting -= 1


def _run_bounded(data, document_type, settings, engines):
    _acquire_slot()
    try:
        return run_pipeline(data, document_type, settings, engines)
    finally:
        _slot.release()


def _write_pages(settings: Settings, case_id: str, document_id: str, pages) -> list[tuple[Any, str]]:
    import os

    root = settings.ocr_document_dir
    folder = root / case_id
    folder.mkdir(parents=True, exist_ok=True)
    for d in (root, folder):
        os.chmod(d, 0o700)  # page images carry printed names/IDs: owner-only
    written: list[tuple[Any, str]] = []
    try:
        for p in pages:
            rel = f"{case_id}/{document_id}-{p.index}.png"
            fd = os.open(root / rel, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(p.png)
            written.append((p, rel))
    except OSError:
        _remove(settings, written)  # never leave a partial set of page files behind
        raise
    return written


def _remove(settings: Settings, written) -> None:
    for _, rel in written:
        (settings.ocr_document_dir / rel).unlink(missing_ok=True)


async def _process(conn, principal, case_id, data, document_type, settings, request_id, engines, document_id, authz_seq) -> dict:
    result = None
    failure: tuple[str, int] | None = None
    rejected: _Rejected | None = None
    try:
        result = await anyio.to_thread.run_sync(lambda: _run_bounded(data, document_type, settings, engines))
    except files.DocumentInvalid as exc:
        failure = (exc.reason, 400)
    except _Rejected as exc:
        rejected = exc
    except paddle.EngineError as exc:
        failure = (exc.reason, exc.status)
    except Exception as exc:  # noqa: BLE001 - never leave the row pending
        failure = ("internal_error", 500)
        _log_internal(exc)

    written = []
    if result is not None:
        try:
            written = await anyio.to_thread.run_sync(lambda: _write_pages(settings, case_id, document_id, result["pages"]))
        except OSError:
            result, failure = None, ("storage_failed", 500)
    consent_changed = abandoned = False
    try:
        async with transaction(conn):
            snap = await consent.snapshot(conn, case_id)
            consent_changed = snap.authz_seq != authz_seq or not snap.is_effective("triage")
            row = await (await conn.execute("SELECT status FROM ocr_documents WHERE document_id = ?", (document_id,))).fetchone()
            if row is None or row[0] != "pending":
                abandoned = True
            elif consent_changed or failure is not None:
                await _finalize_failed(conn, principal, case_id, document_id, "consent_changed" if consent_changed else failure[0], request_id,  # type: ignore[index]
                                       outcome="denied" if consent_changed else "failure")
            elif rejected is not None:
                await conn.execute("UPDATE ocr_documents SET status = 'quality_rejected', failure_code = ?, quality_json = ?, completed_at = ? WHERE document_id = ?",
                                   ("retake_needed", json.dumps(rejected.quality_json), _now(), document_id))
                await audit.record(conn, principal=principal, action="ocr_document_processed", outcome="success", case_id=case_id, request_id=request_id,
                                   details=audit.OcrProcessedDetails(document_id=document_id, document_type=document_type, status="quality_rejected",
                                                                     page_count=len(rejected.quality_json), field_count=0, disputed_count=0, engines=[]))
            else:
                await _store(conn, principal, case_id, document_id, document_type, result, written, request_id)
    except BaseException:
        _remove(settings, written)
        raise
    if abandoned or consent_changed or failure is not None:
        _remove(settings, written)
    if abandoned:
        raise ApiError(409, "DOCUMENT_ABANDONED", "This document took too long and was closed; please upload it again")
    if consent_changed:
        raise ApiError(409, "CONSENT_WITHDRAWN", "Consent changed while the document was being read; the result was discarded")
    if failure is not None:
        reason, status = failure
        if status == 400:
            raise ApiError(400, "DOCUMENT_INVALID", "The document could not be read as a valid PNG, JPEG or PDF", {"reason": reason})
        if reason == "ocr_busy":
            raise ApiError(503, "OCR_BUSY", "Document reading is busy; try again in a minute", {"reason": reason})
        if reason == "ocr_timeout":
            raise ApiError(504, "OCR_TIMEOUT", "Reading the document took too long and was stopped", {"reason": reason})
        raise ApiError(503 if status == 503 else 500, "OCR_UNAVAILABLE", "Document reading failed; no values were produced", {"reason": reason})
    if rejected is not None:
        raise ApiError(422, "DOCUMENT_QUALITY_LOW", "The photo is not clear enough to read. Please retake it.",
                       {"reasons": rejected.reasons, "document_id": document_id})
    return await document_view(conn, document_id)


async def _store(conn, principal, case_id, document_id, document_type, result, written, request_id) -> None:
    pages = result["pages"]
    for p, rel in written:
        await conn.execute("INSERT INTO ocr_pages (document_id, page_index, file_ref, png_sha256, width, height, transform_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                           (document_id, p.index, rel, p.sha256, p.width, p.height, json.dumps(p.transform)))
    dims = {p.index: (p.width, p.height) for p in pages}
    ordinal = 0
    disputed = 0
    all_conf = []
    for v in result["lab"]:
        regs = [_region_dict(r) for r in v.candidate.regions if valid_bbox(r.bbox, *dims[r.page_index])]
        await conn.execute(
            "INSERT INTO ocr_fields (field_id, document_id, case_id, ordinal, kind, page_index, payload_json, regions_json, readings_json, checks_json, band, field_confidence, disputed, created_at) "
            "VALUES (?, ?, ?, ?, 'lab', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), document_id, case_id, ordinal, v.candidate.page_index, json.dumps(_lab_payload(v)), json.dumps(regs),
             json.dumps([{"engine": r.engine, "text": r.text, "bbox": list(r.bbox) if r.bbox else None, "score": r.score} for r in v.readings]),
             json.dumps(_check_list(v.checks)), v.band, v.field_confidence, int(v.disputed), _now()),
        )
        ordinal += 1
        disputed += int(v.disputed)
        all_conf.append(v.field_confidence)
    for f in result["meds"]:
        m: MedCandidate = f["candidate"]
        regs = [{"role": "line", "page_index": m.page_index, "bbox": list(m.bbox), "line_id": m.line_id, "granularity": "block"}] if valid_bbox(m.bbox, *dims[m.page_index]) else []
        await conn.execute(
            "INSERT INTO ocr_fields (field_id, document_id, case_id, ordinal, kind, page_index, payload_json, regions_json, readings_json, checks_json, band, field_confidence, disputed, created_at) "
            "VALUES (?, ?, ?, ?, 'medication', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), document_id, case_id, ordinal, m.page_index, json.dumps(_med_payload(f)), json.dumps(regs),
             json.dumps(f["readings"]), json.dumps(_check_list(f["checks"])), f["band"], f["field_confidence"], int(f["disputed"]), _now()),
        )
        ordinal += 1
        disputed += int(f["disputed"])
        all_conf.append(f["field_confidence"])
    dates = result["dates"]
    confs = [c for c in all_conf if c is not None]
    await conn.execute(
        "UPDATE ocr_documents SET status = ?, page_count = ?, quality_json = ?, engines_json = ?, overall_confidence = ?, dates_json = ?, completed_at = ? WHERE document_id = ?",
        (result["status"], len(pages), json.dumps(result["quality"]), json.dumps(result["engines"]), min(confs) if confs else None,
         json.dumps({"collected_date": dates.collected_date, "report_date": dates.report_date, "regions": [_region_dict(r) for r in dates.regions]}), _now(), document_id),
    )
    engines_used = [e for e in ("paddleocr", "surya", "chandra") if result["engines"].get(e) == "ok"]
    await audit.record(conn, principal=principal, action="ocr_document_processed", outcome="success", case_id=case_id, request_id=request_id,
                       details=audit.OcrProcessedDetails(document_id=document_id, document_type=document_type, status=result["status"],
                                                         page_count=len(pages), field_count=ordinal, disputed_count=disputed, engines=engines_used))  # type: ignore[arg-type]


# ── views ─────────────────────────────────────────────────────────────────────────────────────────


def regions_hash(regions: list[dict]) -> str:
    canon = json.dumps(sorted(([r["role"], r["page_index"], list(r["bbox"])] for r in regions)), separators=(",", ":"))
    return hashlib.sha256(canon.encode()).hexdigest()


async def _latest_reviews(conn, document_id: str) -> dict[str, aiosqlite.Row]:
    async with conn.execute(
        "SELECT e.* FROM ocr_review_events e JOIN (SELECT r.field_id, max(r.seq) AS seq FROM ocr_review_events r JOIN ocr_fields f ON f.field_id = r.field_id "
        "WHERE f.document_id = ? GROUP BY r.field_id) m ON e.seq = m.seq", (document_id,),
    ) as cur:
        return {r["field_id"]: r for r in await cur.fetchall()}


async def _latest_attestation(conn, document_id: str):
    async with conn.execute("SELECT * FROM ocr_attestation_events WHERE document_id = ? ORDER BY seq DESC LIMIT 1", (document_id,)) as cur:
        return await cur.fetchone()


_OUTCOME_STATE = {None: "machine_read", "confirmed": "confirmed", "corrected": "corrected", "unsure": "unsure", "rejected": "rejected"}


def _reviewed_values(kind: str, payload: dict, review) -> dict | None:
    """The value a reviewer accepted (confirmed = the machine reading; corrected = the reviewer's entry)."""
    if review is None or review["outcome"] not in ("confirmed", "corrected"):
        return None
    if kind == "medication":
        base = {k: payload[k] for k in ("drug_raw", "strength_raw", "dosage_pattern", "frequency_raw", "duration_raw")}
        if review["outcome"] == "corrected":
            base.update({k: v for k, v in json.loads(review["corrected_json"]).items() if v is not None})
        return base
    val = {"value": payload["value"]["value"], "comparator": payload["value"]["comparator"], "qualitative": payload["value"]["qualitative"],
           "unit": payload["unit"]["key"], "range": payload["range"], "flag": payload["printed_flag"]}
    if review["outcome"] == "corrected":
        corr = json.loads(review["corrected_json"])
        if corr.get("result") is not None:
            val.update({"value": corr["result"].get("value"), "comparator": corr["result"].get("comparator"), "qualitative": corr["result"].get("qualitative")})
        if corr.get("unit") is not None:
            val["unit"] = None if corr["unit"] == "none" else corr["unit"]
        if corr.get("range") is not None:
            r = corr["range"]
            val["range"] = {"raw": "(reviewer)", "kind": "none" if r["kind"] == "none" else r["kind"], "low": r.get("low"), "high": r.get("high"),
                            "unit_key": None, "qualitative": None, "low_inclusive": r.get("low_inclusive", True), "high_inclusive": r.get("high_inclusive", True)}
        if corr.get("flag") is not None:
            val["flag"] = None if corr["flag"] == "none" else corr["flag"]
    return val


def _range_from(d: dict) -> ParsedRange:
    return ParsedRange(d.get("raw", ""), d.get("kind", "none"), d.get("low"), d.get("high"), d.get("low_inclusive", True), d.get("high_inclusive", True),
                       d.get("unit_key"), d.get("qualitative"))


def _status_on_reviewed(analyte: str | None, rv: dict) -> dict:
    pv = ParsedValue(rv.get("value") or rv.get("qualitative") or "", "qualitative" if rv.get("qualitative") else ("numeric" if rv.get("value") else "empty"),
                     rv.get("value"), rv.get("comparator"), rv.get("qualitative"))
    printed = compare_to_range(pv, rv.get("unit"), _range_from(rv["range"]))
    ref = reference_ranges.evaluate(analyte, rv.get("value"), rv.get("comparator"), rv.get("unit")) if analyte else None
    return {"printed_range_status": printed, "reference": ref}


def _field_view(row, review, png_sha: dict[int, str]) -> dict:
    payload = json.loads(row["payload_json"])
    regions = json.loads(row["regions_json"])
    reviewed = _reviewed_values(row["kind"], payload, review)
    out = {
        "field_id": row["field_id"], "kind": row["kind"], "ordinal": row["ordinal"], "page_index": row["page_index"], **payload,
        "regions": regions, "regions_sha256": regions_hash(regions), "page_png_sha256": png_sha.get(row["page_index"]),
        "readings": json.loads(row["readings_json"]), "checks": json.loads(row["checks_json"]),
        # Automatic checks describe the MACHINE reading only. A reviewer correction is not re-verified by them.
        "checks_apply_to": "machine_reading",
        "band": row["band"], "field_confidence": row["field_confidence"], "confidence_note": "engine score, not a probability that the value is correct",
        "disputed": bool(row["disputed"]),
        "review_status": _OUTCOME_STATE[review["outcome"] if review else None],
        "review": None if review is None else {"event_id": review["event_id"], "outcome": review["outcome"], "actor_role": review["actor_role"], "at": review["created_at"],
                                               "corrected": json.loads(review["corrected_json"]) if review["corrected_json"] else None},
        "reviewed_value": reviewed,
        "can_confirm": _can_confirm(row, payload, regions),
    }
    if reviewed is not None and row["kind"] == "lab":
        out["reviewed_ranges"] = _status_on_reviewed(payload.get("analyte_key"), reviewed)
    return out


def _can_confirm(row, payload: dict, regions: list[dict]) -> bool:
    if row["band"] == "human_entry" or row["disputed"]:
        return False
    if row["kind"] == "medication":
        return bool(regions) and bool(payload.get("drug_raw"))
    roles_present = {r["role"] for r in regions}
    needed = {"name", "value"} | ({"unit"} if payload["unit"]["raw"] else set()) | ({"range"} if payload["range"]["raw"] else set()) | ({"flag"} if payload["flag_raw"] else set())
    return needed <= roles_present


def _docs06_block(doc: dict) -> dict:
    """docs/06 §3.3 IntakePayload fields, derived from the source-linked fields (additive, for compatibility)."""
    labs = [f for f in doc["fields"] if f["kind"] == "lab"]
    return {
        "input_type": "document", "document_type": doc["document_type"], "ocr_engine": "+".join(e for e, s in doc["engines"].items() if s == "ok"),
        "extracted_values": [{
            "field_id": f["field_id"], "field_name": f["analyte_key"] or f["name_raw"], "value": f["value"]["raw"], "unit": f["unit"]["raw"],
            "reference_range": f["range"]["raw"],
            "out_of_range": f["printed_range_status"] in ("below_range", "above_range") or (f["reference"] or {}).get("status") in ("below_range", "above_range"),
            "out_of_range_basis": [b for b, st in (("printed", f["printed_range_status"]), ("sourced", (f["reference"] or {}).get("status"))) if st in ("below_range", "above_range")],
            "confidence": f["field_confidence"], "bbox": next((r["bbox"] for r in f["regions"] if r["role"] == "value"), None), "page": f["page_index"],
            "snomed_code": None, "status": f["review_status"],
        } for f in labs],
        "godel_verification": {
            "overall_confidence": doc["overall_confidence"],
            "disputed_values": [f["field_id"] for f in doc["fields"] if f["disputed"]],
            "rxnorm_matches": [{"field_id": f["field_id"], **(f["rxnorm"] or {})} for f in doc["fields"] if f["kind"] == "medication"],
            "reference_range_flags": [f"{f['analyte_key'] or f['name_raw']}_{st}_{basis}" for f in labs
                                      for basis, st in (("printed", f["printed_range_status"]), ("sourced", (f["reference"] or {}).get("status")))
                                      if st in ("below_range", "above_range")],
        },
        "source_ref": {"type": "ocr", "file_ref": f"documents/{doc['document_id']}"},
    }


async def _purge_event(conn, document_id: str):
    async with conn.execute("SELECT * FROM ocr_purge_events WHERE document_id = ?", (document_id,)) as cur:
        return await cur.fetchone()


async def document_view(conn, document_id: str) -> dict:
    async with conn.execute("SELECT * FROM ocr_documents WHERE document_id = ?", (document_id,)) as cur:
        d = await cur.fetchone()
    if d is None:
        raise not_found()
    purge = await _purge_event(conn, document_id)
    if purge is not None:  # deleted: identity and the deletion record only, never content
        return {"document_id": d["document_id"], "case_id": d["case_id"], "status": "deleted", "document_type": d["document_type"],
                "deleted": {"reason": purge["reason"], "at": purge["created_at"], "by_role": purge["actor_role"]},
                "pages": [], "fields": [], "attestation": None, "created_at": d["created_at"], "engines": {}, "dates": None,
                "overall_confidence": None, "media_type": d["media_type"], "page_count": 0, "quality": None,
                "status_note": "Deleted: page images, read text, values and review decisions were removed."}
    async with conn.execute("SELECT * FROM ocr_pages WHERE document_id = ? ORDER BY page_index", (document_id,)) as cur:
        pages = await cur.fetchall()
    async with conn.execute("SELECT * FROM ocr_fields WHERE document_id = ? ORDER BY ordinal", (document_id,)) as cur:
        rows = await cur.fetchall()
    reviews = await _latest_reviews(conn, document_id)
    att = await _latest_attestation(conn, document_id)
    png_sha = {p["page_index"]: p["png_sha256"] for p in pages}
    fields = [_field_view(r, reviews.get(r["field_id"]), png_sha) for r in rows]
    doc = {
        "document_id": d["document_id"], "case_id": d["case_id"], "status": d["status"], "failure_code": d["failure_code"],
        "document_type": d["document_type"], "media_type": d["media_type"], "page_count": d["page_count"],
        "pipeline_version": d["pipeline_version"], "engines": json.loads(d["engines_json"]) if d["engines_json"] else {},
        "quality": json.loads(d["quality_json"]) if d["quality_json"] else None,
        "overall_confidence": d["overall_confidence"],
        "dates": json.loads(d["dates_json"]) if d["dates_json"] else None,
        "pages": [{"page_index": p["page_index"], "width": p["width"], "height": p["height"], "png_sha256": p["png_sha256"],
                   "transform": json.loads(p["transform_json"]), "coordinate_frame": "pixels on this page image; origin top-left; x1/y1 exclusive"} for p in pages],
        "attestation": None if att is None else {"event_id": att["event_id"], "answer": att["answer"], "actor_role": att["actor_role"], "at": att["created_at"]},
        "fields": fields,
        "status_note": "Machine-read and auto-checked — not yet confirmed. Values never change triage or urgency.",
        "created_at": d["created_at"], "completed_at": d["completed_at"],
    }
    doc.update(_docs06_block(doc))
    return doc


async def _require_read(conn, principal, case_id: str, request_id) -> None:
    """Document content is visible to the case creator or a medical officer (not supervisors), and only
    while triage consent is in effect."""
    try:
        await consent.load_case(conn, principal, case_id, "write")
    except ApiError:
        await consent.load_case(conn, principal, case_id, "triage")
    snap = await consent.snapshot(conn, case_id)
    if not snap.is_effective("triage"):
        state = snap.effective("triage")
        raise await consent.audit_denied(conn, principal, case_id, consent.ConsentNotEffective("triage", "not_provided" if state == "granted" else state), request_id)


async def _consent_still_effective(conn, case_id: str) -> None:
    """Re-check inside the read snapshot that serves the content, so a withdrawal between the access check
    and the read cannot return content."""
    if not (await consent.snapshot(conn, case_id)).is_effective("triage"):
        raise ApiError(409, "CONSENT_WITHDRAWN", "Consent changed; reload")


async def list_documents(conn, principal, case_id: str, request_id=None) -> list[dict]:
    await _require_read(conn, principal, case_id, request_id)
    async with read_transaction(conn):
        await _consent_still_effective(conn, case_id)
        async with conn.execute("SELECT document_id FROM ocr_documents WHERE case_id = ? ORDER BY created_at", (case_id,)) as cur:
            ids = [r["document_id"] for r in await cur.fetchall()]
        return [await document_view(conn, i) for i in ids]


async def get_document(conn, principal, case_id: str, document_id: str, request_id=None) -> dict:
    await _require_read(conn, principal, case_id, request_id)
    async with read_transaction(conn):
        await _consent_still_effective(conn, case_id)
        view = await document_view(conn, document_id)
    if view["case_id"] != case_id:
        raise not_found()
    return view


async def page_image(conn, principal, case_id: str, document_id: str, page_index: int, settings: Settings, request_id=None) -> bytes:
    await _require_read(conn, principal, case_id, request_id)
    async with read_transaction(conn):
        await _consent_still_effective(conn, case_id)
        async with conn.execute(
            "SELECT p.* FROM ocr_pages p JOIN ocr_documents d USING (document_id) WHERE p.document_id = ? AND p.page_index = ? AND d.case_id = ?",
            (document_id, page_index, case_id),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise not_found()
        path = (settings.ocr_document_dir / row["file_ref"]).resolve()
        if settings.ocr_document_dir.resolve() not in path.parents or not path.is_file():
            raise not_found()
        data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != row["png_sha256"]:
        raise ApiError(409, "EVIDENCE_CHANGED", "The stored page image does not match its recorded hash")
    return data


# ── reviewer decisions ────────────────────────────────────────────────────────────────────────────


class AttestationBody(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    answer: Literal["matches", "does_not_match", "unsure"]
    supersedes: uuid.UUID | None = None


_DEC = r"^\d{1,7}(\.\d{1,4})?$"
UnitKey = Literal[tuple(list(UNITS) + ["none"])]  # type: ignore[valid-type]
QualKey = Literal[tuple(sorted(set(QUALITATIVE.values())))]  # type: ignore[valid-type]


class ResultCorrection(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    value: str | None = Field(default=None, pattern=_DEC, max_length=12)
    # "=" states explicitly that there is no "<"/">" sign; required when the machine read one (no silent drop)
    comparator: Literal["<", "<=", ">", ">=", "="] | None = None
    qualitative: QualKey | None = None  # type: ignore[valid-type]


class RangeCorrection(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    kind: Literal["between", "upper", "lower", "none"]
    low: str | None = Field(default=None, pattern=_DEC, max_length=12)
    high: str | None = Field(default=None, pattern=_DEC, max_length=12)
    high_inclusive: bool = True  # False for a printed "< x"
    low_inclusive: bool = True  # False for a printed "> x"


class Correction(BaseModel):
    """Structured only — no free text. Test names are never edited (a wrong name means: reject the row)."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    result: ResultCorrection | None = None
    unit: UnitKey | None = None  # type: ignore[valid-type]
    range: RangeCorrection | None = None
    flag: Literal["none", "H", "L"] | None = None
    # medications
    strength_raw: str | None = Field(default=None, pattern=r"^\d{1,5}(\.\d{1,3})?\s?(mg|mcg|g|ml|iu|%)$", max_length=16)
    dosage_pattern: str | None = Field(default=None, pattern=r"^[0-2½](-[0-2½]){2,3}$", max_length=12)


class ReviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    outcome: Literal["confirmed", "corrected", "rejected", "unsure"]
    correction: Correction | None = None
    supersedes: uuid.UUID | None = None
    shown_png_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    shown_regions_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


async def _gate_reviewer(conn, principal, case_id: str):
    await consent.load_case(conn, principal, case_id, "triage")
    await consent.require(conn, case_id, "triage")


async def attest(conn, principal: Principal, case_id: str, document_id: str, body: AttestationBody, request_id=None) -> dict:
    denial = None
    try:
        async with transaction(conn):
            await _gate_reviewer(conn, principal, case_id)
            async with conn.execute("SELECT status FROM ocr_documents WHERE document_id = ? AND case_id = ?", (document_id, case_id)) as cur:
                d = await cur.fetchone()
            if d is None or d["status"] != "completed" or await _purge_event(conn, document_id) is not None:
                raise not_found()  # deleted documents cannot be attested
            latest = await _latest_attestation(conn, document_id)
            if (latest["event_id"] if latest else None) != (str(body.supersedes) if body.supersedes else None):
                raise ApiError(409, "STALE_DECISION", "Someone else answered this in the meantime; reload and check again")
            await conn.execute("INSERT INTO ocr_attestation_events (event_id, document_id, case_id, answer, actor_id, actor_role, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                               (str(uuid.uuid4()), document_id, case_id, body.answer, principal.user_id, principal.role.value, _now()))
            await audit.record(conn, principal=principal, action="ocr_attestation_recorded", outcome="success", case_id=case_id, request_id=request_id,
                               details=audit.OcrAttestationDetails(document_id=document_id, answer=body.answer))
    except consent.ConsentNotEffective as exc:
        denial = exc
    if denial is not None:
        raise await consent.audit_denied(conn, principal, case_id, denial, request_id)
    return await document_view(conn, document_id)


async def review(conn, principal: Principal, case_id: str, field_id: str, body: ReviewBody, request_id=None) -> dict:
    denial = None
    document_id = None
    try:
        async with transaction(conn):
            await _gate_reviewer(conn, principal, case_id)
            async with conn.execute(
                "SELECT f.*, d.status AS d_status FROM ocr_fields f JOIN ocr_documents d USING (document_id) WHERE f.field_id = ? AND f.case_id = ?", (field_id, case_id),
            ) as cur:
                row = await cur.fetchone()
            if row is None or row["d_status"] != "completed":
                raise not_found()
            document_id = row["document_id"]
            async with conn.execute("SELECT event_id FROM ocr_review_events WHERE field_id = ? ORDER BY seq DESC LIMIT 1", (field_id,)) as cur:
                latest = await cur.fetchone()
            if (latest["event_id"] if latest else None) != (str(body.supersedes) if body.supersedes else None):
                raise ApiError(409, "STALE_DECISION", "This value was decided by someone else in the meantime; reload and review again")
            async with conn.execute("SELECT png_sha256 FROM ocr_pages WHERE document_id = ? AND page_index = ?", (document_id, row["page_index"])) as cur:
                page = await cur.fetchone()
            regions = json.loads(row["regions_json"])
            if page is None or body.shown_png_sha256 != page["png_sha256"] or body.shown_regions_sha256 != regions_hash(regions):
                raise ApiError(409, "STALE_DECISION", "The evidence on screen does not match the stored evidence; reload")
            payload = json.loads(row["payload_json"])
            if body.outcome in ("confirmed", "corrected"):
                att = await _latest_attestation(conn, document_id)
                if att is None or att["answer"] != "matches":
                    raise ApiError(409, "ATTESTATION_REQUIRED", "First confirm that this report belongs to this patient and visit")
            corrected = None
            if body.outcome == "confirmed":
                if body.correction is not None:
                    raise ApiError(400, "VALIDATION_ERROR", "A confirmation carries no values")
                if not _can_confirm(row, payload, regions):
                    raise ApiError(409, "CORRECTION_REQUIRED", "This value cannot be confirmed as read; type it from the paper, mark it unsure, or reject it")
            elif body.outcome == "corrected":
                corrected = _validate_correction(row, payload, regions, body.correction)
            elif body.correction is not None:
                raise ApiError(400, "VALIDATION_ERROR", "Rejected or unsure decisions carry no values")
            await conn.execute(
                "INSERT INTO ocr_review_events (event_id, field_id, case_id, outcome, corrected_json, shown_png_sha256, shown_regions_sha256, actor_id, actor_role, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), field_id, case_id, body.outcome, json.dumps(corrected) if corrected is not None else None, body.shown_png_sha256,
                 body.shown_regions_sha256, principal.user_id, principal.role.value, _now()),
            )
            await audit.record(conn, principal=principal, action="ocr_review_resolved", outcome="success", case_id=case_id, request_id=request_id,
                               details=audit.OcrReviewDetails(field_id=field_id, kind=row["kind"], outcome=body.outcome))
    except consent.ConsentNotEffective as exc:
        denial = exc
    if denial is not None:
        raise await consent.audit_denied(conn, principal, case_id, denial, request_id)
    view = await document_view(conn, document_id)  # type: ignore[arg-type]
    return next(f for f in view["fields"] if f["field_id"] == field_id)


def _validate_correction(row, payload: dict, regions: list[dict], corr: Correction | None) -> dict:
    if corr is None:
        raise ApiError(400, "CORRECTION_INVALID", "A correction needs at least one corrected value", {"reason": "empty"})
    data = corr.model_dump(exclude_none=True)
    if not data:
        raise ApiError(400, "CORRECTION_INVALID", "A correction needs at least one corrected value", {"reason": "empty"})
    if row["kind"] == "medication":
        if set(data) - {"strength_raw", "dosage_pattern"}:
            raise ApiError(400, "CORRECTION_INVALID", "Only strength and dose pattern can be corrected; reject a wrong drug name", {"reason": "field_not_correctable"})
        if row["disputed"] or row["band"] == "human_entry":
            raise ApiError(409, "CORRECTION_REQUIRED", "The drug name itself is uncertain; reject or mark unsure", {"reason": "drug_name_uncertain"})
        return data
    if set(data) & {"strength_raw", "dosage_pattern"}:
        raise ApiError(400, "CORRECTION_INVALID", "Not a lab-value field", {"reason": "field_not_correctable"})
    r = data.get("result")
    if r is not None:
        if (r.get("value") is None) == (r.get("qualitative") is None) or (r.get("comparator") and r.get("qualitative")):
            raise ApiError(400, "CORRECTION_INVALID", "Give either a number (with optional comparator) or a qualitative result", {"reason": "result_shape"})
        if r.get("value") is not None and payload["value"].get("comparator") and not r.get("comparator"):
            raise ApiError(400, "CORRECTION_INVALID", "The report shows a < or > sign: keep it, or choose '=' to say there is none", {"reason": "comparator_required"})
        if r.get("comparator") == "=":
            r["comparator"] = None
    rng = data.get("range")
    if rng is not None:
        k, lo, hi = rng["kind"], rng.get("low"), rng.get("high")
        ok = (k == "none" and lo is None and hi is None) or (k == "between" and lo and hi and float(lo) < float(hi)) or (k == "upper" and hi and not lo) or (k == "lower" and lo and not hi)
        if not ok:
            raise ApiError(400, "CORRECTION_INVALID", "The corrected range is not consistent", {"reason": "range_shape"})
    # A value nobody could see cannot ride along: fields not corrected must have a valid region.
    roles = {rr["role"] for rr in regions}
    must = []
    if r is None:
        if row["band"] == "human_entry" or row["disputed"] or "value" not in roles:
            raise ApiError(409, "CORRECTION_REQUIRED", "This row's value must be typed from the paper", {"reason": "result_required"})
    if "name" not in roles:
        raise ApiError(409, "CORRECTION_REQUIRED", "The test name cannot be shown on the image; reject the row and enter it on the triage form", {"reason": "unseen_name"})
    if "unit" not in data and payload["unit"]["raw"] and "unit" not in roles:
        must.append("unit")
    if "flag" not in data and payload["flag_raw"] and "flag" not in roles:
        must.append("flag")
    if "range" not in data and payload["range"]["raw"] and "range" not in roles:
        must.append("range")
    if must:
        raise ApiError(409, "CORRECTION_REQUIRED", "Some printed parts of this row cannot be shown; type them from the paper", {"reason": "unseen_" + "_".join(must)})
    return data


# ── reviewed values (view/export only; never a triage input) ──────────────────────────────────────


async def reviewed(conn, principal: Principal, case_id: str, request_id=None) -> dict:
    denial = None
    try:
        async with transaction(conn):
            await _gate_reviewer(conn, principal, case_id)
    except consent.ConsentNotEffective as exc:
        denial = exc
    if denial is not None:
        raise await consent.audit_denied(conn, principal, case_id, denial, request_id)
    out = []
    unresolved = []
    async with read_transaction(conn):
        await _consent_still_effective(conn, case_id)
        async with conn.execute("SELECT document_id FROM ocr_documents WHERE case_id = ? AND status = 'completed' ORDER BY created_at", (case_id,)) as cur:
            doc_ids = [r["document_id"] for r in await cur.fetchall()]
        views = [await document_view(conn, d) for d in doc_ids]
    for doc in views:
        if doc["status"] == "deleted":
            continue
        att = doc["attestation"]
        if att is None or att["answer"] != "matches":
            unresolved.append({"document_id": doc["document_id"], "state": "attestation_" + (att["answer"] if att else "missing")})
            continue
        for f in doc["fields"]:
            if f["reviewed_value"] is None:
                if f["review_status"] in ("machine_read", "unsure"):
                    unresolved.append({"document_id": doc["document_id"], "field_id": f["field_id"], "state": f["review_status"]})
                continue
            out.append({
                "field_id": f["field_id"], "kind": f["kind"], "name": f.get("analyte_key") or f.get("name_raw") or f.get("drug_raw"),
                "name_raw": f.get("name_raw") or f.get("drug_raw"), "outcome": f["review_status"], "value": f["reviewed_value"],
                "ranges": f.get("reviewed_ranges"), "rxnorm": f.get("rxnorm") if f["review_status"] == "confirmed" else None,
                "basis": "reviewer_entered_from_paper" if f["review_status"] == "corrected" else "machine_reading_confirmed_by_reviewer",
                "automatic_checks": "not applicable to the reviewer's entry" if f["review_status"] == "corrected" else "see the document view",
                "source": {"type": "ocr_manual_correction" if f["review_status"] == "corrected" else "ocr", "document_id": doc["document_id"],
                           "field_id": f["field_id"], "page_index": f["page_index"], "png_sha256": f["page_png_sha256"], "regions": f["regions"],
                           "resolved_by_role": f["review"]["actor_role"] if f["review"] else None},
            })
    return {"case_id": case_id, "values": out, "unresolved": unresolved,
            "note": "View only. Reviewed document values never change triage or urgency; enter any value on the triage form yourself if clinically relevant."}


# ── startup sweep ─────────────────────────────────────────────────────────────────────────────────


async def startup_sweep(conn, settings: Settings) -> int:
    """Mark stale pending rows abandoned and delete page files no row refers to (crash leftovers)."""
    swept = 0
    async with transaction(conn):
        async with conn.execute("SELECT document_id, case_id, created_at FROM ocr_documents WHERE status = 'pending'") as cur:
            stale = [r for r in await cur.fetchall() if _age_s(r["created_at"]) > pending_ttl_s(settings)]
        for r in stale:
            await conn.execute("UPDATE ocr_documents SET status = 'failed', failure_code = 'abandoned', completed_at = ? WHERE document_id = ? AND status = 'pending'",
                               (_now(), r["document_id"]))
            swept += 1
    await retention_sweep(conn, settings)
    await retry_purged_files(conn, settings)
    from app.ocr import image_service  # lazy: image_service imports this module

    await image_service.startup_sweep(conn, settings)  # medical images (docs/18): same store, `.img` files
    root = settings.ocr_document_dir
    if root.is_dir():
        async with conn.execute("SELECT file_ref FROM ocr_pages") as cur:
            known = {r["file_ref"] for r in await cur.fetchall()}
        async with conn.execute("SELECT document_id FROM ocr_documents WHERE status = 'pending'") as cur:
            pending = {r["document_id"] for r in await cur.fetchall()}
        for f in root.glob("*/*.png"):
            rel = f"{f.parent.name}/{f.name}"
            if rel not in known and not any(f.name.startswith(p + "-") for p in pending):
                f.unlink(missing_ok=True)
    return swept


# ── deletion and retention (docs/14 §3) ───────────────────────────────────────────────────────────


async def _purge_rows(conn, principal_id: str, principal_role: str, case_id: str, document_id: str, reason: str) -> list[str] | None:
    """Inside a write transaction: record the purge event, delete content rows, clear content columns.
    Returns the page file refs to delete after commit, or None if another request already purged it
    (re-checked under the write lock, so concurrent sweeps/deletes never collide)."""
    if await _purge_event(conn, document_id) is not None:
        return None
    await conn.execute("INSERT INTO ocr_purge_events (event_id, document_id, case_id, reason, actor_id, actor_role, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (str(uuid.uuid4()), document_id, case_id, reason, principal_id, principal_role, _now()))
    async with conn.execute("SELECT file_ref FROM ocr_pages WHERE document_id = ?", (document_id,)) as cur:
        refs = [r["file_ref"] for r in await cur.fetchall()]
    await conn.execute("DELETE FROM ocr_review_events WHERE field_id IN (SELECT field_id FROM ocr_fields WHERE document_id = ?)", (document_id,))
    await conn.execute("DELETE FROM ocr_fields WHERE document_id = ?", (document_id,))
    await conn.execute("DELETE FROM ocr_attestation_events WHERE document_id = ?", (document_id,))
    await conn.execute("DELETE FROM ocr_pages WHERE document_id = ?", (document_id,))
    await conn.execute("UPDATE ocr_documents SET dates_json = NULL, quality_json = NULL, overall_confidence = NULL, engines_json = NULL WHERE document_id = ?",
                       (document_id,))
    return refs


def _delete_files(settings: Settings, case_id: str, document_id: str, refs: list[str]) -> tuple[int, int]:
    """Delete the document's page files (recorded refs and any stray file of this document). Returns
    (removed, failed). Failures are reported as counts only — never paths or content."""
    removed = failed = 0
    root = settings.ocr_document_dir
    targets = {root / r for r in refs} | set((root / case_id).glob(f"{document_id}-*.png"))
    for f in targets:
        try:
            if f.exists():
                f.unlink()
                removed += 1
        except OSError:
            failed += 1
    return removed, failed


class _SystemPrincipal:
    user_id = "system"

    class role:  # noqa: N801 - mimics Principal.role.value
        value = "system"


async def delete_document(conn, principal: Principal, case_id: str, document_id: str, settings: Settings, request_id=None) -> dict:
    """Reviewer-requested deletion. Allowed to the creator ANM or an MO (triage access) even after consent
    was withdrawn — withdrawal is a common reason to ask for deletion. Idempotent."""
    await conn.execute("PRAGMA secure_delete = ON")  # overwrite deleted content in the database file
    refs: list[str] = []
    already = False
    async with transaction(conn):
        await consent.load_case(conn, principal, case_id, "triage")
        async with conn.execute("SELECT status FROM ocr_documents WHERE document_id = ? AND case_id = ?", (document_id, case_id)) as cur:
            d = await cur.fetchone()
        if d is None:
            raise not_found()
        if d["status"] == "pending":
            raise ApiError(409, "IN_PROGRESS", "This document is still being read; delete it when processing has finished")
        purged = await _purge_rows(conn, principal.user_id, principal.role.value, case_id, document_id, "reviewer_request")
        if purged is None:
            already = True
        else:
            refs = purged
    removed, failed = await anyio.to_thread.run_sync(lambda: _delete_files(settings, case_id, document_id, refs)) if not already else (0, 0)
    if not already:
        async with transaction(conn):
            await audit.record(conn, principal=principal, action="ocr_document_deleted", outcome="success" if not failed else "failure", case_id=case_id,
                               request_id=request_id, details=audit.OcrDeletedDetails(document_id=document_id, reason="reviewer_request",
                                                                                     files_removed=removed, files_failed=failed))
    return {"document_id": document_id, "status": "deleted", "already_deleted": already, "files_failed": failed,
            "note": "Page images, read text, values and review decisions were deleted from this server. Copies of the database file "
                    "(backups) are not covered."}


async def retention_sweep(conn, settings: Settings) -> int:
    """Purge documents older than OCR_RETENTION_DAYS (if set) and retry deleting files of purged documents.
    Run at startup and before every document request, so expired content is never served. Medical images
    (docs/18) follow the same policy and are swept here too."""
    if settings.ocr_retention_days is None:
        return 0
    from app.ocr import image_service  # lazy: image_service imports this module

    images_purged = await image_service.retention_sweep(conn, settings)
    return images_purged + await _retention_sweep_documents(conn, settings)


async def _retention_sweep_documents(conn, settings: Settings) -> int:
    assert settings.ocr_retention_days is not None
    from datetime import timedelta

    cutoff = (datetime.now(timezone.utc) - timedelta(days=settings.ocr_retention_days)).isoformat(timespec="microseconds")
    async with conn.execute(
        "SELECT d.document_id, d.case_id FROM ocr_documents d WHERE d.created_at < ? AND d.status != 'pending' "
        "AND NOT EXISTS (SELECT 1 FROM ocr_purge_events p WHERE p.document_id = d.document_id)", (cutoff,),
    ) as cur:
        expired = [(r["document_id"], r["case_id"]) for r in await cur.fetchall()]
    if not expired:
        return 0
    await conn.execute("PRAGMA secure_delete = ON")
    sysp = _SystemPrincipal()
    purged_count = 0
    for document_id, case_id in expired:
        async with transaction(conn):
            refs = await _purge_rows(conn, "system", "system", case_id, document_id, "retention_expired")
        if refs is None:
            continue  # purged concurrently by another request
        purged_count += 1
        removed, failed = await anyio.to_thread.run_sync(lambda: _delete_files(settings, case_id, document_id, refs))
        async with transaction(conn):
            await audit.record(conn, principal=sysp, action="ocr_document_deleted", outcome="success" if not failed else "failure", case_id=case_id,  # type: ignore[arg-type]
                               request_id=None, details=audit.OcrDeletedDetails(document_id=document_id, reason="retention_expired",
                                                                                 files_removed=removed, files_failed=failed))
    return purged_count


async def retry_purged_files(conn, settings: Settings) -> int:
    """Files of already-purged documents that could not be deleted earlier (e.g. a locked file)."""
    async with conn.execute("SELECT document_id, case_id FROM ocr_purge_events") as cur:
        rows = [(r["document_id"], r["case_id"]) for r in await cur.fetchall()]
    removed = 0
    for document_id, case_id in rows:
        removed += _delete_files(settings, case_id, document_id, [])[0]
    return removed
