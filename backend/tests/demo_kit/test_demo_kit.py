"""Demo kit (docs/07, Phase 7): the seeded synthetic demo cases, the pre-demo check and the one-command start.
The seed and the check run here against the real app in-process on a throwaway database. Synthetic data only."""

import socket
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.rules import TriageInput, evaluate_triage
from demo_kit import cases as dc
from demo_kit import preflight, seed, stack
from tests.rules.test_demo_vignette import DEMO

API = "/api/v1"
REPO = Path(__file__).resolve().parents[3]


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "demo.db"))
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-key-with-enough-length-for-hs256")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("AI_PROVIDER", "fake")
    monkeypatch.setenv("AI_KEYWORD_RED_FLAGS", "1")
    for name in ("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT"):
        monkeypatch.setenv(name, "")
    from app.config import get_settings

    get_settings.cache_clear()
    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()


# ── The seeded cases ─────────────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("case", dc.CASES, ids=lambda c: c.key)
def test_each_seed_case_gets_its_stated_urgency_and_rules_from_the_engine(case):
    result = evaluate_triage(TriageInput(**case.triage))
    assert result.urgency.value == case.urgency
    assert set(case.rules) <= {t.rule_id for t in result.triggered_rules}


def test_the_seed_covers_red_yellow_and_green_and_the_two_docs_07_cases():
    assert {c.urgency for c in dc.CASES} == {"RED", "YELLOW", "GREEN"}
    by_key = {c.key: c for c in dc.CASES}
    assert by_key["demo_patient"].triage == DEMO  # the docs/07 Beat 4 patient, pinned by tests/rules/test_demo_vignette.py
    floor = by_key["safety_floor"].triage
    assert floor["vitals"] == DEMO["vitals"] and floor["red_flag_screen_completed"] is False and floor["red_flags_present"] == []


def test_seed_cases_are_synthetic_and_carry_no_identifiers():
    from app.privacy.pii import residual_hit

    for case in dc.CASES:
        assert not residual_hit(case.title), case.key
        assert "name" not in str(case.triage).lower()


# ── Seeding through the real API ─────────────────────────────────────────────────────────────────────────────────


def test_seed_creates_every_case_through_the_api_with_consent_rules_and_sign_off(app_client):
    report = seed.seed(app_client, prefix=API, log=lambda *_: None)
    assert [r["key"] for r in report] == [c.key for c in dc.CASES]
    assert all(r["urgency"] == c.urgency for r, c in zip(report, dc.CASES))

    mo = seed.login(app_client, API, "mo_demo", "medical_officer")
    queue = app_client.get(f"{API}/triage/queue", headers=mo).json()["items"]
    ours = [i for i in queue if i["case_id"] in {r["case_id"] for r in report}]
    ranks = {"RED": 3, "YELLOW": 2, "GREEN": 1}
    assert [ranks[i["priority_urgency"]] for i in ours] == sorted((ranks[i["priority_urgency"]] for i in ours), reverse=True)

    referred = next(r for r in report if r["key"] == "referral_in_transit")
    refs = app_client.get(f"{API}/cases/{referred['case_id']}/referrals", headers=mo).json()
    assert [x["status"] for x in refs["referrals"]] == ["in_transit"]


def test_seed_refuses_a_database_that_already_has_cases(app_client):
    seed.seed(app_client, prefix=API, log=lambda *_: None)
    with pytest.raises(seed.SeedRefused, match="not empty"):
        seed.seed(app_client, prefix=API, log=lambda *_: None)
    assert len(seed.seed(app_client, prefix=API, allow_existing=True, log=lambda *_: None)) == len(dc.CASES)


def test_seed_refuses_a_database_whose_only_cases_are_signed_off(app_client):
    """The queue hides signed-off cases by default; a used database must still be refused."""
    anm = seed.login(app_client, API, "anm_demo", "anm")
    mo = seed.login(app_client, API, "mo_demo", "medical_officer")
    cid = app_client.post(f"{API}/cases", json={"scenario": "opd", "facility_code": "PHC-KHURDA-01"}, headers=anm).json()["case_id"]
    version = app_client.get(f"{API}/consent/notice?language=en", headers=anm).json()["version"]
    app_client.post(f"{API}/cases/{cid}/consent", headers=anm, json={"decision": "grant", "include_ai_assist": False,
                    "include_voice_cloud": False, "language": "en", "notice_version": version}).raise_for_status()
    run = app_client.post(f"{API}/cases/{cid}/triage", json=dc.CASES[4].triage, headers=anm).json()
    app_client.patch(f"{API}/triage/{cid}/sign-off", json={"triage_run_id": run["run_id"], "confirm": True}, headers=mo).raise_for_status()
    assert app_client.get(f"{API}/triage/queue", headers=mo).json()["items"] == []  # hidden by default
    with pytest.raises(seed.SeedRefused, match="not empty"):
        seed.seed(app_client, prefix=API, log=lambda *_: None)


@pytest.mark.parametrize("url", ["http://10.0.0.5:8000", "https://example.org", "http://sehat.local:8010", "ftp://127.0.0.1:8010"])
def test_seed_and_check_talk_only_to_this_computer(url):
    with pytest.raises(seed.SeedRefused):
        seed.loopback_url(url)


@pytest.mark.parametrize("url", ["http://127.0.0.1:8010", "http://localhost:8010/", "http://[::1]:8010"])
def test_loopback_urls_are_accepted(url):
    assert seed.loopback_url(url).startswith("http")


# ── The pre-demo check ───────────────────────────────────────────────────────────────────────────────────────────


def _by_name(checks):
    return {c.name: c for c in checks}


def test_preflight_passes_the_rules_beats_and_reports_what_is_off(app_client):
    seed.seed(app_client, prefix=API, log=lambda *_: None)
    checks = _by_name(preflight.run_checks(app_client, prefix=API, expect_provider="fake", web=None))
    assert checks["backend"].status == "PASS"
    assert checks["rules: demo patient is RED (ATP severe pain)"].status == "PASS"
    assert checks["rules: no red-flag screen stays off GREEN"].status == "PASS"
    assert checks["seeded queue"].status == "PASS"
    assert checks["extraction model"].status == "PASS"
    assert checks["guardrails"].status == "WARN"  # off in tests; recommended on for the demo (docs/16 §2b)
    # OCR and voice are off in tests: the check must read the real switches, not pass on a missing field
    assert checks["document reading (OCR)"].status == "WARN" and checks["voice"].status == "WARN"
    assert "OCR_ENABLED" in checks["document reading (OCR)"].fix and "VOICE_ENABLED" in checks["voice"].fix
    assert not [c for c in checks.values() if c.status == "FAIL"]


def test_preflight_fails_when_the_wrong_model_is_configured_and_says_how_to_fix_it(app_client):
    checks = _by_name(preflight.run_checks(app_client, prefix=API, expect_provider="local", web=None))
    assert checks["extraction model"].status == "FAIL" and "AI_PROVIDER" in checks["extraction model"].fix


def test_preflight_warns_on_an_empty_queue(app_client):
    assert _by_name(preflight.run_checks(app_client, prefix=API, expect_provider="fake", web=None))["seeded queue"].status == "WARN"


def test_preflight_checks_the_frontend_when_given_one(app_client):
    ok = preflight.run_checks(app_client, prefix=API, expect_provider="fake", web="http://127.0.0.1:3010",
                              web_get=lambda url: 200)
    down = preflight.run_checks(app_client, prefix=API, expect_provider="fake", web="http://127.0.0.1:3010",
                                web_get=lambda url: (_ for _ in ()).throw(OSError("refused")))
    assert _by_name(ok)["frontend"].status == "PASS" and _by_name(down)["frontend"].status == "FAIL"


class _Resp:
    def __init__(self, data, status=200):
        self._data, self.status_code = data, status

    def json(self):
        return self._data


def _ocr(enabled, ready):
    engines = {k: {"enabled": enabled, "ready": k in ready} for k in ("paddleocr", "surya", "chandra")}
    return _Resp({"ocr_enabled": enabled, "engines": engines})


def test_ocr_passes_only_when_both_printed_text_engines_are_ready():
    """Beat 3 says 'engines agree'; with one engine that sentence would be false."""
    assert preflight._ocr_check(_ocr(True, {"paddleocr", "surya"})).status == "PASS"
    one = preflight._ocr_check(_ocr(True, {"paddleocr", "chandra"}))
    assert one.status == "WARN" and "Do not say 'engines agree'" in one.fix
    assert preflight._ocr_check(_ocr(True, set())).status == "WARN"
    assert preflight._ocr_check(_ocr(False, {"paddleocr", "surya"})).status == "WARN"
    assert preflight._ocr_check(_Resp({}, 404)).status == "WARN"


def test_voice_passes_only_with_the_switch_on_and_a_speech_engine_ready():
    def voice(enabled, ready):
        return _Resp({"voice_enabled": enabled, "engines": {"local": {"ready": ready}, "cloud": {"ready": False}}})

    assert preflight._voice_check(voice(True, True)).status == "PASS"
    assert preflight._voice_check(voice(True, False)).status == "WARN"
    assert preflight._voice_check(voice(False, True)).status == "WARN"


def test_preflight_exit_code_is_one_only_on_a_failure():
    assert preflight.exit_code([preflight.Check("a", "PASS"), preflight.Check("b", "WARN")]) == 0
    assert preflight.exit_code([preflight.Check("a", "PASS"), preflight.Check("b", "FAIL")]) == 1


# ── The one-command start ────────────────────────────────────────────────────────────────────────────────────────


def test_the_demo_backend_runs_offline_on_a_scratch_database(tmp_path):
    env = stack.backend_env({"PATH": "/usr/bin", "AI_CLOUD_ENABLED": "1", "VOICE_CLOUD_STT_ENABLED": "1"}, tmp_path,
                            provider="local", online=False, web_port=3010)
    assert env["DATABASE_PATH"] == str(tmp_path / "sehat-demo.db") and env["OCR_DOCUMENT_DIR"] == str(tmp_path / "docs")
    assert (env["ENVIRONMENT"], env["AI_PROVIDER"], env["AI_CLOUD_ENABLED"], env["VOICE_CLOUD_STT_ENABLED"]) == (
        "development", "local", "0", "0")
    assert env["GUARDRAILS_ENABLED"] == "1" and env["AI_KEYWORD_RED_FLAGS"] == "1"
    assert env["CORS_ALLOWED_ORIGINS"] == "http://127.0.0.1:3010" and "SEHAT_DOTENV" not in env
    assert env["LOCAL_LLM_URL"] == "http://127.0.0.1:8091"


def test_offline_forces_every_cloud_path_off_including_spoken_read_back(tmp_path):
    """Spoken read-back (Sarvam) would send the read-back text off the laptop; offline means none of it."""
    on = {"AI_CLOUD_ENABLED": "1", "VOICE_CLOUD_STT_ENABLED": "1", "VOICE_TTS_ENABLED": "1"}
    env = stack.backend_env(on, tmp_path, provider="local", online=False, web_port=3010)
    assert {k: env[k] for k in on} == {"AI_CLOUD_ENABLED": "0", "VOICE_CLOUD_STT_ENABLED": "0", "VOICE_TTS_ENABLED": "0"}
    assert set(stack.OFFLINE) == set(on)


def test_the_frontend_sends_no_telemetry_and_never_installs_packages():
    assert stack.frontend_env({}, 8010) == {"NEXT_PUBLIC_API_URL": "http://127.0.0.1:8010/api/v1", "NEXT_TELEMETRY_DISABLED": "1"}
    front = stack.processes(Path("/venv/bin/python"), REPO, with_llm=False, api_port=8010, web_port=3010)[-1]
    assert front.argv[:2] == ["npx", "--no-install"]


def test_each_backend_port_has_its_own_scratch_folder():
    assert stack.scratch_root(8010) != stack.scratch_root(8020)
    assert stack.check_scratch(stack.scratch_root(8020) / "sehat-demo.db")


def test_stop_all_stops_the_children_and_their_own_children(tmp_path):
    import os
    import signal
    import subprocess
    import sys
    import time

    script = ("import subprocess, sys, time\n"
              "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
              "print(g.pid, flush=True)\n"
              "time.sleep(120)\n")
    child = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True, start_new_session=True)
    grandchild = int(child.stdout.readline())
    saved = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
    try:
        stack.stop_all([(stack.Proc("x", [], tmp_path, 0), child)])
    finally:
        for s, handler in saved.items():
            signal.signal(s, handler)
    assert child.poll() is not None

    def running(pid: int) -> bool:  # a zombie has stopped and only waits to be reaped; it counts as stopped
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
        return bool(state) and not state.startswith("Z")

    for _ in range(50):
        if not running(grandchild):
            break
        time.sleep(0.1)
    else:
        pytest.fail("the grandchild process was left running")


def test_a_worktree_uses_the_main_checkouts_model_folder_and_the_main_checkout_uses_its_own(tmp_path):
    worktree, main = tmp_path / "wt", tmp_path / "main"
    (main / "models" / "llm").mkdir(parents=True)
    worktree.mkdir()
    assert stack.model_env(worktree, main) == {"LOCAL_LLM_MODEL_DIR": str(main / "models" / "llm")}
    assert stack.model_env(main, main) == {}
    assert stack.model_env(worktree, tmp_path / "nowhere") == {}


def test_the_demo_keeps_facility_isolation_on_with_its_own_mapping(tmp_path):
    env = stack.backend_env({"ALLOW_UNSCOPED_DEMO": "true"}, tmp_path, provider="local", online=False, web_port=3010)
    assert "ALLOW_UNSCOPED_DEMO" not in env
    from app.config import _account_facilities

    mapping = _account_facilities(env["ACCOUNT_FACILITIES"])  # parsed by the backend's own strict parser
    assert mapping["anm_demo"] == mapping["mo_demo"] == frozenset({"PHC-KHURDA-01"}) and mapping["supervisor_demo"] is None


def test_online_mode_leaves_the_cloud_switches_to_the_developer_settings(tmp_path):
    env = stack.backend_env({"AI_CLOUD_ENABLED": "1"}, tmp_path, provider="local", online=True, web_port=3010)
    assert env["AI_CLOUD_ENABLED"] == "1" and "VOICE_CLOUD_STT_ENABLED" not in env and "VOICE_TTS_ENABLED" not in env


@pytest.mark.parametrize("bad", ["sehat.db", "backend/sehat-demo.db"])
def test_the_demo_never_uses_the_shared_database_or_a_file_in_the_repo(bad):
    with pytest.raises(stack.StackRefused):
        stack.check_scratch(REPO / bad)


def test_a_scratch_folder_outside_the_repo_is_accepted(tmp_path):
    assert stack.check_scratch(tmp_path / "sehat-demo.db") == tmp_path / "sehat-demo.db"


def test_every_process_listens_on_this_computer_only(tmp_path):
    procs = stack.processes(Path("/venv/bin/python"), REPO, with_llm=True, api_port=8010, web_port=3010)
    assert [p.name for p in procs] == ["local model", "backend", "frontend"]
    for p in procs[1:]:
        assert "127.0.0.1" in p.argv
    assert "0.0.0.0" not in " ".join(" ".join(p.argv) for p in procs)
    assert stack.processes(Path("/venv/bin/python"), REPO, with_llm=False, api_port=8010, web_port=3010)[0].name == "backend"


def test_a_busy_port_is_detected():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        assert stack.port_in_use(s.getsockname()[1]) is True
    free = socket.socket()
    free.bind(("127.0.0.1", 0))
    port = free.getsockname()[1]
    free.close()
    assert stack.port_in_use(port) is False
