"""Local OCR worker process lifecycle (app/ocr/worker_client.py). Uses tiny stand-in worker scripts with the
same protocol (Unix socket + token); the real Surya/Chandra engines are covered by tests/ocr/test_live.py."""

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from app.ocr.engine import EngineError
from app.ocr.worker_client import OcrWorker

SERVER = textwrap.dedent('''
    import json, os, sys, time
    from http.server import BaseHTTPRequestHandler
    from socketserver import ThreadingMixIn, UnixStreamServer
    MODE = os.environ["FAKE_MODE"]
    TOKEN = os.environ["SEHAT_OCR_WORKER_TOKEN"]
    if MODE == "hang_start":
        time.sleep(600)
    if MODE == "crash_start":
        sys.exit(3)
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def _send(self, code, body, ctype="application/json"):
            self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0); self.rfile.read(n)
            if self.headers.get("X-SEHAT-Worker-Token") != TOKEN:
                return self._send(401, b'{"error":"unauthorized"}')
            if MODE == "slow":
                time.sleep(600)
            if MODE == "garbage":
                return self._send(200, b"not json", "application/json")
            if MODE == "crash_mid":
                os._exit(9)
            if MODE == "child":
                import subprocess
                p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
                open(os.environ["FAKE_CHILD_PID"], "w").write(str(p.pid))
                time.sleep(600)
            return self._send(200, json.dumps({"blocks": [], "pid": os.getpid()}).encode())
    class S(ThreadingMixIn, UnixStreamServer):
        daemon_threads = True
    sock = os.environ["SEHAT_OCR_SOCKET"]
    if os.path.exists(sock): os.unlink(sock)
    srv = S(sock, H); os.chmod(sock, 0o600)
    print("ready", flush=True)
    srv.serve_forever()
''')


@pytest.fixture
def make_worker(tmp_path, monkeypatch):
    script = tmp_path / "fake_worker.py"
    script.write_text(SERVER)
    import tempfile

    run = __import__("pathlib").Path(tempfile.mkdtemp(prefix="sw", dir="/tmp"))  # short: AF_UNIX paths ≤ 104 bytes on macOS
    made = []

    def _make(mode: str, start_timeout_s: float = 10.0) -> OcrWorker:
        monkeypatch.setenv("FAKE_MODE", mode)
        monkeypatch.setenv("FAKE_CHILD_PID", str(tmp_path / "child.pid"))
        w = OcrWorker(__import__("pathlib").Path(sys.executable), script, run, tmp_path, start_timeout_s=start_timeout_s)
        made.append(w)
        return w

    yield _make
    for w in made:
        w.stop()
    import shutil

    shutil.rmtree(run, ignore_errors=True)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def test_success_and_socket_is_owner_only(make_worker):
    import stat

    w = make_worker("ok")
    out = w.call("/surya/page", b"png", 10)
    assert out["blocks"] == []
    assert stat.S_IMODE(os.stat(w.socket_path).st_mode) == 0o600 and stat.S_IMODE(os.stat(w.run_dir).st_mode) == 0o700


def test_start_hang_is_bounded_and_does_not_block_the_next_call(make_worker):
    w = make_worker("hang_start", start_timeout_s=2.0)
    t = time.monotonic()
    with pytest.raises(EngineError) as exc:
        w.call("/surya/page", b"png", 10)
    assert exc.value.reason == "worker_start_failed" and time.monotonic() - t < 6
    assert w._proc is None  # killed, lock released
    with pytest.raises(EngineError):
        w.call("/surya/page", b"png", 10)  # a second call is not stuck behind the first


def test_crash_before_ready(make_worker):
    w = make_worker("crash_start", start_timeout_s=5.0)
    with pytest.raises(EngineError) as exc:
        w.call("/surya/page", b"png", 10)
    assert exc.value.reason == "worker_start_failed"


def test_processing_timeout_kills_worker_and_next_call_restarts(make_worker, monkeypatch):
    w = make_worker("slow")
    with pytest.raises(EngineError) as exc:
        w.call("/surya/page", b"png", 1.0)
    assert exc.value.reason == "ocr_timeout" and exc.value.status == 504
    assert w._proc is None and not w.socket_path.exists()
    monkeypatch.setenv("FAKE_MODE", "ok")
    assert w.call("/surya/page", b"png", 10)["blocks"] == []  # restarted cleanly


def test_crash_during_processing_and_garbage_reply_are_explicit_errors(make_worker):
    w = make_worker("crash_mid")
    with pytest.raises(EngineError) as exc:
        w.call("/surya/page", b"png", 10)
    assert exc.value.reason == "worker_unavailable"
    g = make_worker("garbage")
    with pytest.raises(EngineError) as exc2:
        g.call("/surya/page", b"png", 10)
    assert exc2.value.reason == "engine_failed"


def test_no_orphan_child_after_timeout(make_worker, tmp_path):
    w = make_worker("child")
    with pytest.raises(EngineError):
        w.call("/surya/page", b"png", 2.0)
    pid = int((tmp_path / "child.pid").read_text())
    for _ in range(50):
        if not _alive(pid):
            break
        time.sleep(0.1)
    assert not _alive(pid)  # the worker's process group (incl. its children) was killed


def test_wrong_token_is_refused(make_worker):
    import httpx

    w = make_worker("ok")
    w.call("/surya/page", b"png", 10)
    with httpx.Client(transport=httpx.HTTPTransport(uds=str(w.socket_path)), base_url="http://x") as c:
        assert c.post("/surya/page", content=b"x", headers={"X-SEHAT-Worker-Token": "nope"}).status_code == 401


def test_overlong_socket_path_fails_clearly(tmp_path):
    w = OcrWorker(__import__("pathlib").Path(sys.executable), tmp_path / "x.py", tmp_path / ("d" * 120), tmp_path)
    (tmp_path / "x.py").write_text("")
    with pytest.raises(EngineError) as exc:
        w.call("/surya/page", b"png", 1)
    assert exc.value.reason == "socket_path_too_long"


@pytest.mark.parametrize("engine", ["surya", "chandra"])
def test_engine_files_with_wrong_hash_are_refused(tmp_path, engine):
    """Fail closed: tampered or substituted model files are never loaded (worker _verify_engine)."""
    import json
    from pathlib import Path

    models = tmp_path / "m"
    g = models / "hf-home/hub/models--datalab-to--surya-ocr-2-gguf/snapshots/6a3a4c30e5e74446d4f8b6afd05b2f2da970f470"
    g.mkdir(parents=True)
    (g / "surya-2.gguf").write_bytes(b"not the pinned model")
    (g / "surya-2-mmproj.gguf").write_bytes(b"x")
    c = models / "chandra-ocr-2-mlx-8bit"
    c.mkdir(parents=True)
    (c / "model.safetensors").write_bytes(b"substituted")
    (c / "SEHAT_ENGINE_MANIFEST.json").write_text(json.dumps({"files": {"model.safetensors": "0" * 64}}))
    worker_dir = Path(__file__).parents[2] / "ocr_worker"
    code = f"import sys; sys.path.insert(0, {str(worker_dir)!r}); import worker\n" \
           f"try:\n    worker._verify_engine({engine!r})\nexcept worker.IntegrityError:\n    print('refused')\n"
    out = subprocess.run([sys.executable, "-c", code], env={**os.environ, "SEHAT_OCR_MODELS": str(models)}, capture_output=True, text=True, timeout=60)
    assert out.stdout.strip() == "refused", out.stderr[-500:]


def test_worker_exits_when_its_backend_dies(tmp_path):
    """A backend that is killed (no graceful shutdown) must not leave the worker resident (seen 2026-10-02)."""
    worker = Path(__file__).parents[2] / "ocr_worker" / "worker.py"
    pid_file = tmp_path / "worker.pid"
    # an intermediate "backend" spawns a process running worker.watch_parent, then dies without cleanup
    child = textwrap.dedent(f'''
        import os, sys, threading, time, importlib.util
        spec = importlib.util.spec_from_file_location("w", {str(worker)!r}); w = importlib.util.module_from_spec(spec); spec.loader.exec_module(w)
        open({str(pid_file)!r}, "w").write(str(os.getpid()))
        w.watch_parent(os.getppid(), lambda: os._exit(0), interval_s=0.1)
        time.sleep(60)
    ''')
    backend = subprocess.Popen([sys.executable, "-c", f"import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', {child!r}], start_new_session=True); time.sleep(60)"])
    deadline = time.monotonic() + 10
    while not pid_file.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    pid = int(pid_file.read_text())
    backend.kill()
    backend.wait()
    deadline = time.monotonic() + 5
    alive = True
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
            time.sleep(0.1)
        except ProcessLookupError:
            alive = False
            break
    if alive:
        os.kill(pid, 9)
    assert not alive, "worker outlived its backend"
