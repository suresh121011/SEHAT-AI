"""The pre-demo check: is the running stack the one the demo needs? Read-only apart from two stateless rules-engine
calls (`/triage/process` stores nothing). Every failure says how to fix it, in plain words."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import httpx

from demo_kit import cases as dc
from demo_kit.seed import ACCOUNTS, API, SeedRefused, login

Status = Literal["PASS", "WARN", "FAIL"]


@dataclass(frozen=True)
class Check:
    name: str
    status: Status
    detail: str = ""
    fix: str = ""


def exit_code(checks: list[Check]) -> int:
    return 1 if any(c.status == "FAIL" for c in checks) else 0


def _web_get(url: str) -> int:
    return httpx.get(url, timeout=10, follow_redirects=True).status_code


def run_checks(client: httpx.Client, *, prefix: str = API, expect_provider: str = "local", web: str | None = "http://127.0.0.1:3010",
               web_get: Callable[[str], int] = _web_get) -> list[Check]:
    out: list[Check] = []
    try:
        health = client.get(f"{prefix}/health").json()
    except (httpx.HTTPError, ValueError) as exc:
        return [Check("backend", "FAIL", type(exc).__name__, "Start the backend: make demo (or make dev-backend).")]
    ok = health.get("status") == "ok" and health.get("database") == "ok"
    out.append(Check("backend", "PASS" if ok else "FAIL", f"database: {health.get('database')}",
                     "" if ok else "The database is missing tables; start the backend on a fresh scratch database."))

    provider = health.get("ai_provider")
    out.append(Check("extraction model", "PASS" if provider == expect_provider else "FAIL", f"provider {provider}",
                     "" if provider == expect_provider else f"Start the backend with AI_PROVIDER={expect_provider} (make demo sets it)."))

    try:
        anm = login(client, prefix, *ACCOUNTS["anm"])
        mo = login(client, prefix, *ACCOUNTS["mo"])
    except SeedRefused as exc:
        out.append(Check("demo accounts", "FAIL", str(exc), "Run the backend with ENVIRONMENT=development and AUTH_MODE=demo."))
        return out
    out.append(Check("demo accounts", "PASS", "anm_demo and mo_demo can sign in"))

    caps = client.get(f"{prefix}/ai/capabilities", headers=anm).json()
    ready = caps.get("ready") is True
    out.append(Check("model ready", "PASS" if ready else ("FAIL" if expect_provider == "local" else "WARN"),
                     f"ready={caps.get('ready')} {caps.get('not_ready_reason') or ''}".strip(),
                     "" if ready else "Start the local model first: make llm-start (it takes about a minute to load)."))
    rails = (caps.get("guardrails") or {}).get("enabled") is True
    out.append(Check("guardrails", "PASS" if rails else "WARN", "NeMo Guardrails " + ("on" if rails else "off"),
                     "" if rails else "Recommended on for the demo (docs/16 §2b): GUARDRAILS_ENABLED=1 (make demo sets it)."))
    keywords = (caps.get("keyword_red_flags") or {}).get("enabled") is True
    out.append(Check("red-flag keyword suggester", "PASS" if keywords else "WARN", "on" if keywords else "off",
                     "" if keywords else "AI_KEYWORD_RED_FLAGS=1 (make demo sets it)."))

    azure = (health.get("azure_chat") or {}).get("state")
    out.append(Check("clinical assistant (needs network)", "PASS" if azure == "ready" else "WARN", f"Azure chat {azure}",
                     "" if azure == "ready" else "Expected when offline: skip the assistant beat, or run make demo ONLINE=1 with network."))

    for name, body, want, rule in (
        ("rules: demo patient is RED (ATP severe pain)", dc.DEMO_PATIENT, "RED", "ATP_RED_SEVERE_PAIN"),
        ("rules: no red-flag screen stays off GREEN", {**dc.DEMO_PATIENT, "red_flag_screen_completed": False, "red_flags_present": []},
         "YELLOW", "SAFETY_FLOOR_INSUFFICIENT_DATA"),
    ):
        r = client.post(f"{prefix}/triage/process", json=body, headers=anm)
        res = r.json() if r.status_code == 200 else {}
        good = res.get("urgency") == want and rule in {t["rule_id"] for t in res.get("triggered_rules", [])}
        out.append(Check(name, "PASS" if good else "FAIL", f"{res.get('urgency')} via {rule if good else '?'}",
                         "" if good else "The rules changed: update docs/07 and demo_kit/cases.py before the demo."))

    items = client.get(f"{prefix}/triage/queue", headers=mo).json().get("items", [])
    levels = {u: sum(1 for i in items if i.get("priority_urgency") == u) for u in ("RED", "YELLOW", "GREEN")}
    seeded = all(levels.values())
    out.append(Check("seeded queue", "PASS" if seeded else "WARN", ", ".join(f"{k} {v}" for k, v in levels.items()),
                     "" if seeded else "Seed the demo cases: make demo-seed (a fresh scratch database only)."))

    out.append(_ocr_check(client.get(f"{prefix}/intake/document/capabilities", headers=anm)))
    out.append(_voice_check(client.get(f"{prefix}/voice/capabilities", headers=anm)))

    if web:
        try:
            status = web_get(f"{web}/login")
            out.append(Check("frontend", "PASS" if status == 200 else "FAIL", f"{web} HTTP {status}",
                             "" if status == 200 else "Start the frontend: make demo (or make dev-frontend)."))
        except Exception as exc:  # noqa: BLE001 - any failure to reach it is the same advice
            out.append(Check("frontend", "FAIL", type(exc).__name__, "Start the frontend: make demo (or make dev-frontend)."))
    return out


def _ready(caps: dict) -> set[str]:
    return {k for k, e in (caps.get("engines") or {}).items() if isinstance(e, dict) and e.get("ready") is True}


# The report beat (docs/07 Beat 3) says "engines agree": that needs both printed-text engines, not just one.
PRINTED_ENGINES = frozenset({"paddleocr", "surya"})


def _ocr_check(r: httpx.Response) -> Check:
    caps = r.json() if r.status_code == 200 else {}
    ready = _ready(caps)
    name = "document reading (OCR)"
    if caps.get("ocr_enabled") is not True:
        return Check(name, "WARN", "off", "OCR_ENABLED=1 and its engines in the developer settings if the demo uses the report beat; otherwise skip it.")
    if PRINTED_ENGINES <= ready:
        return Check(name, "PASS", f"on ({', '.join(sorted(ready))} ready)")
    if ready:
        return Check(name, "WARN", f"one printed-text engine only ({', '.join(sorted(ready))})",
                     "Do not say 'engines agree' in Beat 3: only one engine read the page. Enable Surya and PaddleOCR in the developer settings.")
    return Check(name, "WARN", "switched on, no engine ready", "Check the OCR engine settings and models (make doctor); otherwise skip the report beat.")


def _voice_check(r: httpx.Response) -> Check:
    caps = r.json() if r.status_code == 200 else {}
    ready = _ready(caps)
    on = caps.get("voice_enabled") is True and bool(ready)  # the switch alone is not enough: a speech engine must be ready
    detail = f"on ({', '.join(sorted(ready))} ready)" if on else ("switched on, no engine ready" if caps.get("voice_enabled") else "off")
    return Check("voice", "PASS" if on else "WARN", detail,
                 "" if on else "VOICE_ENABLED=1 and the local speech engine in the developer settings if the demo uses voice; otherwise type.")


def print_checks(checks: list[Check]) -> None:
    for c in checks:
        print(f"{c.status:<4}  {c.name}: {c.detail}" + (f"\n      fix: {c.fix}" if c.fix else ""))
    fails, warns = sum(c.status == "FAIL" for c in checks), sum(c.status == "WARN" for c in checks)
    print(f"\n{len(checks) - fails - warns} passed, {warns} warning(s), {fails} failure(s)." +
          (" Ready for the demo." if not fails else " Not ready: fix the failures above."))
