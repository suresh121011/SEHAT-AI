"""Medical image orchestration (architecture §10A Pipeline B; docs/18). Mirrors app.ocr.service.upload.

    T1 [txn] case access (creator ANM) → triage consent in effect AND granted under a notice version that
             discloses documents → idempotency → `pending` row (gate decided here: disabled / consent / attestation)
       the metadata-stripped image is written to OCR_DOCUMENT_DIR/{case_id}/{document_id}.img
       only if the gate is open: ONE backend call (no fallback to another backend), bounded by MEDGEMMA_TIMEOUT_S
       → parse → field-level non-diagnostic guard → urgency keyword rules on the FILTERED fields → suffix
    T2 [txn] row still pending and consent unchanged → finalise once + audit, or discard

Guarantees and limits:
- MEDGEMMA_ENABLED=0: the image is stored and the row is `not_available` (reason `disabled`); no backend is built
  or called.
- A cloud backend (google_ai / azure) is called only with effective `ai_assist` consent AND a per-upload synthetic
  attestation; otherwise `not_available` with the specific reason. Startup already refused cloud without
  AI_CLOUD_ENABLED=1 + AI_CLOUD_SYNTHETIC_DATA_ONLY=1 (app.config).
- A backend error or timeout is `failed`; the image is kept for the reviewer to view.
- Output is a description for a clinician to interpret. It never sets urgency and never calls the rules engine.
  Urgency keyword hits are raise-only reviewer flags, and image findings with a flag (or a classifier mismatch)
  must be acknowledged by a reviewer before sign-off (app.review_queue).
- The upload's filename is used in memory for the classifier hint only; it is never stored or audited.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import uuid

import anyio
from pydantic import BaseModel, ConfigDict

from app import audit, consent
from app.auth import Principal
from app.config import Settings
from app.consent_notice import DOCUMENT_NOTICE_VERSIONS
from app.database import read_transaction, transaction
from app.errors import ApiError, not_found
from app.ocr import files, medgemma
from app.ocr.image_classifier import MEDGEMMA_TYPES, classify_upload, resolve
from app.ocr.service import _SystemPrincipal, _age_s, _consent_still_effective, _gate_reviewer, _log_internal, _now, _require_read

NOT_AVAILABLE_NOTES = {
    "disabled": "Image description is turned off on this server (MEDGEMMA_ENABLED=0). The image is saved for the reviewer to view.",
    "consent_ai_assist_missing": "The image was saved but not sent for description: this server's description service is a cloud model, "
                                 "and the patient's AI-assist consent is not in effect.",
    "synthetic_attestation_missing": "The image was saved but not sent for description: cloud description is limited to synthetic/demo "
                                     "images in this build, and the upload was not confirmed as synthetic.",
}
FAILED_NOTES = {
    "backend_timeout": "Image description took too long and was stopped. The image is saved for the reviewer; no other service was tried.",
    "bad_response": "The description service returned an unreadable answer, so nothing was shown. The image is saved for the reviewer.",
    "backend_error": "The description service failed. The image is saved for the reviewer; no other service was tried.",
    "abandoned": "Image description did not finish and was closed. The image is saved for the reviewer.",
    "interrupted": "Image description was interrupted. The image is saved for the reviewer.",
    "consent_changed": "Consent changed while the image was being described; the result was discarded.",
    "storage_failed": "The image could not be stored.",
}
DESCRIBED_NOTE = "AI-described visual findings for a qualified reviewer to interpret. Not a diagnosis; urgency is not changed by this."


def pending_ttl_s(settings: Settings) -> float:
    return settings.medgemma_timeout_s + 120


def source_image_url(case_id: str, document_id: str) -> str:
    return f"/api/backend/cases/{case_id}/medical-images/{document_id}/image"


# ── image sanitising (thread; no DB) ──────────────────────────────────────────────────────────────


def sanitize(data: bytes, media: str) -> tuple[bytes, int, int]:
    """Decode with the same header caps as app.ocr.files, apply EXIF orientation, downscale to the OCR page cap
    and re-encode in the same format with NO metadata (EXIF/GPS/text chunks dropped). Raises DocumentInvalid."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    Image.MAX_IMAGE_PIXELS = files.MAX_DECODE_PIXELS
    try:
        with Image.open(io.BytesIO(data)) as probe:  # header only
            w, h = probe.size
            if w * h > files.MAX_DECODE_PIXELS or w <= 0 or h <= 0:
                raise files.DocumentInvalid("too_many_pixels")
            probe.verify()
        img = Image.open(io.BytesIO(data))
        img.load()
    except files.DocumentInvalid:
        raise
    except Image.DecompressionBombError:
        raise files.DocumentInvalid("too_many_pixels") from None
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
        raise files.DocumentInvalid("corrupt") from None
    img = ImageOps.exif_transpose(img)
    img = files._fit(img, {})
    if media == "image/jpeg" or img.mode not in ("L", "RGB", "RGBA", "LA"):
        img = img.convert("RGB")
    img.info = {}
    buf = io.BytesIO()
    if media == "image/jpeg":
        img.save(buf, format="JPEG", quality=92)
    else:
        img.save(buf, format="PNG", optimize=False)
    return buf.getvalue(), img.width, img.height


def _write_image(settings: Settings, file_ref: str, data: bytes) -> None:
    root = settings.ocr_document_dir
    folder = root / file_ref.split("/", 1)[0]
    folder.mkdir(parents=True, exist_ok=True)
    for d in (root, folder):
        os.chmod(d, 0o700)
    fd = os.open(root / file_ref, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)


def _remove_file(settings: Settings, file_ref: str) -> None:
    try:
        (settings.ocr_document_dir / file_ref).unlink(missing_ok=True)
    except OSError:
        pass


# ── gate ──────────────────────────────────────────────────────────────────────────────────────────


def _gate(settings: Settings, backend, snap: consent.ConsentSnapshot, synthetic_attestation: bool) -> str | None:
    """Reason the backend must NOT be called, or None. Checked in order: disabled, consent, attestation."""
    if not settings.medgemma_enabled or backend is None:
        return "disabled"
    if getattr(backend, "cloud", settings.medgemma_backend in medgemma.CLOUD_BACKENDS):
        if not snap.is_effective("ai_assist"):
            return "consent_ai_assist_missing"
        if not synthetic_attestation:
            return "synthetic_attestation_missing"
    return None


# ── upload ────────────────────────────────────────────────────────────────────────────────────────


async def _finalize_failed(conn, principal, case_id, document_id, image_type, mismatch, backend_name, model, reason, request_id, outcome="failure") -> None:
    cur = await conn.execute("UPDATE medical_images SET status = 'failed', failure_code = ?, requires_acknowledgement = ?, completed_at = ? "
                             "WHERE document_id = ? AND status = 'pending'", (reason, int(mismatch), _now(), document_id))
    if cur.rowcount and backend_name in medgemma.BACKENDS:
        await audit.record(conn, principal=principal, action="medgemma_image_analyzed", outcome=outcome, case_id=case_id, request_id=request_id,
                           details=audit.MedgemmaAnalyzedDetails(document_id=document_id, image_class=image_type, backend=backend_name, model=model or "unknown",
                                                                 status="failed", reason_code=reason, field_count=0, withheld_field_count=0,
                                                                 description_withheld=False, red_count=0, yellow_count=0, review_note_count=0,
                                                                 classifier_mismatch=mismatch, rule_sets=[]))


async def upload(conn, principal: Principal, case_id: str, data: bytes, image_type: str, idempotency_key: str, settings: Settings,
                 request_id: str | None = None, *, filename: str | None = None, content_type: str | None = None,
                 synthetic_attestation: bool = False, backend=None) -> dict:
    if image_type not in MEDGEMMA_TYPES:
        raise ApiError(400, "VALIDATION_ERROR", "Request validation failed", {"fields": ["document_type"]})
    sha = hashlib.sha256(data).hexdigest()
    try:
        media = files.sniff(data)
    except files.DocumentInvalid as exc:
        if exc.reason == "empty":
            raise ApiError(400, "DOCUMENT_INVALID", "The file is empty", {"reason": "empty"}) from None
        raise ApiError(415, "UNSUPPORTED_MEDIA_TYPE", "Send a PNG or JPEG image", {"reason": exc.reason}) from None
    if media == "application/pdf":
        raise ApiError(415, "UNSUPPORTED_MEDIA_TYPE", "Medical images must be PNG or JPEG; upload a PDF as a text document", {"reason": "pdf_not_supported_for_images"})
    hint = classify_upload(filename, content_type, data[:16])
    _, mismatch = resolve(image_type, hint)
    try:
        stored, width, height = await anyio.to_thread.run_sync(lambda: sanitize(data, media))
    except files.DocumentInvalid as exc:
        raise ApiError(400, "DOCUMENT_INVALID", "The image could not be read as a valid PNG or JPEG", {"reason": exc.reason}) from None
    image_sha = hashlib.sha256(stored).hexdigest()
    if settings.medgemma_enabled and backend is None:
        backend = medgemma.build_image_backend(settings)
    if not settings.medgemma_enabled:
        backend = None  # never called when disabled, even if one was injected
    backend_name = getattr(backend, "name", None) if backend is not None else None
    model = getattr(backend, "model", None) if backend is not None else None
    document_id = str(uuid.uuid4())
    file_ref = f"{case_id}/{document_id}.img"
    denial = None
    existing = None
    authz_seq = 0
    reason: str | None = None
    cloud = bool(getattr(backend, "cloud", False))
    try:
        async with transaction(conn):
            await consent.load_case(conn, principal, case_id, "write")
            snap = await consent.require(conn, case_id, "triage")
            authz_seq = snap.authz_seq
            if await consent.granted_notice_version(conn, case_id, "triage") not in DOCUMENT_NOTICE_VERSIONS:
                existing = "notice"
            else:
                async with conn.execute("SELECT * FROM medical_images WHERE case_id = ? AND idempotency_key = ?", (case_id, idempotency_key)) as cur:
                    existing = await cur.fetchone()
                if existing is None:
                    reason = _gate(settings, backend, snap, synthetic_attestation)
                    await conn.execute(
                        "INSERT INTO medical_images (document_id, case_id, created_by, idempotency_key, upload_sha256, media_type, byte_size, declared_type, "
                        "classifier_hint, classifier_mismatch, synthetic_attestation, status, not_available_reason, file_ref, image_sha256, width, height, "
                        "requires_acknowledgement, backend, model_id, prompt_version, guard_version, consent_seq, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (document_id, case_id, principal.user_id, idempotency_key, sha, media, len(data), image_type, hint, int(mismatch), int(synthetic_attestation),
                         reason, file_ref, image_sha, width, height, int(mismatch), backend_name if reason != "disabled" else None,
                         model if reason != "disabled" else None, medgemma.PROMPT_VERSION, medgemma.GUARD_VERSION, authz_seq, _now()),
                    )
                elif existing["status"] == "pending" and _age_s(existing["created_at"]) > pending_ttl_s(settings):
                    await _finalize_failed(conn, principal, case_id, existing["document_id"], existing["declared_type"], bool(existing["classifier_mismatch"]),
                                           existing["backend"], existing["model_id"], "abandoned", request_id)
    except consent.ConsentNotEffective as exc:
        denial = exc
    if denial is not None:
        raise await consent.audit_denied(conn, principal, case_id, denial, request_id)
    if existing == "notice":
        raise ApiError(403, "CONSENT_NOTICE_UPDATE_REQUIRED", "Consent must be recorded again with the current notice, which explains document upload",
                       {"reason": "document_notice_not_accepted"})
    if existing is not None:
        if existing["created_by"] != principal.user_id or existing["upload_sha256"] != sha or existing["declared_type"] != image_type:
            raise ApiError(409, "IDEMPOTENCY_CONFLICT", "This request key was already used for a different document")
        view = await image_view(conn, existing["document_id"])
        if view["status"] == "pending":
            raise ApiError(409, "IN_PROGRESS", "This image is still being described")
        return view
    try:
        return await _process(conn, principal, case_id, document_id, image_type, media, stored, file_ref, hint, mismatch, reason, backend,
                              backend_name, model, cloud, authz_seq, settings, request_id)
    except BaseException:
        with anyio.CancelScope(shield=True):
            try:
                async with transaction(conn):
                    await _finalize_failed(conn, principal, case_id, document_id, image_type, mismatch, backend_name, model, "interrupted", request_id)
            except Exception:  # noqa: BLE001 - best effort; the TTL sweep covers what this cannot
                pass
        raise


def _signals(raw: medgemma.RawFindings, image_type: str, hint: str | None, mismatch: bool) -> tuple[list[dict], list[str]]:
    """Urgency rules read the UNFILTERED model output: they are raise-only and their notes carry only the matched
    keyword, never model text, so the non-diagnostic filter must not be able to hide a signal (e.g. a withheld
    "consistent with tension pneumothorax" still raises CRITICAL_IMAGING_FINDING)."""
    rule_sets = [image_type]
    signals = medgemma.check_image_urgency(raw.fields, image_type)
    if mismatch and hint in MEDGEMMA_TYPES and hint != image_type:
        rule_sets.append(hint)  # type: ignore[arg-type]
        signals += medgemma.check_image_urgency(raw.fields, hint, description=raw.description, all_text=True)  # type: ignore[arg-type]
    return signals, rule_sets


async def _process(conn, principal, case_id, document_id, image_type, media, stored, file_ref, hint, mismatch, reason, backend, backend_name, model, cloud,
                   authz_seq, settings, request_id) -> dict:
    try:
        await anyio.to_thread.run_sync(lambda: _write_image(settings, file_ref, stored))
    except OSError:
        async with transaction(conn):
            await conn.execute("UPDATE medical_images SET status = 'failed', failure_code = 'storage_failed', completed_at = ? WHERE document_id = ? AND status = 'pending'",
                               (_now(), document_id))
        raise ApiError(500, "IMAGE_STORAGE_FAILED", "The image could not be stored", {"reason": "storage_failed"}) from None

    failure: str | None = None
    raw: medgemma.RawFindings | None = None
    if reason is None:
        try:
            with anyio.fail_after(settings.medgemma_timeout_s):
                reply = await backend.describe(stored, media, medgemma.build_prompt(image_type), image_type=image_type)
            raw = medgemma.parse_output(reply, image_type)
        except TimeoutError:
            failure = "backend_timeout"
        except medgemma.BadResponse:
            failure = "bad_response"
        except Exception as exc:  # noqa: BLE001 - never leave the row pending; never echo the provider error
            failure = "backend_error"
            _log_internal(exc)

    consent_changed = abandoned = False
    async with transaction(conn):
        snap = await consent.snapshot(conn, case_id)
        consent_changed = snap.authz_seq != authz_seq or not snap.is_effective("triage") or (reason is None and cloud and not snap.is_effective("ai_assist"))
        row = await (await conn.execute("SELECT status FROM medical_images WHERE document_id = ?", (document_id,))).fetchone()
        if row is None or row[0] != "pending":
            abandoned = True
        elif consent_changed:
            await _finalize_failed(conn, principal, case_id, document_id, image_type, mismatch, backend_name if reason is None else None, model,
                                   "consent_changed", request_id, outcome="denied")
        elif reason is not None:
            await conn.execute("UPDATE medical_images SET status = 'not_available', completed_at = ? WHERE document_id = ?", (_now(), document_id))
            await audit.record(conn, principal=principal, action="medgemma_image_not_available", outcome="success", case_id=case_id, request_id=request_id,
                               details=audit.MedgemmaNotAvailableDetails(document_id=document_id, image_class=image_type, reason=reason,
                                                                         classifier_mismatch=mismatch))
        elif failure is not None:
            await _finalize_failed(conn, principal, case_id, document_id, image_type, mismatch, backend_name, model, failure, request_id)
        else:
            await _store(conn, principal, case_id, document_id, image_type, hint, mismatch, raw, backend_name, model, request_id)
    if consent_changed:
        _remove_file(settings, file_ref)
        raise ApiError(409, "CONSENT_WITHDRAWN", "Consent changed while the image was being described; the result was discarded")
    if abandoned:
        raise ApiError(409, "DOCUMENT_ABANDONED", "This image took too long and was closed; please upload it again")
    return await image_view(conn, document_id)


async def _store(conn, principal, case_id, document_id, image_type, hint, mismatch, raw: medgemma.RawFindings, backend_name, model, request_id) -> None:
    filtered = medgemma.filter_findings(raw)
    signals, rule_sets = _signals(raw, image_type, hint, mismatch)
    band = medgemma.confidence_band(raw.confidence)
    description = (filtered.description + medgemma.MANDATORY_SUFFIX) if filtered.description else None
    findings = {"raw_description": description, "structured_fields": filtered.fields, "image_quality": raw.image_quality}
    needs_ack = medgemma.requires_acknowledgement(signals, mismatch)
    await conn.execute(
        "UPDATE medical_images SET status = 'described', findings_json = ?, withheld_json = ?, urgency_signals_json = ?, rule_sets_run = ?, confidence = ?, "
        "confidence_band = ?, requires_acknowledgement = ?, completed_at = ? WHERE document_id = ?",
        (json.dumps(findings), json.dumps(filtered.withheld()), json.dumps(signals), json.dumps(rule_sets), raw.confidence, band, int(needs_ack), _now(), document_id),
    )
    if backend_name not in medgemma.BACKENDS:
        backend_name = "fake"  # audit enum; an injected test double without a known name is recorded as not-a-cloud call
    await audit.record(conn, principal=principal, action="medgemma_image_analyzed", outcome="success", case_id=case_id, request_id=request_id,
                       details=audit.MedgemmaAnalyzedDetails(
                           document_id=document_id, image_class=image_type, backend=backend_name, model=model or "unknown", status="described",
                           confidence_band=band, field_count=len(filtered.fields), withheld_field_count=len(filtered.withheld_fields),
                           description_withheld=filtered.description_withheld, signal_codes=sorted({s["signal"] for s in signals}),
                           red_count=sum(s["action"] == "RED_FLAG" for s in signals), yellow_count=sum(s["action"] == "YELLOW_FLAG" for s in signals),
                           review_note_count=sum(s["action"] == "REVIEW_NOTE" for s in signals), classifier_mismatch=mismatch, rule_sets=rule_sets))
    if filtered.any_blocked:
        await audit.record(conn, principal=principal, action="medgemma_diagnosis_blocked", outcome="success", case_id=case_id, request_id=request_id,
                           details=audit.MedgemmaBlockedDetails(document_id=document_id, reasons=sorted(set(filtered.reasons))[:8],
                                                                withheld_field_count=len(filtered.withheld_fields),
                                                                dropped_sentence_count=filtered.dropped_sentences,
                                                                description_withheld=filtered.description_withheld))


# ── views ─────────────────────────────────────────────────────────────────────────────────────────


async def _purge_event(conn, document_id: str):
    async with conn.execute("SELECT * FROM medical_image_purge_events WHERE document_id = ?", (document_id,)) as cur:
        return await cur.fetchone()


async def _acknowledged(conn, document_id: str) -> bool:
    async with conn.execute("SELECT 1 FROM medical_image_acknowledgements WHERE document_id = ?", (document_id,)) as cur:
        return await cur.fetchone() is not None


def _note(row, withheld: dict, band: str | None) -> str | None:
    status = row["status"]
    if status == "not_available":
        return NOT_AVAILABLE_NOTES.get(row["not_available_reason"] or "", None)
    if status == "failed":
        return FAILED_NOTES.get(row["failure_code"] or "", FAILED_NOTES["backend_error"])
    if status == "unsupported_type":
        return "Image type not supported. Displaying raw image to reviewer."
    if status == "pending":
        return "The image is being described."
    parts = [DESCRIBED_NOTE]
    if withheld.get("fields") or withheld.get("description"):
        parts.append("Some model text was withheld because it read as a diagnosis or instruction; check the image itself.")
    if band == "low":
        parts.append("The model reported low confidence in its own description (uncalibrated); rely on the image.")
    return " ".join(parts)


def row_view(row, acknowledged: bool) -> dict:
    findings = json.loads(row["findings_json"]) if row["findings_json"] else {}
    withheld = json.loads(row["withheld_json"]) if row["withheld_json"] else {"fields": [], "description": False, "reasons": []}
    signals = json.loads(row["urgency_signals_json"]) if row["urgency_signals_json"] else []
    return {
        "document_id": row["document_id"], "case_id": row["case_id"], "pipeline": "medgemma",
        "image_class": row["declared_type"], "declared_type": row["declared_type"],
        "classifier_hint": row["classifier_hint"], "classifier_mismatch": bool(row["classifier_mismatch"]),
        "status": row["status"], "note": _note(row, withheld, row["confidence_band"]),
        "not_available_reason": row["not_available_reason"] if row["status"] == "not_available" else None,
        "raw_description": findings.get("raw_description"),
        "structured_fields": findings.get("structured_fields") or {},
        "withheld": withheld,
        "urgency_signals": signals,
        "confidence": row["confidence"], "confidence_band": row["confidence_band"],
        "disclaimer": medgemma.DISCLAIMER,
        "keyword_rules_validated": medgemma.KEYWORD_RULES_VALIDATED,
        "source_image_url": source_image_url(row["case_id"], row["document_id"]),
        "media_type": row["media_type"],
        "backend": row["backend"], "model": row["model_id"], "prompt_version": row["prompt_version"], "guard_version": row["guard_version"],
        "rule_sets_run": json.loads(row["rule_sets_run"]) if row["rule_sets_run"] else [],
        "requires_acknowledgement": bool(row["requires_acknowledgement"]), "acknowledged": acknowledged,
        "created_at": row["created_at"], "completed_at": row["completed_at"],
    }


async def image_view(conn, document_id: str) -> dict:
    async with conn.execute("SELECT * FROM medical_images WHERE document_id = ?", (document_id,)) as cur:
        row = await cur.fetchone()
    if row is None or await _purge_event(conn, document_id) is not None:
        raise not_found()
    return row_view(row, await _acknowledged(conn, document_id))


async def list_images(conn, principal, case_id: str, request_id=None) -> list[dict]:
    await _require_read(conn, principal, case_id, request_id)
    async with read_transaction(conn):
        await _consent_still_effective(conn, case_id)
        async with conn.execute("SELECT m.document_id FROM medical_images m WHERE m.case_id = ? AND NOT EXISTS "
                                "(SELECT 1 FROM medical_image_purge_events p WHERE p.document_id = m.document_id) ORDER BY m.created_at", (case_id,)) as cur:
            ids = [r["document_id"] for r in await cur.fetchall()]
        return [await image_view(conn, i) for i in ids]


async def image_bytes(conn, principal, case_id: str, document_id: str, settings: Settings, request_id=None) -> tuple[bytes, str]:
    await _require_read(conn, principal, case_id, request_id)
    async with read_transaction(conn):
        await _consent_still_effective(conn, case_id)
        async with conn.execute("SELECT * FROM medical_images WHERE document_id = ? AND case_id = ?", (document_id, case_id)) as cur:
            row = await cur.fetchone()
        if row is None or await _purge_event(conn, document_id) is not None:
            raise not_found()
        path = (settings.ocr_document_dir / row["file_ref"]).resolve()
        if settings.ocr_document_dir.resolve() not in path.parents or not path.is_file():
            raise not_found()
        data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != row["image_sha256"]:
        raise ApiError(409, "EVIDENCE_CHANGED", "The stored image does not match its recorded hash")
    return data, row["media_type"]


# ── reviewer acknowledgement ──────────────────────────────────────────────────────────────────────


class AcknowledgeBody(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


async def acknowledge(conn, principal: Principal, case_id: str, document_id: str, request_id=None) -> dict:
    """"Findings reviewed": one append-only event per image (idempotent: a repeat returns the same state)."""
    denial = None
    try:
        async with transaction(conn):
            await _gate_reviewer(conn, principal, case_id)
            async with conn.execute("SELECT * FROM medical_images WHERE document_id = ? AND case_id = ?", (document_id, case_id)) as cur:
                row = await cur.fetchone()
            if row is None or await _purge_event(conn, document_id) is not None:
                raise not_found()
            if row["status"] == "pending":
                raise ApiError(409, "IN_PROGRESS", "This image is still being described")
            if not await _acknowledged(conn, document_id):
                await conn.execute("INSERT INTO medical_image_acknowledgements (event_id, document_id, case_id, actor_id, actor_role, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                                   (str(uuid.uuid4()), document_id, case_id, principal.user_id, principal.role.value, _now()))
                signals = json.loads(row["urgency_signals_json"]) if row["urgency_signals_json"] else []
                await audit.record(conn, principal=principal, action="medgemma_findings_acknowledged", outcome="success", case_id=case_id, request_id=request_id,
                                   details=audit.MedgemmaAcknowledgedDetails(document_id=document_id, requires_acknowledgement=bool(row["requires_acknowledgement"]),
                                                                             classifier_mismatch=bool(row["classifier_mismatch"]), signal_count=len(signals)))
    except consent.ConsentNotEffective as exc:
        denial = exc
    if denial is not None:
        raise await consent.audit_denied(conn, principal, case_id, denial, request_id)
    return await image_view(conn, document_id)


# ── deletion, retention, startup sweep ───────────────────────────────────────────────────────────


async def _purge(conn, actor_id: str, actor_role: str, case_id: str, document_id: str, reason: str) -> str | None:
    """Inside a write transaction. Returns the file ref to delete after commit, or None if already purged."""
    if await _purge_event(conn, document_id) is not None:
        return None
    await conn.execute("INSERT INTO medical_image_purge_events (event_id, document_id, case_id, reason, actor_id, actor_role, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (str(uuid.uuid4()), document_id, case_id, reason, actor_id, actor_role, _now()))
    await conn.execute("UPDATE medical_images SET findings_json = NULL, urgency_signals_json = NULL, confidence = NULL WHERE document_id = ?", (document_id,))
    row = await (await conn.execute("SELECT file_ref FROM medical_images WHERE document_id = ?", (document_id,))).fetchone()
    return row["file_ref"]


def _delete_file(settings: Settings, file_ref: str) -> tuple[int, int]:
    f = settings.ocr_document_dir / file_ref
    try:
        if f.exists():
            f.unlink()
            return 1, 0
        return 0, 0
    except OSError:
        return 0, 1


async def delete_image(conn, principal: Principal, case_id: str, document_id: str, settings: Settings, request_id=None) -> dict:
    """Reviewer-requested deletion (creator ANM or an MO), allowed after consent withdrawal. Idempotent."""
    await conn.execute("PRAGMA secure_delete = ON")
    ref = None
    async with transaction(conn):
        await consent.load_case(conn, principal, case_id, "triage")
        async with conn.execute("SELECT status FROM medical_images WHERE document_id = ? AND case_id = ?", (document_id, case_id)) as cur:
            d = await cur.fetchone()
        if d is None:
            raise not_found()
        if d["status"] == "pending":
            raise ApiError(409, "IN_PROGRESS", "This image is still being described; delete it when processing has finished")
        ref = await _purge(conn, principal.user_id, principal.role.value, case_id, document_id, "reviewer_request")
    already = ref is None
    removed, failed = (0, 0) if already else await anyio.to_thread.run_sync(lambda: _delete_file(settings, ref))
    if not already:
        async with transaction(conn):
            await audit.record(conn, principal=principal, action="medgemma_image_deleted", outcome="success" if not failed else "failure", case_id=case_id,
                               request_id=request_id, details=audit.OcrDeletedDetails(document_id=document_id, reason="reviewer_request",
                                                                                     files_removed=removed, files_failed=failed))
    return {"document_id": document_id, "status": "deleted", "already_deleted": already, "files_failed": failed,
            "note": "The image and its AI-described findings were deleted from this server. Copies of the database file (backups) are not covered."}


async def retention_sweep(conn, settings: Settings) -> int:
    """Purge images older than OCR_RETENTION_DAYS (same policy as OCR documents); retry files of purged images."""
    if settings.ocr_retention_days is None:
        return 0
    from datetime import datetime, timedelta, timezone

    cutoff = (datetime.now(timezone.utc) - timedelta(days=settings.ocr_retention_days)).isoformat(timespec="microseconds")
    async with conn.execute(
        "SELECT m.document_id, m.case_id FROM medical_images m WHERE m.created_at < ? AND m.status != 'pending' "
        "AND NOT EXISTS (SELECT 1 FROM medical_image_purge_events p WHERE p.document_id = m.document_id)", (cutoff,),
    ) as cur:
        expired = [(r["document_id"], r["case_id"]) for r in await cur.fetchall()]
    if not expired:
        return 0
    await conn.execute("PRAGMA secure_delete = ON")
    sysp = _SystemPrincipal()
    count = 0
    for document_id, case_id in expired:
        async with transaction(conn):
            ref = await _purge(conn, "system", "system", case_id, document_id, "retention_expired")
        if ref is None:
            continue
        count += 1
        removed, failed = await anyio.to_thread.run_sync(lambda: _delete_file(settings, ref))
        async with transaction(conn):
            await audit.record(conn, principal=sysp, action="medgemma_image_deleted", outcome="success" if not failed else "failure", case_id=case_id,  # type: ignore[arg-type]
                               request_id=None, details=audit.OcrDeletedDetails(document_id=document_id, reason="retention_expired",
                                                                                 files_removed=removed, files_failed=failed))
    return count


async def startup_sweep(conn, settings: Settings) -> None:
    """Close stale pending rows, retry files of purged images and delete image files no row refers to."""
    async with transaction(conn):
        async with conn.execute("SELECT document_id, created_at FROM medical_images WHERE status = 'pending'") as cur:
            stale = [r["document_id"] for r in await cur.fetchall() if _age_s(r["created_at"]) > pending_ttl_s(settings)]
        for document_id in stale:
            await conn.execute("UPDATE medical_images SET status = 'failed', failure_code = 'abandoned', completed_at = ? WHERE document_id = ? AND status = 'pending'",
                               (_now(), document_id))
    async with conn.execute("SELECT m.file_ref FROM medical_images m JOIN medical_image_purge_events p USING (document_id)") as cur:
        for r in await cur.fetchall():
            _delete_file(settings, r["file_ref"])
    root = settings.ocr_document_dir
    if root.is_dir():
        async with conn.execute("SELECT file_ref FROM medical_images m WHERE NOT EXISTS (SELECT 1 FROM medical_image_purge_events p WHERE p.document_id = m.document_id)") as cur:
            known = {r["file_ref"] for r in await cur.fetchall()}
        for f in root.glob("*/*.img"):
            if f"{f.parent.name}/{f.name}" not in known:
                f.unlink(missing_ok=True)
