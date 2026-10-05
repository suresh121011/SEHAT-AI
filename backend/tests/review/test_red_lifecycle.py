"""RED lifecycle, acknowledgment/sign-off concurrency and migration 9 on a populated v8 database (docs/17 §5, §10).

Written by the independent safety review (Agent A) as executable repros and adopted into the suite: the first two
tests reproduced a real defect (an acknowledged RED dropped from RED queue priority after a lower re-run) before the
RED-hold fix. Synthetic data only; concurrency tests use two real database connections.
"""

import asyncio, sqlite3, uuid
import pytest
from app import review_queue
from app.auth import Principal, Role, demo_user_id
from app.config import get_settings
from app.database import MIGRATIONS, SCHEMA_VERSION, _connect, run_migrations
from tests.privacy.helpers import audit_rows, auth
from tests.review.test_review_api import (anm, mo, sup, clock, make_case, rerun, q, review, sign_off, override, ack, db, item_for,
                                          RED, YELLOW, GREEN, API)

MO = Principal(user_id=demo_user_id("mo_demo"), username="mo_demo", role=Role.MEDICAL_OFFICER)

def gov(client, sup):
    return client.get(f"{API}/audit/governance", headers=auth(sup)).json()["red_escalation"]

# V1 candidate: ack + lower re-triage (ANM can re-run) drops RED priority without any sign-off
def test_ack_then_lower_rerun_keeps_red_priority(client, anm, mo):
    cid, r1 = make_case(client, anm, RED)
    assert ack(client, mo, cid, r1).status_code == 200
    rerun(client, anm, cid, GREEN)
    it = item_for(q(client, mo).json(), cid)
    assert it["review_status"] == "awaiting_review"
    assert it["priority_urgency"] == "RED", (it["priority_urgency"], it["priority_source"], it["escalation"])

def test_raised_red_ack_then_rerun_keeps_red_priority(client, anm, mo):
    cid, r1 = make_case(client, anm, YELLOW)
    assert override(client, mo, cid, r1, "RED").status_code == 200
    assert ack(client, mo, cid, r1).status_code == 200
    rerun(client, anm, cid, YELLOW)
    it = item_for(q(client, mo).json(), cid)
    assert it["priority_urgency"] == "RED", (it["priority_urgency"], it["priority_source"], it["escalation"])

def test_unacked_raise_survives_rerun(client, anm, mo):
    cid, r1 = make_case(client, anm, YELLOW)
    assert override(client, mo, cid, r1, "RED").status_code == 200
    rerun(client, anm, cid, GREEN)
    it = item_for(q(client, mo).json(), cid)
    assert it["priority_urgency"] == "RED" and it["escalation"]["state"] == "pending"
    assert it["escalation"]["anchor_source"] == "earlier_red_run"

def test_reraise_new_episode_distinct_deadline_and_two_overdue(client, anm, mo, sup, clock):
    cid, r1 = make_case(client, anm, YELLOW)
    assert override(client, mo, cid, r1, "RED").status_code == 200
    d1 = item_for(q(client, mo).json(), cid)["escalation"]["deadline"]
    clock.advance(200); q(client, mo); q(client, mo)
    assert ack(client, mo, cid, r1).status_code == 200
    assert override(client, mo, cid, r1, "YELLOW").status_code == 200
    it = item_for(q(client, mo).json(), cid)
    assert it["escalation"]["state"] == "acknowledged" and it["priority_urgency"] == "RED"
    clock.advance(5)
    assert override(client, mo, cid, r1, "RED").status_code == 200
    e2 = item_for(q(client, mo).json(), cid)["escalation"]
    assert e2["state"] == "pending" and e2["deadline"] != d1
    clock.advance(200); q(client, mo); q(client, mo)
    assert ack(client, mo, cid, r1).status_code == 200
    assert ack(client, mo, cid, r1).status_code == 409
    over = [a for a in audit_rows(client) if a["action"] == "red_escalation_overdue" and a["case_id"] == cid]
    assert len(over) == 2
    g1, g2 = gov(client, sup), gov(client, sup)
    assert g1 == g2 and g1["episodes"] == 2 and g1["acknowledged_late"] == 2
    # re-triage keeps both episodes and both overrides in governance + history
    rerun(client, anm, cid, GREEN)
    g3 = gov(client, sup)
    assert g3["episodes"] == 2
    ov = client.get(f"{API}/audit/governance", headers=auth(sup)).json()["overrides"]
    assert ov["events"] == 3
    ev = review(client, mo, cid).json()["review_events"]
    assert sum(e["kind"] == "override" for e in ev) == 3

def test_signoff_on_new_run_closes_only_carried_episode(client, anm, mo, sup):
    cid, r1 = make_case(client, anm, RED)
    assert ack(client, mo, cid, r1).status_code == 200
    r2 = rerun(client, anm, cid, RED)  # new episode
    e = item_for(q(client, mo).json(), cid)["escalation"]
    assert e["state"] == "pending" and e["anchor_source"] == "triage_run"
    assert sign_off(client, mo, cid, r2).status_code == 200
    g = gov(client, sup)
    assert g["episodes"] == 2 and g["acknowledged"] == 2

async def _race(path, cid, rid, kinds):
    async def one(k):
        conn = await _connect(path)
        try:
            if k == "ack":
                return await review_queue.acknowledge(conn, MO, cid, review_queue.AcknowledgeBody(triage_run_id=uuid.UUID(rid)), None)
            return await review_queue.sign_off(conn, MO, cid, review_queue.SignOffBody(triage_run_id=uuid.UUID(rid), confirm=True), None)
        except Exception as exc:
            return exc
        finally:
            await conn.close()
    return await asyncio.gather(*(one(k) for k in kinds))

@pytest.mark.parametrize("kinds", [("ack", "sign"), ("sign", "ack"), ("ack", "ack"), ("ack", "ack", "sign", "sign")])
@pytest.mark.parametrize("n", range(5))
def test_concurrent_ack_signoff(client, anm, mo, kinds, n):
    cid, rid = make_case(client, anm, RED)
    res = asyncio.run(_race(get_settings().database_path, cid, rid, kinds))
    errs = [r for r in res if not isinstance(r, dict)]
    assert all(getattr(e, "status_code", None) == 409 for e in errs), res
    with db() as c:
        ev = [r["kind"] for r in c.execute("SELECT kind FROM review_events WHERE triage_run_id=? ORDER BY seq", (rid,))]
    assert ev.count("sign_off") <= 1 and ev.count("acknowledge") <= 1
    assert not (ev and ev[0] == "sign_off" and "acknowledge" in ev), ev  # no ack after sign-off closed the episode
    acks = [a for a in audit_rows(client) if a["action"] == "red_escalation_acknowledged" and a["case_id"] == cid]
    assert len(acks) == 1, (ev, len(acks))
    it = item_for(q(client, mo, include_signed_off="true").json(), cid)
    assert it["escalation"]["state"] == "acknowledged"
    assert it["escalation"]["acknowledged_via"] == ev[0]

# Migration 9 on a populated v8 DB
def _fill(c, table, **vals):
    cols = c.execute(f"PRAGMA table_info({table})").fetchall()
    row = {}
    for _, name, typ, notnull, dflt, pk in cols:
        if name in vals: row[name] = vals[name]
        elif notnull and dflt is None and not pk: row[name] = 1 if "INT" in typ.upper() else "x"
    c.execute(f"INSERT INTO {table} ({','.join(row)}) VALUES ({','.join('?'*len(row))})", list(row.values()))

def test_migration_9_on_populated_v8(tmp_path):
    p = tmp_path / "v8.db"
    async def mig(m):
        conn = await _connect(p)
        try: return await run_migrations(conn, m)
        finally: await conn.close()
    assert asyncio.run(mig(MIGRATIONS[:8])) == 8
    c = sqlite3.connect(p); c.execute("PRAGMA foreign_keys=OFF")
    _fill(c, "cases", case_id="c1")
    ce = {r[1] for r in c.execute("PRAGMA table_info(consent_events)")}
    print("CE", ce)
    _fill(c, "consent_events", case_id="c1", **{k: v for k, v in dict(purpose="triage", action="granted", status="granted", event="grant").items() if k in ce})
    _fill(c, "triage_runs", run_id="r1", case_id="c1", urgency="RED", created_at="2026-01-01T00:00:00.000000+00:00")
    for i, k in enumerate(["override", "acknowledge", "sign_off"]):
        kw = dict(old_urgency="RED", new_urgency="YELLOW", reason_code="other") if k == "override" else {}
        _fill(c, "review_events", event_id=f"e{i}", case_id="c1", triage_run_id="r1", kind=k, rules_urgency="RED", created_at=f"2026-01-01T00:00:0{i+1}", **kw)
    c.commit(); print("FKCHECK", c.execute("PRAGMA foreign_key_check").fetchall()); c.close()
    assert asyncio.run(mig(MIGRATIONS)) == SCHEMA_VERSION
    c = sqlite3.connect(p)
    assert c.execute("SELECT COUNT(*) FROM review_events").fetchone()[0] == 3
    idx = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "idx_review_events_one_ack" not in idx and "idx_review_events_one_sign_off" in idx and "idx_triage_runs_one_correction" in idx
    for sql in ("UPDATE review_events SET kind='acknowledge'", "DELETE FROM review_events", "UPDATE triage_runs SET urgency='GREEN'", "DELETE FROM triage_runs"):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            c.execute(sql)
    # older build: all 8 steps skipped on a fully migrated DB
    c.close(); assert asyncio.run(mig(MIGRATIONS[:8])) == SCHEMA_VERSION
    c = sqlite3.connect(p)
    # rollback text: index recreatable when no run has 2 acks; fails after a second ack
    c.execute("CREATE UNIQUE INDEX idx_review_events_one_ack ON review_events(triage_run_id) WHERE kind = 'acknowledge'")
    c.execute("DROP INDEX idx_review_events_one_ack")
    _fill(c, "review_events", event_id="e9", case_id="c1", triage_run_id="r1", kind="acknowledge", rules_urgency="RED", created_at="2026-01-01T00:00:09")
    c.commit()
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("CREATE UNIQUE INDEX idx_review_events_one_ack ON review_events(triage_run_id) WHERE kind = 'acknowledge'")
    # doc claims columns cannot be dropped without rebuild; check DROP COLUMN
    out = {}
    for col in ("correction_reason_text", "correction_reason_code", "corrects_run_id"):
        try: c.execute(f"ALTER TABLE triage_runs DROP COLUMN {col}"); out[col] = "dropped"
        except sqlite3.Error as e: out[col] = str(e)
    print("DROPCOL", out)
