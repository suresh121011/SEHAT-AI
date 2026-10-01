"""Document deletion and retention (docs/14 §3). Synthetic documents only; engines faked."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.config import get_settings
from tests.ocr.test_api import CANARY, FakeEngines, _attest, _ready_case, _review, _upload, ocr_client  # noqa: F401  (fixture)
from tests.privacy.helpers import audit_rows, auth, token_for, withdraw


def _db():
    c = sqlite3.connect(get_settings().database_path)
    c.row_factory = sqlite3.Row
    return c


def _counts(doc_id):
    with _db() as c:
        return {t: c.execute(f"SELECT count(*) FROM {t} WHERE document_id = ?", (doc_id,)).fetchone()[0]
                for t in ("ocr_pages", "ocr_fields", "ocr_attestation_events")} | {
            "ocr_review_events": c.execute("SELECT count(*) FROM ocr_review_events WHERE case_id IN (SELECT case_id FROM ocr_documents WHERE document_id = ?)", (doc_id,)).fetchone()[0]}


def test_reviewer_delete_removes_files_rows_and_content_and_is_idempotent(ocr_client):
    anm = token_for(ocr_client, "anm")
    case_id = _ready_case(ocr_client, anm)
    doc = _upload(ocr_client, anm, case_id).json()
    _attest(ocr_client, anm, case_id, doc["document_id"])
    _review(ocr_client, anm, case_id, doc["fields"][0], "corrected", {"result": {"value": "9.9", "comparator": "="}})
    files = list(Path(get_settings().ocr_document_dir).rglob("*.png"))
    assert files
    r = ocr_client.delete(f"/api/v1/cases/{case_id}/documents/{doc['document_id']}", headers=auth(anm))
    assert r.status_code == 200 and r.json()["already_deleted"] is False and r.json()["files_failed"] == 0
    assert not any(f.exists() for f in files)
    assert _counts(doc["document_id"]) == {"ocr_pages": 0, "ocr_fields": 0, "ocr_attestation_events": 0, "ocr_review_events": 0}
    with _db() as c:
        d = c.execute("SELECT * FROM ocr_documents WHERE document_id = ?", (doc["document_id"],)).fetchone()
        assert d["dates_json"] is None and d["quality_json"] is None and d["engines_json"] is None and d["status"] == "completed"
        assert c.execute("SELECT reason FROM ocr_purge_events").fetchone()[0] == "reviewer_request"
    # no longer served
    view = ocr_client.get(f"/api/v1/cases/{case_id}/documents/{doc['document_id']}", headers=auth(anm)).json()
    assert view["status"] == "deleted" and view["fields"] == [] and view["pages"] == []
    assert ocr_client.get(f"/api/v1/cases/{case_id}/documents/{doc['document_id']}/pages/0/image", headers=auth(anm)).status_code == 404
    assert ocr_client.get(f"/api/v1/cases/{case_id}/documents/reviewed", headers=auth(anm)).json()["values"] == []
    # idempotent
    again = ocr_client.delete(f"/api/v1/cases/{case_id}/documents/{doc['document_id']}", headers=auth(anm))
    assert again.status_code == 200 and again.json()["already_deleted"] is True
    # audit: one deletion record, ids and counts only
    dels = [r for r in audit_rows(ocr_client) if r["action"] == "ocr_document_deleted"]
    assert len(dels) == 1 and json.loads(dels[0]["details_json"])["files_removed"] == len(files)
    assert not any(x in json.dumps(dels) for x in CANARY + ("9.9",))


def test_delete_allowed_after_consent_withdrawal_but_not_to_others(ocr_client):
    anm, sup, other, pat = (token_for(ocr_client, w) for w in ("anm", "supervisor", "anm_other", "patient"))
    case_id = _ready_case(ocr_client, anm)
    doc = _upload(ocr_client, anm, case_id).json()
    url = f"/api/v1/cases/{case_id}/documents/{doc['document_id']}"
    assert ocr_client.delete(url).status_code == 401
    assert ocr_client.delete(url, headers=auth(pat)).status_code == 403
    for tok in (sup, other):
        assert ocr_client.delete(url, headers=auth(tok)).status_code in (403, 404)
    withdraw(ocr_client, anm, case_id, "triage")
    assert ocr_client.delete(url, headers=auth(anm)).status_code == 200
    # a document id under another case is not found
    case2 = _ready_case(ocr_client, anm)
    assert ocr_client.delete(f"/api/v1/cases/{case2}/documents/{doc['document_id']}", headers=auth(anm)).status_code == 404


def test_direct_delete_without_purge_event_is_still_blocked(ocr_client):
    anm = token_for(ocr_client, "anm")
    case_id = _ready_case(ocr_client, anm)
    _upload(ocr_client, anm, case_id)
    with _db() as c:
        for stmt in ("DELETE FROM ocr_pages", "DELETE FROM ocr_fields", "UPDATE ocr_documents SET dates_json = NULL"):
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                c.execute(stmt)


def test_retention_expiry_purges_before_anything_is_served(ocr_client, monkeypatch):
    anm = token_for(ocr_client, "anm")
    case_id = _ready_case(ocr_client, anm)
    doc = _upload(ocr_client, anm, case_id).json()
    old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat(timespec="microseconds")
    with _db() as c:
        c.execute("DROP TRIGGER ocr_documents_final")  # test-only: age the synthetic document
        c.execute("UPDATE ocr_documents SET created_at = ? WHERE document_id = ?", (old, doc["document_id"]))
    monkeypatch.setenv("OCR_RETENTION_DAYS", "30")
    get_settings.cache_clear()
    view = ocr_client.get(f"/api/v1/cases/{case_id}/documents/{doc['document_id']}", headers=auth(anm)).json()
    assert view["status"] == "deleted" and view["deleted"]["reason"] == "retention_expired"
    assert not list(Path(get_settings().ocr_document_dir).rglob(f"{doc['document_id']}-*.png"))
    assert [r for r in audit_rows(ocr_client) if r["action"] == "ocr_document_deleted" and r["actor_id"] == "system"]


def test_retention_must_be_explicit_when_ocr_is_enabled(monkeypatch):
    from app.config import get_settings as gs

    monkeypatch.setenv("OCR_ENABLED", "1")
    monkeypatch.delenv("OCR_RETENTION_DAYS", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("JWT_SECRET_KEY", "x" * 40)
    gs.cache_clear()
    with pytest.raises(RuntimeError, match="OCR_RETENTION_DAYS"):
        gs()
    for bad in ("0", "-3", "thirty"):
        monkeypatch.setenv("OCR_RETENTION_DAYS", bad)
        gs.cache_clear()
        with pytest.raises(RuntimeError):
            gs()
    monkeypatch.setenv("OCR_RETENTION_DAYS", "none")
    gs.cache_clear()
    assert gs().ocr_retention_days is None
    gs.cache_clear()


def test_delete_pending_document_is_refused(ocr_client):
    anm = token_for(ocr_client, "anm")
    case_id = _ready_case(ocr_client, anm)
    doc = _upload(ocr_client, anm, case_id).json()
    with _db() as c:
        c.execute("DROP TRIGGER ocr_documents_final")
        c.execute("UPDATE ocr_documents SET status = 'pending' WHERE document_id = ?", (doc["document_id"],))
    r = ocr_client.delete(f"/api/v1/cases/{case_id}/documents/{doc['document_id']}", headers=auth(anm))
    assert r.status_code == 409 and r.json()["error"]["code"] == "IN_PROGRESS"


def test_concurrent_purges_do_not_collide_and_deleted_cannot_be_attested(ocr_client):
    import asyncio

    from app.database import _connect
    from app.ocr import service

    anm = token_for(ocr_client, "anm")
    case_id = _ready_case(ocr_client, anm)
    doc = _upload(ocr_client, anm, case_id).json()

    async def two_purges():
        a, b = await _connect(get_settings().database_path), await _connect(get_settings().database_path)
        try:
            async def one(conn):
                async with service.transaction(conn):
                    return await service._purge_rows(conn, "x", "anm", case_id, doc["document_id"], "reviewer_request")
            return await asyncio.gather(one(a), one(b))
        finally:
            await a.close()
            await b.close()

    results = asyncio.run(two_purges())
    assert sum(r is not None for r in results) == 1  # exactly one purge; the other sees it and stops
    att = _attest(ocr_client, anm, case_id, doc["document_id"])
    assert att.status_code == 404


def test_evidence_columns_frozen_after_purge(ocr_client):
    anm = token_for(ocr_client, "anm")
    case_id = _ready_case(ocr_client, anm)
    doc = _upload(ocr_client, anm, case_id).json()
    ocr_client.delete(f"/api/v1/cases/{case_id}/documents/{doc['document_id']}", headers=auth(anm))
    with _db() as c:
        for col, val in (("created_at", "'2000-01-01'"), ("byte_size", "1"), ("upload_sha256", "'x'"), ("dates_json", "'{}'")):
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                c.execute(f"UPDATE ocr_documents SET {col} = {val} WHERE document_id = ?", (doc["document_id"],))
