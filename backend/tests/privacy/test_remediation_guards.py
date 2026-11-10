"""Regression guards for the Semantic Kernel / Gemini Live remediation (docs/integration/semantic-kernel-remediation-plan.md).

Each test pins one defect found in the reviewed contribution 21a9761 so it cannot return:
credentials readable by browser scripts or carried in URLs, an unauthenticated or cross-origin WebSocket, patient audio
to a provider the consent does not name, a second urgency path, a red-flag screen marked complete without answers, a
rules-engine failure reported as GREEN, and exception text in responses. Static scans run over the source tree; the
privacy-boundary tests in this folder stay as they are.
"""

import ast
import itertools
import json
import re
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2] / "app"
FRONTEND_SRC = Path(__file__).resolve().parents[3] / "frontend" / "src"


def _routes(routes):
    """Every route, including those inside included routers (newer FastAPI wraps them in an `_IncludedRouter`)."""
    for r in routes:
        inner = getattr(r, "original_router", None)
        if inner is not None:
            yield from _routes(inner.routes)
        else:
            yield r


def _sources(root: Path, exts: tuple[str, ...]):
    for p in root.rglob("*"):
        if p.suffix in exts and "node_modules" not in p.parts and ".next" not in p.parts:
            yield p, p.read_text(encoding="utf-8")


# ── credentials ────────────────────────────────────────────────────────────────────────────────────────────

def test_no_route_hands_the_session_token_to_browser_scripts():
    assert not (FRONTEND_SRC / "app" / "api" / "session" / "token").exists()
    for p, src in _sources(FRONTEND_SRC, (".ts", ".tsx")):
        assert "session/token" not in src, p


def test_no_frontend_url_carries_a_credential_or_bypasses_the_same_origin_proxy():
    bad = re.compile(r"[?&](token|access_token|jwt|key)=|:8000\b")
    for p, src in _sources(FRONTEND_SRC, (".ts", ".tsx")):
        if p.name.endswith(".test.ts"):
            continue
        # The one allowed :8000 is the server-side proxy's default target (lib/auth.ts API_BASE_URL, read only by the
        # Next.js route handlers under app/api/); the browser never connects to it directly.
        lines = [ln for ln in src.splitlines() if not ln.lstrip().startswith("export const API_BASE_URL =")]
        hits = [m.group(0) for m in bad.finditer("\n".join(lines))]
        assert not hits, (p, hits)


def test_no_backend_code_puts_an_api_key_in_a_url():
    for p, src in _sources(APP, (".py",)):
        assert not re.search(r"[?&]key=\{", src), p


def test_no_websocket_endpoint_exists():
    """D7: there is no WebSocket route. Any future one must authenticate without the URL or a bare cookie and must
    validate Origin against an explicit allowlist; this test is the reminder that a new route needs its own review."""
    from fastapi.routing import APIWebSocketRoute

    from app.main import create_app

    ws = [r.path for r in _routes(create_app().routes) if isinstance(r, APIWebSocketRoute)]
    assert ws == []


# ── consent and provider routing ───────────────────────────────────────────────────────────────────────────

def test_no_code_path_sends_patient_audio_to_google():
    """D1/D2: patient audio goes only to the on-device engine or, with voice_cloud consent, to Sarvam AI."""
    for p, src in _sources(APP, (".py",)):
        for marker in ("BidiGenerateContent", "live-followup", "generativelanguage.googleapis.com/ws", "gemini_live"):
            if marker == "gemini_live" and p.name == "database.py":
                continue  # the step-13 repair names the leftover gemini_live_* table only to detect and keep it
            assert marker not in src, (p, marker)


def test_kernel_registers_no_cloud_chat_service_even_with_a_google_key(monkeypatch):
    from app.config import get_settings
    from app.services.kernel import build_kernel

    monkeypatch.setenv("GOOGLE_AI_API_KEY", "test-key-not-real")
    get_settings.cache_clear()
    try:
        kernel = build_kernel(get_settings())
        ids = set(getattr(kernel, "services", {}) or {})
        assert not any("gemini" in str(i).lower() for i in ids), ids
    finally:
        get_settings.cache_clear()
    src = (APP / "services" / "kernel.py").read_text(encoding="utf-8")
    assert "generativelanguage" not in src


def test_spoken_readback_is_only_the_deterministic_template(monkeypatch):
    """Nothing a model writes is spoken to the patient: TTS text comes from readback.readback_text only."""
    tree = ast.parse((APP / "voice" / "tts.py").read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", getattr(n.func, "attr", "")) == "synthesize"]
    assert calls, "synthesize call not found"
    assigns = {t.id: n.value for n in ast.walk(tree) if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)}
    text_src = assigns.get("text")
    assert isinstance(text_src, ast.Call) and getattr(text_src.func, "attr", "") == "readback_text"


# ── clinical safety ────────────────────────────────────────────────────────────────────────────────────────

def test_no_second_urgency_path():
    """D3: urgency is produced only by POST /triage (and shown, as recorded, by review views)."""
    from app.main import create_app

    paths = [getattr(r, "path", "") for r in _routes(create_app().routes)]
    assert any(p.endswith("/triage") for p in paths)  # the walk really sees the API routes
    assert not [p for p in paths if "orchestrate" in p]
    assert not (APP / "services" / "orchestrator.py").exists()


def test_no_code_marks_the_red_flag_screen_complete_by_itself():
    pat = re.compile(r"[\"']red_flag_screen_completed[\"']\s*:\s*True|red_flag_screen_completed\s*=\s*True")
    for p, src in _sources(APP, (".py",)):
        assert not pat.search(src), p


def test_harness_never_reports_green_on_a_rules_engine_failure_or_an_unscreened_patient():
    from app.services.plugins import TriageRulesPlugin

    plugin = TriageRulesPlugin()
    broken = json.loads(plugin.evaluate_triage("{not json"))
    assert broken["status"] == "evaluation_failed" and "final_urgency" not in broken
    assert "error" not in broken or not re.search(r"Expecting|Traceback|line \d", str(broken.get("error")))  # no exception text
    unscreened = {"scenario": "opd", "age_years": 40, "red_flag_screen_completed": False, "red_flags_present": [],
                  "vitals": {"resp_rate": 16, "spo2": 98, "pulse": 78, "sbp": 124, "dbp": 80, "temp_c": 36.8, "consciousness": "A"}}
    assert json.loads(plugin.evaluate_triage(json.dumps(unscreened)))["final_urgency"] != "GREEN"


@pytest.mark.parametrize(("det", "sug"), list(itertools.product(["GREEN", "YELLOW", "RED"], ["GREEN", "YELLOW", "RED", ""])))
def test_harness_raise_only_never_lowers_urgency(det, sug):
    from app.services.plugins import TriageRulesPlugin
    from app.rules.urgency import Urgency

    out = json.loads(TriageRulesPlugin().apply_raise_only_urgency(det, sug))
    assert Urgency(out["effective_urgency"]).rank >= Urgency(det).rank


def test_plugins_never_return_or_log_exception_text():
    src = (APP / "services" / "plugins.py").read_text(encoding="utf-8")
    assert "str(exc)" not in src and "str(e)" not in src
    assert "_case_records" not in src  # no in-process store of patient narratives


def test_triage_route_never_reports_green_when_the_engine_fails(client, monkeypatch):
    from tests.privacy.helpers import auth, grant, new_case, token_for

    anm = token_for(client, "anm")
    cid = new_case(client, anm)
    assert grant(client, anm, cid).status_code == 200

    def boom(*_a, **_k):
        raise RuntimeError("engine down")
    monkeypatch.setattr("app.case_triage.evaluate_triage", boom, raising=False)
    monkeypatch.setattr("app.rules.engine.evaluate_triage", boom)
    body = {"scenario": "opd", "age_years": 40, "red_flag_screen_completed": True,
            "vitals": {"resp_rate": 16, "spo2": 98, "pulse": 78, "sbp": 124, "dbp": 80, "temp_c": 36.8, "consciousness": "A"}}
    r = client.post(f"/api/v1/cases/{cid}/triage", json=body, headers=auth(anm))
    assert r.status_code >= 500 and "GREEN" not in r.text and "engine down" not in r.text
