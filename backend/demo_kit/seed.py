"""Seed a running backend with the synthetic demo cases, through the public API exactly as the app does: a health
worker creates the case and records consent, the rules engine triages it, a medical officer signs off and refers. No
database is written directly, so consent, audit, facility scope and the append-only rules all apply.

Refuses a backend that is not on this computer, and a database that already has triaged cases at any facility, signed
off or not (unless told otherwise), so it cannot fill the owner's working database by accident.
"""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urlsplit

import httpx

from demo_kit import cases as dc

LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})
API = "/api/v1"
ACCOUNTS = {"anm": ("anm_demo", "anm"), "mo": ("mo_demo", "medical_officer"), "supervisor": ("supervisor_demo", "supervisor")}


class SeedRefused(Exception):
    pass


def loopback_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or parts.hostname not in LOOPBACK:
        raise SeedRefused(f"{url}: the demo kit talks only to this computer (127.0.0.1, localhost or ::1)")
    return url.rstrip("/")


def login(client: httpx.Client, prefix: str, username: str, role: str) -> dict[str, str]:
    r = client.post(f"{prefix}/auth/login", json={"username": username, "role": role})
    if r.status_code != 200:
        raise SeedRefused(f"demo login as {username} failed ({r.status_code}); is AUTH_MODE=demo and ENVIRONMENT=development?")
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _ok(r: httpx.Response, what: str) -> dict:
    if r.status_code >= 300:
        code = (r.json().get("error") or {}).get("code") if r.headers.get("content-type", "").startswith("application/json") else None
        raise SeedRefused(f"{what} failed: HTTP {r.status_code}{f' {code}' if code else ''}")
    return r.json()


def facility(client: httpx.Client, prefix: str, anm: dict, mo: dict) -> str:
    scopes = [_ok(client.get(f"{prefix}/auth/me", headers=h), "reading the account").get("facility_scope") for h in (anm, mo)]
    anm_scope, mo_scope = scopes
    code = anm_scope[0] if anm_scope else dc.FACILITY
    if anm_scope == [] or (mo_scope is not None and code not in mo_scope):
        raise SeedRefused("anm_demo and mo_demo do not share a facility (ACCOUNT_FACILITIES); the queue would be empty")
    return code


def seed(client: httpx.Client, *, prefix: str = API, allow_existing: bool = False,
         log: Callable[[str], object] = print) -> list[dict]:
    anm = login(client, prefix, *ACCOUNTS["anm"])
    mo = login(client, prefix, *ACCOUNTS["mo"])
    # Emptiness is judged by the supervisor (every facility) including signed-off cases, so a used database with only
    # signed-off cases, or with cases at other facilities, is still refused.
    supervisor = login(client, prefix, *ACCOUNTS["supervisor"])
    queue = _ok(client.get(f"{prefix}/triage/queue", params={"include_signed_off": "true"}, headers=supervisor),
                "reading the queue").get("items", [])
    if queue and not allow_existing:
        raise SeedRefused(f"the database is not empty ({len(queue)} triaged case(s)). Seed only a fresh scratch database, "
                          "or pass --allow-existing.")
    code = facility(client, prefix, anm, mo)
    version = _ok(client.get(f"{prefix}/consent/notice", params={"language": "en"}, headers=anm), "reading the notice")["version"]
    report = []
    for case in dc.CASES:
        cid = _ok(client.post(f"{prefix}/cases", json={"scenario": case.triage["scenario"], "facility_code": code}, headers=anm),
                  f"{case.key}: creating the case")["case_id"]
        _ok(client.post(f"{prefix}/cases/{cid}/consent", headers=anm,
                        json={"decision": "grant", "include_ai_assist": False, "include_voice_cloud": False, "language": "en",
                              "notice_version": version}), f"{case.key}: consent")
        run = _ok(client.post(f"{prefix}/cases/{cid}/triage", json=case.triage, headers=anm), f"{case.key}: triage")
        urgency = run["result"]["urgency"]
        if urgency != case.urgency:
            raise SeedRefused(f"{case.key}: the rules engine gave {urgency}, the demo expects {case.urgency}. The rules changed; "
                              "update demo_kit/cases.py and docs/07 together.")
        entry = {"key": case.key, "title": case.title, "case_id": cid, "urgency": urgency, "beat": case.beat}
        if case.sign_off:
            _ok(client.patch(f"{prefix}/triage/{cid}/sign-off", json={"triage_run_id": run["run_id"], "confirm": True}, headers=mo),
                f"{case.key}: sign-off")
            entry["signed_off"] = True
        if case.referral is not None:
            ref = case.referral
            body = {"triage_run_id": run["run_id"], "to_facility": ref.to_facility, "reason_code": ref.reason_code,
                    "transport_mode": ref.transport_mode, "escort_required": ref.escort_required,
                    "travel_minutes": ref.travel_minutes, "insurance_scheme": ref.insurance_scheme, "confirm": True}
            rid = _ok(client.post(f"{prefix}/cases/{cid}/referrals", json=body, headers=mo), f"{case.key}: referral")["referral_id"]
            current = "referred"
            for status in ref.statuses:
                _ok(client.post(f"{prefix}/cases/{cid}/referrals/{rid}/status", headers=mo,
                                json={"status": status, "expected_status": current, "detail_code": None, "confirm": True}),
                    f"{case.key}: referral status {status}")
                current = status
            entry["referral_status"] = current
        report.append(entry)
        log(f"  {urgency:<6} {case.title}  ({case.beat})")
    return report
