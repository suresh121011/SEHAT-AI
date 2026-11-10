"""SQLite storage (hackathon). Baseline tables per docs/09 §1.2; privacy tables per docs/11.

Transactions are explicit: connections use autocommit (`isolation_level=None`) and every write goes
through `transaction()` (BEGIN IMMEDIATE ... COMMIT/ROLLBACK). Migrations never use `executescript`,
because it silently commits any pending transaction.
"""

import logging
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

from app.config import get_settings

log = logging.getLogger("sehat.database")

_NOW = "(strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))"

# ── Step 1: baseline schema (Phase 1). Idempotent; unchanged from the original SCHEMA. ──────────
BASELINE_STATEMENTS: tuple[str, ...] = (
    f"""CREATE TABLE IF NOT EXISTS cases (
    case_id        TEXT PRIMARY KEY,
    patient_token  TEXT NOT NULL,
    facility_code  TEXT NOT NULL,
    scenario       TEXT NOT NULL,
    status         TEXT NOT NULL,
    created_at     TEXT NOT NULL DEFAULT {_NOW}
)""",
    f"""CREATE TABLE IF NOT EXISTS consent (
    consent_id  TEXT PRIMARY KEY,
    case_id     TEXT NOT NULL REFERENCES cases(case_id),
    method      TEXT NOT NULL,
    language    TEXT NOT NULL,
    audio_ref   TEXT,
    granted_at  TEXT NOT NULL DEFAULT {_NOW}
)""",
    f"""CREATE TABLE IF NOT EXISTS triage_notes (
    case_id      TEXT PRIMARY KEY REFERENCES cases(case_id),
    urgency      TEXT NOT NULL,
    fields_json  TEXT NOT NULL DEFAULT '{{}}',
    scores_json  TEXT NOT NULL DEFAULT '{{}}',
    review_json  TEXT NOT NULL DEFAULT '{{}}',
    created_at   TEXT NOT NULL DEFAULT {_NOW}
)""",
    f"""CREATE TABLE IF NOT EXISTS audit_events (
    event_id       TEXT PRIMARY KEY,
    timestamp      TEXT NOT NULL DEFAULT {_NOW},
    actor_id       TEXT NOT NULL,
    action         TEXT NOT NULL,
    case_id        TEXT REFERENCES cases(case_id),
    details_json   TEXT NOT NULL DEFAULT '{{}}',
    previous_hash  TEXT,
    current_hash   TEXT NOT NULL UNIQUE
)""",
    f"""CREATE TABLE IF NOT EXISTS referrals (
    referral_id    TEXT PRIMARY KEY,
    case_id        TEXT NOT NULL REFERENCES cases(case_id),
    from_facility  TEXT NOT NULL,
    to_facility    TEXT NOT NULL,
    status         TEXT NOT NULL,
    created_at     TEXT NOT NULL DEFAULT {_NOW}
)""",
    "CREATE INDEX IF NOT EXISTS idx_consent_case ON consent(case_id)",
    "CREATE INDEX IF NOT EXISTS idx_audit_case ON audit_events(case_id)",
    "CREATE INDEX IF NOT EXISTS idx_referrals_case ON referrals(case_id)",
)


def _append_only(table: str) -> tuple[str, str]:
    return (
        f"CREATE TRIGGER {table}_no_update BEFORE UPDATE ON {table} BEGIN SELECT RAISE(ABORT, 'append-only'); END",
        f"CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table} BEGIN SELECT RAISE(ABORT, 'append-only'); END",
    )


# ── Step 2: privacy tables (Phase 3). Additive only: legacy `consent`/`audit_events` are kept
# (deprecated, unused) so no existing data or test is affected. ──────────────────────────────────
PRIVACY_STATEMENTS: tuple[str, ...] = (
    "ALTER TABLE cases ADD COLUMN created_by TEXT",
    """CREATE TABLE consent_events (
    seq                   INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id              TEXT NOT NULL UNIQUE,
    case_id               TEXT NOT NULL REFERENCES cases(case_id),
    purpose               TEXT NOT NULL CHECK (purpose IN ('triage', 'ai_assist')),
    action                TEXT NOT NULL CHECK (action IN ('granted', 'declined', 'withdrawn')),
    notice_version        TEXT NOT NULL,
    language              TEXT NOT NULL,
    notice_review_status  TEXT NOT NULL,
    method                TEXT NOT NULL,
    actor_id              TEXT NOT NULL,
    actor_role            TEXT NOT NULL,
    created_at            TEXT NOT NULL
)""",
    """CREATE TABLE triage_runs (
    seq              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id           TEXT NOT NULL UNIQUE,
    case_id          TEXT NOT NULL REFERENCES cases(case_id),
    consent_seq      INTEGER NOT NULL REFERENCES consent_events(seq),
    urgency          TEXT NOT NULL,
    result_json      TEXT NOT NULL,
    engine_version   TEXT NOT NULL,
    ruleset_version  TEXT NOT NULL,
    actor_id         TEXT NOT NULL,
    created_at       TEXT NOT NULL
)""",
    """CREATE TABLE audit_log (
    seq            INTEGER PRIMARY KEY,
    event_id       TEXT NOT NULL UNIQUE,
    timestamp      TEXT NOT NULL,
    actor_id       TEXT NOT NULL,
    actor_role     TEXT NOT NULL,
    action         TEXT NOT NULL,
    case_id        TEXT,
    outcome        TEXT NOT NULL CHECK (outcome IN ('success', 'denied', 'failure')),
    request_id     TEXT,
    details_json   TEXT NOT NULL,
    previous_hash  TEXT NOT NULL,
    current_hash   TEXT NOT NULL UNIQUE
)""",
    "CREATE INDEX idx_consent_events_case ON consent_events(case_id, purpose, seq)",
    "CREATE INDEX idx_triage_runs_case ON triage_runs(case_id)",
    "CREATE INDEX idx_audit_log_case ON audit_log(case_id)",
    *_append_only("consent_events"),
    *_append_only("triage_runs"),
    *_append_only("audit_log"),
)

# ── Step 3: voice pipeline (Phase 4, docs/12). ──────────────────────────────────────────────────
# (a) consent_events gains the `voice_cloud` purpose. SQLite cannot alter a CHECK constraint, so the
#     table is rebuilt with the documented procedure (sqlite.org/lang_altertable.html §7): this step runs
#     with foreign_keys OFF (see FK_OFF_STEPS), copies every row with its original seq, drops the old
#     table, renames, recreates index and append-only triggers, and `run_migrations` then runs
#     `PRAGMA foreign_key_check` before COMMIT. triage_runs.consent_seq references resolve by name.
# (b) voice tables. No audio is stored anywhere; transcripts are untrusted machine output.
VOICE_STATEMENTS: tuple[str, ...] = (
    """CREATE TABLE consent_events_v3 (
    seq                   INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id              TEXT NOT NULL UNIQUE,
    case_id               TEXT NOT NULL REFERENCES cases(case_id),
    purpose               TEXT NOT NULL CHECK (purpose IN ('triage', 'ai_assist', 'voice_cloud')),
    action                TEXT NOT NULL CHECK (action IN ('granted', 'declined', 'withdrawn')),
    notice_version        TEXT NOT NULL,
    language              TEXT NOT NULL,
    notice_review_status  TEXT NOT NULL,
    method                TEXT NOT NULL,
    actor_id              TEXT NOT NULL,
    actor_role            TEXT NOT NULL,
    created_at            TEXT NOT NULL
)""",
    "INSERT INTO consent_events_v3 (seq, event_id, case_id, purpose, action, notice_version, language, notice_review_status, method, actor_id, actor_role, created_at) "
    "SELECT seq, event_id, case_id, purpose, action, notice_version, language, notice_review_status, method, actor_id, actor_role, created_at FROM consent_events ORDER BY seq",
    "DROP TABLE consent_events",  # its index and triggers are dropped with it (DROP fires no triggers)
    "ALTER TABLE consent_events_v3 RENAME TO consent_events",
    "CREATE INDEX idx_consent_events_case ON consent_events(case_id, purpose, seq)",
    *_append_only("consent_events"),
    """CREATE TABLE voice_transcriptions (
    transcription_id  TEXT PRIMARY KEY,
    case_id           TEXT NOT NULL REFERENCES cases(case_id),
    created_by        TEXT NOT NULL,
    idempotency_key   TEXT NOT NULL,
    audio_sha256      TEXT NOT NULL,
    language          TEXT NOT NULL CHECK (language IN ('en', 'hi', 'or')),
    engine            TEXT NOT NULL CHECK (engine IN ('local', 'cloud')),
    status            TEXT NOT NULL CHECK (status IN ('pending', 'completed', 'no_speech', 'empty_transcript', 'failed')),
    model_id          TEXT,
    mode              TEXT,
    audio_duration_ms INTEGER NOT NULL,
    segments_json     TEXT NOT NULL DEFAULT '[]',
    transcript_raw    TEXT,
    failure_code      TEXT,
    consent_seq       INTEGER NOT NULL REFERENCES consent_events(seq),
    created_at        TEXT NOT NULL,
    completed_at      TEXT,
    UNIQUE (case_id, idempotency_key)
)""",
    # A transcription row is written as `pending` before any engine runs (idempotency) and may be
    # finalised exactly once; after that it is immutable. It is never deleted.
    "CREATE TRIGGER voice_transcriptions_final BEFORE UPDATE ON voice_transcriptions WHEN OLD.status != 'pending' "
    "BEGIN SELECT RAISE(ABORT, 'append-only'); END",
    "CREATE TRIGGER voice_transcriptions_no_delete BEFORE DELETE ON voice_transcriptions BEGIN SELECT RAISE(ABORT, 'append-only'); END",
    """CREATE TABLE voice_candidates (
    candidate_id      TEXT PRIMARY KEY,
    transcription_id  TEXT NOT NULL REFERENCES voice_transcriptions(transcription_id),
    case_id           TEXT NOT NULL REFERENCES cases(case_id),
    ordinal           INTEGER NOT NULL,
    field             TEXT NOT NULL,
    char_start        INTEGER NOT NULL,
    char_end          INTEGER NOT NULL,
    raw_value         REAL,
    raw_value2        REAL,
    unit              TEXT,
    normalized_json   TEXT,
    flags_json        TEXT NOT NULL,
    created_at        TEXT NOT NULL
)""",
    """CREATE TABLE voice_readback_events (
    seq               INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id          TEXT NOT NULL UNIQUE,
    candidate_id      TEXT NOT NULL REFERENCES voice_candidates(candidate_id),
    case_id           TEXT NOT NULL REFERENCES cases(case_id),
    outcome           TEXT NOT NULL CHECK (outcome IN ('confirmed', 'corrected', 'rejected', 'unsure')),
    resolved_field    TEXT,
    resolved_json     TEXT,
    resolved_unit     TEXT,
    actor_id          TEXT NOT NULL,
    actor_role        TEXT NOT NULL,
    created_at        TEXT NOT NULL
)""",
    "CREATE INDEX idx_voice_transcriptions_case ON voice_transcriptions(case_id)",
    "CREATE INDEX idx_voice_candidates_transcription ON voice_candidates(transcription_id, ordinal)",
    "CREATE INDEX idx_voice_readback_candidate ON voice_readback_events(candidate_id, seq)",
    *_append_only("voice_candidates"),
    *_append_only("voice_readback_events"),
)

# ── Step 4: OCR documents (Phase 5, docs/14). Additive only (new tables; nothing rebuilt). Page images
# live on the local filesystem (architecture: "local filesystem (documents)"); rows hold path + hash.
# Everything is append-only; a document row may be finalised exactly once. Deletion/retention is not
# implemented in Phase 5 (docs/14 §10), consistent with voice transcripts.
OCR_STATEMENTS: tuple[str, ...] = (
    """CREATE TABLE ocr_documents (
    document_id        TEXT PRIMARY KEY,
    case_id            TEXT NOT NULL REFERENCES cases(case_id),
    created_by         TEXT NOT NULL,
    idempotency_key    TEXT NOT NULL,
    upload_sha256      TEXT NOT NULL,
    media_type         TEXT NOT NULL CHECK (media_type IN ('image/png', 'image/jpeg', 'application/pdf')),
    document_type      TEXT NOT NULL CHECK (document_type IN ('lab_report', 'prescription', 'discharge_summary')),
    byte_size          INTEGER NOT NULL,
    status             TEXT NOT NULL CHECK (status IN ('pending', 'completed', 'quality_rejected', 'no_text', 'failed')),
    failure_code       TEXT,
    page_count         INTEGER,
    quality_json       TEXT,
    engines_json       TEXT,
    pipeline_version   TEXT NOT NULL,
    overall_confidence REAL,
    dates_json         TEXT,
    consent_seq        INTEGER NOT NULL REFERENCES consent_events(seq),
    created_at         TEXT NOT NULL,
    completed_at       TEXT,
    UNIQUE (case_id, idempotency_key)
)""",
    "CREATE TRIGGER ocr_documents_final BEFORE UPDATE ON ocr_documents WHEN OLD.status != 'pending' "
    "BEGIN SELECT RAISE(ABORT, 'append-only'); END",
    "CREATE TRIGGER ocr_documents_no_delete BEFORE DELETE ON ocr_documents BEGIN SELECT RAISE(ABORT, 'append-only'); END",
    """CREATE TABLE ocr_pages (
    document_id     TEXT NOT NULL REFERENCES ocr_documents(document_id),
    page_index      INTEGER NOT NULL,
    file_ref        TEXT NOT NULL,
    png_sha256      TEXT NOT NULL,
    width           INTEGER NOT NULL,
    height          INTEGER NOT NULL,
    transform_json  TEXT NOT NULL,
    PRIMARY KEY (document_id, page_index)
)""",
    """CREATE TABLE ocr_fields (
    field_id              TEXT PRIMARY KEY,
    document_id           TEXT NOT NULL REFERENCES ocr_documents(document_id),
    case_id               TEXT NOT NULL REFERENCES cases(case_id),
    ordinal               INTEGER NOT NULL,
    kind                  TEXT NOT NULL CHECK (kind IN ('lab', 'medication')),
    page_index            INTEGER NOT NULL,
    payload_json          TEXT NOT NULL,
    regions_json          TEXT NOT NULL,
    readings_json         TEXT NOT NULL,
    checks_json           TEXT NOT NULL,
    band                  TEXT NOT NULL CHECK (band IN ('accept', 'amber', 'human_entry')),
    field_confidence      REAL,
    disputed              INTEGER NOT NULL,
    created_at            TEXT NOT NULL
)""",
    """CREATE TABLE ocr_attestation_events (
    seq          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id     TEXT NOT NULL UNIQUE,
    document_id  TEXT NOT NULL REFERENCES ocr_documents(document_id),
    case_id      TEXT NOT NULL REFERENCES cases(case_id),
    answer       TEXT NOT NULL CHECK (answer IN ('matches', 'does_not_match', 'unsure')),
    actor_id     TEXT NOT NULL,
    actor_role   TEXT NOT NULL,
    created_at   TEXT NOT NULL
)""",
    """CREATE TABLE ocr_review_events (
    seq                   INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id              TEXT NOT NULL UNIQUE,
    field_id              TEXT NOT NULL REFERENCES ocr_fields(field_id),
    case_id               TEXT NOT NULL REFERENCES cases(case_id),
    outcome               TEXT NOT NULL CHECK (outcome IN ('confirmed', 'corrected', 'rejected', 'unsure')),
    corrected_json        TEXT,
    shown_png_sha256      TEXT,
    shown_regions_sha256  TEXT,
    actor_id              TEXT NOT NULL,
    actor_role            TEXT NOT NULL,
    created_at            TEXT NOT NULL
)""",
    "CREATE INDEX idx_ocr_documents_case ON ocr_documents(case_id)",
    "CREATE INDEX idx_ocr_fields_document ON ocr_fields(document_id, ordinal)",
    "CREATE INDEX idx_ocr_attestation_document ON ocr_attestation_events(document_id, seq)",
    "CREATE INDEX idx_ocr_review_field ON ocr_review_events(field_id, seq)",
    *_append_only("ocr_pages"),
    *_append_only("ocr_fields"),
    *_append_only("ocr_attestation_events"),
    *_append_only("ocr_review_events"),
)

# ── Step 5: OCR document deletion and retention (Phase 5, docs/14 §3). A document's content may be deleted
# only after an append-only purge event is recorded for it (reviewer request or retention expiry). The purge
# event, the document's id row and the audit log remain as evidence that it existed and was deleted.
_PURGED = "EXISTS (SELECT 1 FROM ocr_purge_events p WHERE p.document_id = OLD.document_id)"
OCR_PURGE_STATEMENTS: tuple[str, ...] = (
    """CREATE TABLE ocr_purge_events (
    seq          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id     TEXT NOT NULL UNIQUE,
    document_id  TEXT NOT NULL UNIQUE REFERENCES ocr_documents(document_id),
    case_id      TEXT NOT NULL REFERENCES cases(case_id),
    reason       TEXT NOT NULL CHECK (reason IN ('reviewer_request', 'retention_expired')),
    actor_id     TEXT NOT NULL,
    actor_role   TEXT NOT NULL,
    created_at   TEXT NOT NULL
)""",
    *_append_only("ocr_purge_events"),
    "DROP TRIGGER ocr_pages_no_delete",
    f"CREATE TRIGGER ocr_pages_no_delete BEFORE DELETE ON ocr_pages WHEN NOT {_PURGED} BEGIN SELECT RAISE(ABORT, 'append-only'); END",
    "DROP TRIGGER ocr_fields_no_delete",
    f"CREATE TRIGGER ocr_fields_no_delete BEFORE DELETE ON ocr_fields WHEN NOT {_PURGED} BEGIN SELECT RAISE(ABORT, 'append-only'); END",
    "DROP TRIGGER ocr_attestation_events_no_delete",
    f"CREATE TRIGGER ocr_attestation_events_no_delete BEFORE DELETE ON ocr_attestation_events WHEN NOT {_PURGED} BEGIN SELECT RAISE(ABORT, 'append-only'); END",
    "DROP TRIGGER ocr_review_events_no_delete",
    "CREATE TRIGGER ocr_review_events_no_delete BEFORE DELETE ON ocr_review_events WHEN NOT EXISTS "
    "(SELECT 1 FROM ocr_purge_events p JOIN ocr_fields f ON f.document_id = p.document_id WHERE f.field_id = OLD.field_id) "
    "BEGIN SELECT RAISE(ABORT, 'append-only'); END",
    # A finalised document row may change once more, only to clear its content columns after a purge event;
    # identity, type, status and hashes never change.
    "DROP TRIGGER ocr_documents_final",
    "CREATE TRIGGER ocr_documents_final BEFORE UPDATE ON ocr_documents WHEN OLD.status != 'pending' AND ("
    f"NOT {_PURGED} OR "
    + " OR ".join(f"NEW.{c} IS NOT OLD.{c}" for c in (
        "document_id", "case_id", "created_by", "idempotency_key", "upload_sha256", "media_type", "document_type", "byte_size",
        "status", "failure_code", "page_count", "pipeline_version", "consent_seq", "created_at", "completed_at"))
    + " OR NEW.dates_json IS NOT NULL OR NEW.quality_json IS NOT NULL OR NEW.overall_confidence IS NOT NULL OR NEW.engines_json IS NOT NULL) "
    "BEGIN SELECT RAISE(ABORT, 'append-only'); END",
)

# ── Step 6: Phase 6 AI extraction (docs/16 §9). Append-only. Only redacted-derived content is stored: the
# redacted segments the provider saw, voted fields with their quotes, reviewer decisions and note drafts. Raw
# intake text is never stored. `triage_runs` is not touched by this step.
AI_STATEMENTS: tuple[str, ...] = (
    """CREATE TABLE ai_extraction_runs (
    seq               INTEGER PRIMARY KEY AUTOINCREMENT,
    extraction_id     TEXT NOT NULL UNIQUE,
    case_id           TEXT NOT NULL REFERENCES cases(case_id),
    created_by        TEXT NOT NULL,
    actor_role        TEXT NOT NULL,
    idempotency_key   TEXT NOT NULL,
    provider          TEXT NOT NULL,
    provider_kind     TEXT NOT NULL,
    model_id          TEXT NOT NULL,
    prompt_version    TEXT NOT NULL,
    schema_version    TEXT NOT NULL,
    passes_requested  INTEGER NOT NULL,
    passes_valid      INTEGER NOT NULL,
    status            TEXT NOT NULL CHECK (status IN ('completed', 'insufficient_agreement')),
    consent_seq       INTEGER NOT NULL REFERENCES consent_events(seq),
    segments_json     TEXT NOT NULL,
    skipped_json      TEXT NOT NULL,
    dropped_json      TEXT NOT NULL,
    abstentions_json  TEXT NOT NULL,
    urgency_json      TEXT NOT NULL,
    flags_json        TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    request_sha256    TEXT NOT NULL,
    UNIQUE (case_id, idempotency_key)
)""",
    """CREATE TABLE ai_fields (
    field_id          TEXT PRIMARY KEY,
    extraction_id     TEXT NOT NULL REFERENCES ai_extraction_runs(extraction_id),
    case_id           TEXT NOT NULL REFERENCES cases(case_id),
    ordinal           INTEGER NOT NULL,
    origin            TEXT NOT NULL CHECK (origin IN ('model', 'ocr_reviewed')),
    field_key         TEXT NOT NULL,
    kind              TEXT NOT NULL,
    status            TEXT NOT NULL CHECK (status IN ('agreed', 'majority', 'disputed', 'disputed_raise', 'human_reviewed')),
    agreement         TEXT,
    value_json        TEXT NOT NULL,
    candidates_json   TEXT NOT NULL,
    evidence_json     TEXT NOT NULL,
    critical          INTEGER NOT NULL,
    priority_review   INTEGER NOT NULL,
    flags_json        TEXT NOT NULL,
    created_at        TEXT NOT NULL
)""",
    """CREATE TABLE ai_field_review_events (
    seq               INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id          TEXT NOT NULL UNIQUE,
    field_id          TEXT NOT NULL REFERENCES ai_fields(field_id),
    case_id           TEXT NOT NULL REFERENCES cases(case_id),
    outcome           TEXT NOT NULL CHECK (outcome IN ('accepted', 'corrected', 'rejected', 'unsure')),
    corrected_json    TEXT,
    supersedes        TEXT,
    actor_id          TEXT NOT NULL,
    actor_role        TEXT NOT NULL,
    created_at        TEXT NOT NULL
)""",
    """CREATE TABLE ai_note_drafts (
    seq                    INTEGER PRIMARY KEY AUTOINCREMENT,
    note_id                TEXT NOT NULL UNIQUE,
    case_id                TEXT NOT NULL REFERENCES cases(case_id),
    extraction_id          TEXT NOT NULL REFERENCES ai_extraction_runs(extraction_id),
    triage_run_id          TEXT REFERENCES triage_runs(run_id),
    recorded_urgency       TEXT,   -- the rules engine's, from triage_run_id
    urgency_if_accepted    TEXT,   -- enforce_raise_only(recorded, unanimous AI suggestion); nothing is recorded from it
    blocked_count          INTEGER NOT NULL,
    note_json              TEXT NOT NULL,
    consent_seq            INTEGER NOT NULL REFERENCES consent_events(seq),
    actor_id               TEXT NOT NULL,
    actor_role             TEXT NOT NULL,
    created_at             TEXT NOT NULL
)""",
    *_append_only("ai_extraction_runs"),
    *_append_only("ai_fields"),
    *_append_only("ai_field_review_events"),
    *_append_only("ai_note_drafts"),
)

# ── Step 7: Phase 6 P1. New triage runs also store the validated rules-engine input (vitals, flags, age; no
# identifiers) so counterfactuals can be recomputed. Nullable: earlier runs have none. Rows stay append-only.
TRIAGE_INPUT_STATEMENTS: tuple[str, ...] = ("ALTER TABLE triage_runs ADD COLUMN input_json TEXT",)

# ── Step 8: Phase 8 reviewer dashboard (docs/17). Append-only human review events bound to ONE triage run:
# sign-off, urgency override (reason code required) and RED acknowledgment. The triage run itself is never
# modified; the rules-engine urgency stays on triage_runs and the override is a separate, attributable row.
REVIEW_STATEMENTS: tuple[str, ...] = (
    """CREATE TABLE review_events (
    seq             INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        TEXT NOT NULL UNIQUE,
    case_id         TEXT NOT NULL REFERENCES cases(case_id),
    triage_run_id   TEXT NOT NULL REFERENCES triage_runs(run_id),
    kind            TEXT NOT NULL CHECK (kind IN ('sign_off', 'override', 'acknowledge')),
    rules_urgency   TEXT NOT NULL CHECK (rules_urgency IN ('RED', 'YELLOW', 'GREEN')),
    old_urgency     TEXT CHECK (old_urgency IN ('RED', 'YELLOW', 'GREEN')),
    new_urgency     TEXT CHECK (new_urgency IN ('RED', 'YELLOW', 'GREEN')),
    reason_code     TEXT,
    reason_text     TEXT,
    actor_id        TEXT NOT NULL,
    actor_role      TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    CHECK (kind != 'override' OR (old_urgency IS NOT NULL AND new_urgency IS NOT NULL AND reason_code IS NOT NULL AND old_urgency != new_urgency))
)""",
    "CREATE INDEX idx_review_events_case ON review_events(case_id, seq)",
    "CREATE UNIQUE INDEX idx_review_events_one_sign_off ON review_events(triage_run_id) WHERE kind = 'sign_off'",
    "CREATE UNIQUE INDEX idx_review_events_one_ack ON review_events(triage_run_id) WHERE kind = 'acknowledge'",
    *_append_only("review_events"),
)

# ── Step 9: Phase 8 hardening (docs/17 §3a). A reviewer's correction is a NEW triage run that names the run it
# corrects and the reason. Nullable additive columns (older runs and intake runs have none); rows stay append-only.
# The partial unique index lets a run be corrected at most once, so two concurrent corrections of the same run
# cannot both commit even if the in-transaction stale check were bypassed. Rollback: the columns are ignored by
# older code; nothing existing is rewritten.
TRIAGE_CORRECTION_STATEMENTS: tuple[str, ...] = (
    "ALTER TABLE triage_runs ADD COLUMN corrects_run_id TEXT REFERENCES triage_runs(run_id)",
    "ALTER TABLE triage_runs ADD COLUMN correction_reason_code TEXT",
    "ALTER TABLE triage_runs ADD COLUMN correction_reason_text TEXT",
    "CREATE UNIQUE INDEX idx_triage_runs_one_correction ON triage_runs(corrects_run_id) WHERE corrects_run_id IS NOT NULL",
    # A reviewer may raise a run to RED again after acknowledging an earlier RED on it: that new escalation needs
    # its own acknowledgment, so "one acknowledgment per run" no longer holds. Duplicate acknowledgments of the SAME
    # escalation are still refused by the state check inside the acknowledge write transaction (BEGIN IMMEDIATE).
    "DROP INDEX idx_review_events_one_ack",
)

# ── Step 10: medical image visual-findings description (architecture §10A, docs/18). Additive only: new tables,
# `ocr_documents` is not touched. The stored image (metadata-stripped re-encode) lives on the local filesystem next to
# OCR pages; rows hold its path and hash. A row is finalised exactly once; after an append-only purge event it may
# change once more, only to clear its content columns (same pattern as step 5). Acknowledgements ("findings
# reviewed") are append-only, one per image. Provenance columns record backend, model, prompt/guard versions and the
# rule sets that ran. Rollback: older code ignores these tables.
_IMG_TYPES = "('chest_xray', 'ecg_strip', 'ct_report_image', 'wound_photo', 'skin_lesion')"
_ALL_DOC_TYPES = "('lab_report', 'prescription', 'discharge_summary', 'chest_xray', 'ecg_strip', 'ct_report_image', 'wound_photo', 'skin_lesion')"
_IMG_PURGED = "EXISTS (SELECT 1 FROM medical_image_purge_events p WHERE p.document_id = OLD.document_id)"
MEDICAL_IMAGE_STATEMENTS: tuple[str, ...] = (
    f"""CREATE TABLE medical_images (
    document_id               TEXT PRIMARY KEY,
    case_id                   TEXT NOT NULL REFERENCES cases(case_id),
    created_by                TEXT NOT NULL,
    idempotency_key           TEXT NOT NULL,
    upload_sha256             TEXT NOT NULL,
    media_type                TEXT NOT NULL CHECK (media_type IN ('image/png', 'image/jpeg')),
    byte_size                 INTEGER NOT NULL,
    declared_type             TEXT NOT NULL CHECK (declared_type IN {_IMG_TYPES}),
    classifier_hint           TEXT CHECK (classifier_hint IS NULL OR classifier_hint IN {_ALL_DOC_TYPES}),
    classifier_mismatch       INTEGER NOT NULL CHECK (classifier_mismatch IN (0, 1)),
    synthetic_attestation     INTEGER NOT NULL CHECK (synthetic_attestation IN (0, 1)),
    status                    TEXT NOT NULL CHECK (status IN ('pending', 'described', 'not_available', 'unsupported_type', 'failed')),
    not_available_reason      TEXT CHECK (not_available_reason IS NULL OR not_available_reason IN ('disabled', 'consent_ai_assist_missing', 'synthetic_attestation_missing')),
    failure_code              TEXT,
    file_ref                  TEXT NOT NULL,
    image_sha256              TEXT NOT NULL,
    width                     INTEGER NOT NULL,
    height                    INTEGER NOT NULL,
    findings_json             TEXT,
    withheld_json             TEXT,
    urgency_signals_json      TEXT,
    rule_sets_run             TEXT,
    confidence                REAL,
    confidence_band           TEXT CHECK (confidence_band IS NULL OR confidence_band IN ('high', 'moderate', 'low')),
    requires_acknowledgement  INTEGER NOT NULL DEFAULT 0 CHECK (requires_acknowledgement IN (0, 1)),
    backend                   TEXT,
    model_id                  TEXT,
    prompt_version            TEXT NOT NULL,
    guard_version             TEXT NOT NULL,
    consent_seq               INTEGER NOT NULL REFERENCES consent_events(seq),
    created_at                TEXT NOT NULL,
    completed_at              TEXT,
    UNIQUE (case_id, idempotency_key)
)""",
    "CREATE INDEX idx_medical_images_case ON medical_images(case_id)",
    """CREATE TABLE medical_image_acknowledgements (
    seq          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id     TEXT NOT NULL UNIQUE,
    document_id  TEXT NOT NULL UNIQUE REFERENCES medical_images(document_id),
    case_id      TEXT NOT NULL REFERENCES cases(case_id),
    actor_id     TEXT NOT NULL,
    actor_role   TEXT NOT NULL,
    created_at   TEXT NOT NULL
)""",
    """CREATE TABLE medical_image_purge_events (
    seq          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id     TEXT NOT NULL UNIQUE,
    document_id  TEXT NOT NULL UNIQUE REFERENCES medical_images(document_id),
    case_id      TEXT NOT NULL REFERENCES cases(case_id),
    reason       TEXT NOT NULL CHECK (reason IN ('reviewer_request', 'retention_expired')),
    actor_id     TEXT NOT NULL,
    actor_role   TEXT NOT NULL,
    created_at   TEXT NOT NULL
)""",
    *_append_only("medical_image_acknowledgements"),
    *_append_only("medical_image_purge_events"),
    "CREATE TRIGGER medical_images_no_delete BEFORE DELETE ON medical_images BEGIN SELECT RAISE(ABORT, 'append-only'); END",
    "CREATE TRIGGER medical_images_final BEFORE UPDATE ON medical_images WHEN OLD.status != 'pending' AND ("
    f"NOT {_IMG_PURGED} OR "
    + " OR ".join(f"NEW.{c} IS NOT OLD.{c}" for c in (
        "document_id", "case_id", "created_by", "idempotency_key", "upload_sha256", "media_type", "byte_size", "declared_type", "classifier_hint",
        "classifier_mismatch", "synthetic_attestation", "status", "not_available_reason", "failure_code", "file_ref", "image_sha256", "width", "height",
        "withheld_json", "rule_sets_run", "confidence_band", "requires_acknowledgement", "backend", "model_id", "prompt_version", "guard_version",
        "consent_seq", "created_at", "completed_at"))
    + " OR NEW.findings_json IS NOT NULL OR NEW.urgency_signals_json IS NOT NULL OR NEW.confidence IS NOT NULL) "
    "BEGIN SELECT RAISE(ABORT, 'append-only'); END",
)

# ── Step 11: red-flag keyword suggester (docs/16 §2c). ai_fields gains origin 'keyword_rule' (deterministic phrases, not
# AI model output) and status 'keyword_suggested'. SQLite cannot alter a CHECK constraint, so the table is rebuilt as in
# step 3: foreign keys off for the step (ai_field_review_events references it), every row copied unchanged, old table
# dropped, renamed, append-only triggers recreated, then foreign_key_check before COMMIT. Rollback: older code still
# reads the table; it shows keyword_rule rows without a review action (its review route accepts origin 'model' only).
KEYWORD_RED_FLAG_STATEMENTS: tuple[str, ...] = (
    """CREATE TABLE ai_fields_v11 (
    field_id          TEXT PRIMARY KEY,
    extraction_id     TEXT NOT NULL REFERENCES ai_extraction_runs(extraction_id),
    case_id           TEXT NOT NULL REFERENCES cases(case_id),
    ordinal           INTEGER NOT NULL,
    origin            TEXT NOT NULL CHECK (origin IN ('model', 'ocr_reviewed', 'keyword_rule')),
    field_key         TEXT NOT NULL,
    kind              TEXT NOT NULL,
    status            TEXT NOT NULL CHECK (status IN ('agreed', 'majority', 'disputed', 'disputed_raise', 'human_reviewed', 'keyword_suggested')),
    agreement         TEXT,
    value_json        TEXT NOT NULL,
    candidates_json   TEXT NOT NULL,
    evidence_json     TEXT NOT NULL,
    critical          INTEGER NOT NULL,
    priority_review   INTEGER NOT NULL,
    flags_json        TEXT NOT NULL,
    created_at        TEXT NOT NULL
)""",
    "INSERT INTO ai_fields_v11 SELECT * FROM ai_fields",
    "DROP TABLE ai_fields",
    "ALTER TABLE ai_fields_v11 RENAME TO ai_fields",
    *_append_only("ai_fields"),
)

# ── Step 12: patient-to-health-worker handover and body map (docs/11 §3a). Additive. `created_by_role` is backfilled
# from each case's `case_created` audit row (the actor role recorded at creation); a case with no such row stays NULL
# and can never be handed over. `handled_by` is set once, by the ANM who takes over a patient-started case. Body-map
# selections are append-only; the latest event is the current selection. Rollback: older code ignores the new
# columns and table. ──────────────────────────────────────────────────────────────────────────────────────────
HANDOVER_STATEMENTS: tuple[str, ...] = (
    "ALTER TABLE cases ADD COLUMN created_by_role TEXT",
    "ALTER TABLE cases ADD COLUMN handled_by TEXT",
    "ALTER TABLE cases ADD COLUMN handled_at TEXT",
    "UPDATE cases SET created_by_role = (SELECT a.actor_role FROM audit_log a WHERE a.action = 'case_created' AND a.case_id = cases.case_id "
    "ORDER BY a.seq LIMIT 1) WHERE created_by_role IS NULL",
    "CREATE INDEX idx_cases_patient_token ON cases(patient_token)",
    """CREATE TABLE body_map_events (
    seq           INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id      TEXT NOT NULL UNIQUE,
    case_id       TEXT NOT NULL REFERENCES cases(case_id),
    regions_json  TEXT NOT NULL,
    consent_seq   INTEGER NOT NULL REFERENCES consent_events(seq),
    actor_id      TEXT NOT NULL,
    actor_role    TEXT NOT NULL,
    created_at    TEXT NOT NULL
)""",
    "CREATE INDEX idx_body_map_case ON body_map_events(case_id, seq)",
    *_append_only("body_map_events"),
)

# ── Step 13: clinical encounter summary (docs/19). A case-level, source-linked summary assembled deterministically from
# every stored source (no model call). Each generation is an append-only snapshot keyed by the hash of the source
# inventory it read, so a later change anywhere in the case marks it stale. Review events (accepted / needs_changes)
# are bound to one summary, name the event they supersede, and never touch triage_runs or review_events.
# Rollback: older code ignores both tables.
ENCOUNTER_SUMMARY_STATEMENTS: tuple[str, ...] = (
    """CREATE TABLE encounter_summaries (
    seq               INTEGER PRIMARY KEY AUTOINCREMENT,
    summary_id        TEXT NOT NULL UNIQUE,
    case_id           TEXT NOT NULL REFERENCES cases(case_id),
    triage_run_id     TEXT REFERENCES triage_runs(run_id),
    recorded_urgency  TEXT CHECK (recorded_urgency IN ('RED', 'YELLOW', 'GREEN')),  -- the rules engine's, copied
    inventory_sha256  TEXT NOT NULL,
    schema_version    TEXT NOT NULL,
    status            TEXT NOT NULL CHECK (status IN ('complete', 'partial')),
    summary_json      TEXT NOT NULL,
    consent_seq       INTEGER NOT NULL REFERENCES consent_events(seq),
    actor_id          TEXT NOT NULL,
    actor_role        TEXT NOT NULL,
    created_at        TEXT NOT NULL
)""",
    "CREATE INDEX idx_encounter_summaries_case ON encounter_summaries(case_id, seq)",
    """CREATE TABLE encounter_summary_review_events (
    seq                    INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id               TEXT NOT NULL UNIQUE,
    summary_id             TEXT NOT NULL REFERENCES encounter_summaries(summary_id),
    case_id                TEXT NOT NULL REFERENCES cases(case_id),
    outcome                TEXT NOT NULL CHECK (outcome IN ('accepted', 'needs_changes')),
    acknowledged_conflicts INTEGER NOT NULL CHECK (acknowledged_conflicts IN (0, 1)),
    supersedes             TEXT,
    actor_id               TEXT NOT NULL,
    actor_role             TEXT NOT NULL,
    created_at             TEXT NOT NULL
)""",
    "CREATE INDEX idx_encounter_summary_reviews ON encounter_summary_review_events(summary_id, seq)",
    *_append_only("encounter_summaries"),
    *_append_only("encounter_summary_review_events"),
)

# ── Step 14: patient registry (P1, docs/implementation/p1-patient-registry.md). (a) consent_events gains the `link_visits`
# purpose: the CHECK constraint is widened by the same rebuild as step 3 (foreign keys off for the step, every row
# copied with its seq, index and append-only triggers recreated, foreign_key_check before COMMIT). (b) `patients`: one
# row per patient, identified ONLY by a random patient code the patient holds (no name, no ID number). (c)
# `patient_link_events`: append-only link/unlink of a case (visit) to a patient; the current link of a case is its
# latest event. Nothing is merged or rewritten; a wrong link is undone by an unlink event. Rollback: older code ignores
# both tables and never grants `link_visits`.
PATIENT_REGISTRY_STATEMENTS: tuple[str, ...] = (
    """CREATE TABLE consent_events_v14 (
    seq                   INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id              TEXT NOT NULL UNIQUE,
    case_id               TEXT NOT NULL REFERENCES cases(case_id),
    purpose               TEXT NOT NULL CHECK (purpose IN ('triage', 'ai_assist', 'voice_cloud', 'link_visits')),
    action                TEXT NOT NULL CHECK (action IN ('granted', 'declined', 'withdrawn')),
    notice_version        TEXT NOT NULL,
    language              TEXT NOT NULL,
    notice_review_status  TEXT NOT NULL,
    method                TEXT NOT NULL,
    actor_id              TEXT NOT NULL,
    actor_role            TEXT NOT NULL,
    created_at            TEXT NOT NULL
)""",
    "INSERT INTO consent_events_v14 (seq, event_id, case_id, purpose, action, notice_version, language, notice_review_status, method, actor_id, actor_role, created_at) "
    "SELECT seq, event_id, case_id, purpose, action, notice_version, language, notice_review_status, method, actor_id, actor_role, created_at FROM consent_events ORDER BY seq",
    "DROP TABLE consent_events",
    "ALTER TABLE consent_events_v14 RENAME TO consent_events",
    "CREATE INDEX idx_consent_events_case ON consent_events(case_id, purpose, seq)",
    *_append_only("consent_events"),
    """CREATE TABLE patients (
    patient_id       TEXT PRIMARY KEY,
    patient_code     TEXT NOT NULL UNIQUE,
    facility_code    TEXT NOT NULL,
    created_by       TEXT NOT NULL,
    created_by_role  TEXT NOT NULL,
    created_at       TEXT NOT NULL
)""",
    """CREATE TABLE patient_link_events (
    seq          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id     TEXT NOT NULL UNIQUE,
    patient_id   TEXT NOT NULL REFERENCES patients(patient_id),
    case_id      TEXT NOT NULL REFERENCES cases(case_id),
    action       TEXT NOT NULL CHECK (action IN ('link', 'unlink')),
    basis        TEXT CHECK (basis IS NULL OR basis IN ('new_code', 'patient_code')),
    reason_code  TEXT CHECK (reason_code IS NULL OR reason_code IN ('linked_in_error', 'patient_request')),
    consent_seq  INTEGER REFERENCES consent_events(seq),
    actor_id     TEXT NOT NULL,
    actor_role   TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    CHECK ((action = 'link' AND basis IS NOT NULL AND consent_seq IS NOT NULL AND reason_code IS NULL)
        OR (action = 'unlink' AND reason_code IS NOT NULL AND basis IS NULL))
)""",
    "CREATE INDEX idx_patient_link_case ON patient_link_events(case_id, seq)",
    "CREATE INDEX idx_patient_link_patient ON patient_link_events(patient_id, seq)",
    *_append_only("patients"),
    *_append_only("patient_link_events"),
)

# ── Step 15: server-managed sessions (docs/security/post-remediation-hardening.md A2) ─────────────────────────────
# Opaque session tokens replace long-lived JWTs. Only SHA-256(token) is stored, never the token. Rows are kept for
# audit (no DELETE). Identity, lifetime ceiling and a revocation, once set, can never be changed.
SESSION_STATEMENTS: tuple[str, ...] = (
    """CREATE TABLE sessions (
    session_id           TEXT PRIMARY KEY,
    token_sha256         TEXT NOT NULL UNIQUE,
    user_id              TEXT NOT NULL,
    username             TEXT NOT NULL,
    role                 TEXT NOT NULL,
    auth_mode            TEXT NOT NULL CHECK (auth_mode IN ('demo', 'oidc')),
    issuer               TEXT,
    subject              TEXT,
    facilities_json      TEXT,
    created_at           TEXT NOT NULL,
    last_seen_at         TEXT NOT NULL,
    idle_expires_at      TEXT NOT NULL,
    absolute_expires_at  TEXT NOT NULL,
    revoked_at           TEXT,
    revoked_reason       TEXT CHECK (revoked_reason IS NULL OR revoked_reason IN ('logout', 'user_revoked', 'user_revoked_all', 'admin_revoked')),
    revoked_by           TEXT,
    CHECK (idle_expires_at <= absolute_expires_at),
    CHECK ((revoked_at IS NULL) = (revoked_reason IS NULL)),
    CHECK ((auth_mode = 'oidc') = (issuer IS NOT NULL AND subject IS NOT NULL))
)""",
    "CREATE INDEX idx_sessions_user ON sessions(user_id)",
    """CREATE TRIGGER sessions_no_delete BEFORE DELETE ON sessions
    BEGIN SELECT RAISE(ABORT, 'sessions are kept for audit'); END""",
    """CREATE TRIGGER sessions_immutable BEFORE UPDATE ON sessions
    WHEN NEW.session_id IS NOT OLD.session_id OR NEW.token_sha256 IS NOT OLD.token_sha256 OR NEW.user_id IS NOT OLD.user_id
      OR NEW.username IS NOT OLD.username OR NEW.role IS NOT OLD.role OR NEW.auth_mode IS NOT OLD.auth_mode
      OR NEW.issuer IS NOT OLD.issuer OR NEW.subject IS NOT OLD.subject OR NEW.facilities_json IS NOT OLD.facilities_json
      OR NEW.created_at IS NOT OLD.created_at
      OR NEW.absolute_expires_at IS NOT OLD.absolute_expires_at
      OR (OLD.revoked_at IS NOT NULL AND (NEW.revoked_at IS NOT OLD.revoked_at OR NEW.revoked_reason IS NOT OLD.revoked_reason
          OR NEW.revoked_by IS NOT OLD.revoked_by OR NEW.idle_expires_at IS NOT OLD.idle_expires_at))
    BEGIN SELECT RAISE(ABORT, 'session identity, lifetime ceiling and revocation are immutable'); END""",
)

# ── Step 16: patient identity binding (post-remediation hardening A3, owner decision H3) ──────────────────────────
# A real (OIDC) patient login is bound to a registered patient only by an explicit, single-use code that the visit's
# ANM issues. Codes are stored as SHA-256 only and can be used once. Bindings are append-only events: the current
# binding of a login is its latest event. Nothing is matched or merged automatically.
PATIENT_IDENTITY_STATEMENTS: tuple[str, ...] = (
    """CREATE TABLE patient_binding_codes (
    binding_id      TEXT PRIMARY KEY,
    code_sha256     TEXT NOT NULL UNIQUE,
    patient_id      TEXT NOT NULL REFERENCES patients(patient_id),
    case_id         TEXT NOT NULL REFERENCES cases(case_id),
    issued_by       TEXT NOT NULL,
    issued_by_role  TEXT NOT NULL,
    issued_at       TEXT NOT NULL,
    expires_at      TEXT NOT NULL,
    used_at         TEXT,
    used_by         TEXT,
    CHECK (issued_at < expires_at),
    CHECK ((used_at IS NULL) = (used_by IS NULL))
)""",
    """CREATE TRIGGER patient_binding_codes_no_delete BEFORE DELETE ON patient_binding_codes
    BEGIN SELECT RAISE(ABORT, 'patient_binding_codes is append-only'); END""",
    """CREATE TRIGGER patient_binding_codes_use_once BEFORE UPDATE ON patient_binding_codes
    WHEN OLD.used_at IS NOT NULL OR NEW.binding_id IS NOT OLD.binding_id OR NEW.code_sha256 IS NOT OLD.code_sha256
      OR NEW.patient_id IS NOT OLD.patient_id OR NEW.case_id IS NOT OLD.case_id OR NEW.issued_by IS NOT OLD.issued_by
      OR NEW.issued_by_role IS NOT OLD.issued_by_role OR NEW.issued_at IS NOT OLD.issued_at OR NEW.expires_at IS NOT OLD.expires_at
    BEGIN SELECT RAISE(ABORT, 'a binding code can be used once and is otherwise immutable'); END""",
    """CREATE TABLE patient_identity_events (
    seq          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id     TEXT NOT NULL UNIQUE,
    user_id      TEXT NOT NULL,
    issuer       TEXT NOT NULL,
    subject      TEXT NOT NULL,
    patient_id   TEXT NOT NULL REFERENCES patients(patient_id),
    action       TEXT NOT NULL CHECK (action IN ('bind', 'unbind')),
    binding_id   TEXT REFERENCES patient_binding_codes(binding_id),
    reason_code  TEXT CHECK (reason_code IS NULL OR reason_code IN ('patient_request', 'bound_in_error', 'admin')),
    actor_id     TEXT NOT NULL,
    actor_role   TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    CHECK ((action = 'bind' AND binding_id IS NOT NULL AND reason_code IS NULL) OR (action = 'unbind' AND binding_id IS NULL AND reason_code IS NOT NULL))
)""",
    "CREATE INDEX idx_patient_identity_user ON patient_identity_events(user_id, seq)",
    "CREATE INDEX idx_patient_identity_patient ON patient_identity_events(patient_id, seq)",
    *_append_only("patient_identity_events"),
)

# ── Step 17: rate-limit counters (post-remediation hardening A4, owner decision H4) ────────────────────────────────
# Fixed-window counters and temporary blocks, shared by every process using this database file (one host; not
# multi-host). Keys are HMACs of (scope, principal or source address), never the raw user id or IP. Mutable by design.
RATE_LIMIT_STATEMENTS: tuple[str, ...] = (
    """CREATE TABLE rate_limit_counters (
    bucket_key    TEXT NOT NULL,
    window_start  TEXT NOT NULL,
    count         INTEGER NOT NULL CHECK (count >= 0),
    PRIMARY KEY (bucket_key, window_start)
)""",
    """CREATE TABLE rate_limit_blocks (
    bucket_key     TEXT PRIMARY KEY,
    blocked_until  TEXT NOT NULL
)""",
)

MIGRATIONS: tuple[tuple[str, ...], ...] = (BASELINE_STATEMENTS, PRIVACY_STATEMENTS, VOICE_STATEMENTS, OCR_STATEMENTS, OCR_PURGE_STATEMENTS, AI_STATEMENTS,
                                           TRIAGE_INPUT_STATEMENTS, REVIEW_STATEMENTS, TRIAGE_CORRECTION_STATEMENTS, MEDICAL_IMAGE_STATEMENTS,
                                           KEYWORD_RED_FLAG_STATEMENTS, HANDOVER_STATEMENTS, ENCOUNTER_SUMMARY_STATEMENTS, PATIENT_REGISTRY_STATEMENTS,
                                           SESSION_STATEMENTS, PATIENT_IDENTITY_STATEMENTS,
                                           RATE_LIMIT_STATEMENTS)
SCHEMA_VERSION = len(MIGRATIONS)
# Steps that rebuild a referenced table: foreign-key enforcement is switched off around the step (the
# pragma is a no-op inside a transaction), and integrity is re-checked with foreign_key_check before
# COMMIT, so a step that leaves a dangling reference still rolls back.
FK_OFF_STEPS = frozenset({3, 11, 14})

TABLES = ("cases", "consent", "triage_notes", "audit_events", "referrals")
PRIVACY_TABLES = ("consent_events", "triage_runs", "audit_log")
VOICE_TABLES = ("voice_transcriptions", "voice_candidates", "voice_readback_events")
AI_TABLES = ("ai_extraction_runs", "ai_fields", "ai_field_review_events", "ai_note_drafts")
ENCOUNTER_SUMMARY_TABLES = ("encounter_summaries", "encounter_summary_review_events")
OCR_TABLES = ("ocr_documents", "ocr_pages", "ocr_fields", "ocr_attestation_events", "ocr_review_events", "ocr_purge_events")
MEDICAL_IMAGE_TABLES = ("medical_images", "medical_image_acknowledgements", "medical_image_purge_events")


class MigrationError(RuntimeError):
    def __init__(self, step: int):
        super().__init__(f"database migration step {step} failed and was rolled back")
        self.step = step


class SchemaMismatchError(RuntimeError):
    """The database does not match what this code's migrations define (M-1/M-2/M-3). Startup fails closed; the message
    names schema objects only, never data or file paths."""

    def __init__(self, problem: str):
        super().__init__(f"database schema mismatch: {problem}. Nothing was changed. This is a development database: "
                         "rebuild it (delete the file and restart) or migrate it with matching code.")


async def _connect(path: Path, timeout: float = 5.0) -> aiosqlite.Connection:
    # isolation_level must be passed to connect(): setting it afterwards fails across threads.
    conn = await aiosqlite.connect(path, isolation_level=None, timeout=timeout)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys = ON")  # no effect inside a transaction, so set it first
    return conn


@asynccontextmanager
async def transaction(conn: aiosqlite.Connection) -> AsyncIterator[aiosqlite.Connection]:
    """Write transaction: BEGIN IMMEDIATE takes the write lock up front; any exception rolls back."""
    await conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        await conn.execute("ROLLBACK")
        raise
    await conn.execute("COMMIT")


@asynccontextmanager
async def read_transaction(conn: aiosqlite.Connection) -> AsyncIterator[aiosqlite.Connection]:
    """Consistent snapshot for multi-statement reads; rolls back (never commits) on error."""
    await conn.execute("BEGIN")
    try:
        yield conn
    except BaseException:
        await conn.execute("ROLLBACK")
        raise
    await conn.execute("COMMIT")


async def _user_version(conn: aiosqlite.Connection) -> int:
    async with conn.execute("PRAGMA user_version") as cur:
        row = await cur.fetchone()
    return int(row[0]) if row is not None else 0


_SUMMARY_STEP = 13  # main's step 13: encounter summary (ENCOUNTER_SUMMARY_STATEMENTS)


async def _table_exists(conn: aiosqlite.Connection, name: str) -> bool:
    async with conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)) as cur:
        return await cur.fetchone() is not None


async def _repair_skipped_summary_step(conn: aiosqlite.Connection, migrations: tuple[tuple[str, ...], ...]) -> bool:
    """Forward repair for a database migrated by the unmerged contribution 21a9761 (remediation plan §3C). Its own
    "step 13" (gemini_live_*) set user_version 13, so main's step 13 would be skipped by number and the summary tables
    would never exist (later, step 14 still succeeds). Detected by schema, not by number: version >= 13 while
    `encounter_summaries` is missing. Main's step 13 is then applied as-is: additive CREATE TABLE / INDEX / TRIGGER
    statements only, so no existing row is changed or lost. Leftover gemini_live_* tables are kept, unused and unread.
    Returns True when a repair ran; it is logged (counts and step only, never data)."""
    if len(migrations) < _SUMMARY_STEP or migrations[_SUMMARY_STEP - 1] is not ENCOUNTER_SUMMARY_STATEMENTS:
        return False
    version = await _user_version(conn)
    if version < _SUMMARY_STEP or await _table_exists(conn, "encounter_summaries"):
        return False
    try:
        async with transaction(conn):
            if await _table_exists(conn, "encounter_summaries"):  # re-check under the lock: another process repaired it
                return False
            for statement in ENCOUNTER_SUMMARY_STATEMENTS:
                await conn.execute(statement)
            async with conn.execute("PRAGMA foreign_key_check") as cur:
                if await cur.fetchone() is not None:
                    raise MigrationError(_SUMMARY_STEP)
    except MigrationError:
        raise
    except Exception as exc:
        raise MigrationError(_SUMMARY_STEP) from exc
    log.warning("schema_repaired step=%d reason=summary_tables_missing user_version=%d leftover_gemini_tables=%s",
                _SUMMARY_STEP, version, await _table_exists(conn, "gemini_live_sessions"))
    return True


_SCHEMA_CACHE: dict[int, dict] = {}


def _fingerprint_sync(db: sqlite3.Connection) -> dict:
    """Tables with their columns (name, declared type, not-null, primary-key position), plus index and trigger names
    with the table they belong to. Internal sqlite_* objects are ignored."""
    out: dict = {"table": {}, "index": {}, "trigger": {}}
    rows = db.execute("SELECT type, name, tbl_name FROM sqlite_master WHERE type IN ('table', 'index', 'trigger') AND name NOT LIKE 'sqlite_%'").fetchall()
    for typ, name, tbl in rows:
        if typ == "table":
            cols = db.execute(f'PRAGMA table_info("{name}")').fetchall()
            out["table"][name] = sorted((c[1], (c[2] or "").upper(), int(c[3]), int(c[5])) for c in cols)
        else:
            out[typ][name] = tbl
    return out


def _expected_schema(migrations: tuple[tuple[str, ...], ...]) -> dict:
    """What these migrations define: replayed once on an empty in-memory database (no data, so no FK concerns)."""
    key = id(migrations)
    if key not in _SCHEMA_CACHE:
        db = sqlite3.connect(":memory:")
        try:
            db.execute("PRAGMA foreign_keys = OFF")
            for statements in migrations:
                for statement in statements:
                    db.execute(statement)
            _SCHEMA_CACHE[key] = _fingerprint_sync(db)
        finally:
            db.close()
    return _SCHEMA_CACHE[key]


async def _actual_schema(conn: aiosqlite.Connection) -> dict:
    out: dict = {"table": {}, "index": {}, "trigger": {}}
    async with conn.execute("SELECT type, name, tbl_name FROM sqlite_master WHERE type IN ('table', 'index', 'trigger') AND name NOT LIKE 'sqlite_%'") as cur:
        rows = [tuple(r) for r in await cur.fetchall()]
    for typ, name, tbl in rows:
        if typ == "table":
            async with conn.execute(f'PRAGMA table_info("{name}")') as cur:
                cols = await cur.fetchall()
            out["table"][name] = sorted((c[1], (c[2] or "").upper(), int(c[3]), int(c[5])) for c in cols)
        else:
            out[typ][name] = tbl
    return out


async def validate_schema(conn: aiosqlite.Connection, migrations: tuple[tuple[str, ...], ...] = MIGRATIONS) -> None:
    """Every table (with identical columns), index and trigger the migrations define must exist (M-1/M-2). Extra
    objects are allowed: leftovers such as the contribution's unused gemini_live_* tables are kept, never read."""
    expected, actual = _expected_schema(migrations), await _actual_schema(conn)
    problems = []
    for name, cols in expected["table"].items():
        if name not in actual["table"]:
            problems.append(f"table {name} is missing")
        elif actual["table"][name] != cols:
            problems.append(f"table {name} has different columns")
    for typ in ("index", "trigger"):
        for name, tbl in expected[typ].items():
            if actual[typ].get(name) != tbl:
                problems.append(f"{typ} {name} on {tbl} is missing")
    if problems:
        raise SchemaMismatchError("; ".join(sorted(problems)[:10]))


async def run_migrations(conn: aiosqlite.Connection, migrations: tuple[tuple[str, ...], ...] = MIGRATIONS, fk_off_steps: frozenset[int] = FK_OFF_STEPS) -> int:
    """Apply pending steps; each step (DDL + FK check + version bump) is one atomic transaction. A database from newer
    code is refused before anything is changed (M-3). A database that skipped main's step 13 under a colliding step
    number is repaired forward first (`_repair_skipped_summary_step`). Afterwards the schema itself is validated
    against what the migrations define, so `user_version` alone is never trusted (M-1/M-2)."""
    version = await _user_version(conn)
    if version > len(migrations):
        raise SchemaMismatchError(f"user_version {version} is newer than this code's {len(migrations)}")
    await _repair_skipped_summary_step(conn, migrations)
    for step, statements in enumerate(migrations, start=1):
        fk_off = step in fk_off_steps and await _user_version(conn) < step
        if fk_off:
            await conn.execute("PRAGMA foreign_keys = OFF")
        try:
            async with transaction(conn):
                if await _user_version(conn) >= step:  # re-read under the lock: another process may have applied it
                    continue
                for statement in statements:
                    await conn.execute(statement)
                async with conn.execute("PRAGMA foreign_key_check") as cur:
                    if await cur.fetchone() is not None:
                        raise MigrationError(step)
                await conn.execute(f"PRAGMA user_version = {int(step)}")
        except MigrationError:
            raise
        except Exception as exc:
            raise MigrationError(step) from exc
        finally:
            if fk_off:
                await conn.execute("PRAGMA foreign_keys = ON")
    await validate_schema(conn, migrations)
    return await _user_version(conn)


async def init_db(path: Path | None = None) -> None:
    path = path or get_settings().database_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = await _connect(path)
    try:
        await run_migrations(conn)
    finally:
        await conn.close()


async def get_db() -> AsyncIterator[aiosqlite.Connection]:
    """FastAPI dependency: one connection per request."""
    conn = await _connect(get_settings().database_path)
    try:
        yield conn
    finally:
        await conn.close()


async def existing_tables(conn: aiosqlite.Connection) -> set[str]:
    async with conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'") as cur:
        return {row["name"] async for row in cur}
