"""Backend side of the local OCR worker (backend/ocr_worker/worker.py, run with .venv-ocr): Surya OCR 2 and
Chandra OCR 2 (docs/14 §4).

- Spawned lazily by the backend with a fresh random token (env, never .env/logs) and a Unix socket in a
  0700 directory; the worker creates the socket 0600. No TCP port.
- Every call has a hard deadline: on timeout the worker process is KILLED (and respawned on next use), so a
  stuck engine cannot keep running. This is real cancellation, unlike in-process inference threads.
- Errors map to fixed reason codes; worker output never reaches logs.
"""

from __future__ import annotations

import os
import queue
import secrets
import signal
import subprocess
import threading
import time
from pathlib import Path

import httpx

from app.ocr.engine import EngineError


class OcrWorker:
    def __init__(self, python: Path, script: Path, run_dir: Path, models_dir: Path, start_timeout_s: float = 60.0):
        self.python, self.script, self.run_dir, self.models_dir = python, script, run_dir, models_dir
        self.start_timeout_s = start_timeout_s
        self._proc: subprocess.Popen | None = None
        self._token = ""
        self._lock = threading.Lock()

    @property
    def socket_path(self) -> Path:
        return self.run_dir / "ocr.sock"

    def installed(self) -> bool:
        return self.python.is_file() and self.script.is_file()

    def _start(self) -> None:
        if not self.installed():
            raise EngineError("runtime_not_installed")
        if len(str(self.socket_path).encode()) > 100:  # macOS AF_UNIX path limit is 104 bytes
            raise EngineError("socket_path_too_long")
        self.run_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.run_dir, 0o700)
        self._token = secrets.token_hex(32)
        env = {k: v for k, v in os.environ.items() if not k.startswith(("SARVAM", "AZURE", "OPENAI", "JWT"))}
        env.update({"SEHAT_OCR_WORKER_TOKEN": self._token, "SEHAT_OCR_SOCKET": str(self.socket_path), "SEHAT_OCR_MODELS": str(self.models_dir),
                    "SEHAT_OCR_PARENT_PID": str(os.getpid())})  # the worker exits if this process dies
        self._proc = subprocess.Popen([str(self.python), str(self.script)], env=env, stdin=subprocess.DEVNULL,
                                      stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                      start_new_session=True)  # own process group: llama-server dies with it
        # Wait for "ready" without blocking on readline() (a worker that hangs while loading must not hang
        # the API): a reader thread hands lines over, then keeps draining stdout so the pipe never fills.
        lines: queue.Queue[str] = queue.Queue()
        out = self._proc.stdout

        def _reader() -> None:
            for raw in iter(out.readline, ""):  # type: ignore[union-attr]
                lines.put(raw.strip())

        threading.Thread(target=_reader, daemon=True).start()
        deadline = time.monotonic() + self.start_timeout_s
        while time.monotonic() < deadline and self._proc.poll() is None:
            try:
                if lines.get(timeout=0.5) == "ready":
                    return
            except queue.Empty:
                continue
        self.stop()
        raise EngineError("worker_start_failed")

    def _kill_recorded_children(self) -> None:
        """Engine children started in their own session (Surya's llama-server) — only if the PID still
        belongs to llama-server, so a recycled PID is never killed."""
        pid_file = self.run_dir / "children.pid"
        try:
            pids = [int(x) for x in pid_file.read_text().split()] if pid_file.is_file() else []
        except (OSError, ValueError):
            pids = []
        for pid in pids:
            try:
                comm = subprocess.run(["ps", "-p", str(pid), "-o", "comm="], capture_output=True, text=True, timeout=5).stdout.strip()
                if comm.endswith("llama-server"):
                    os.kill(pid, signal.SIGKILL)
            except (OSError, subprocess.SubprocessError):
                pass
        pid_file.unlink(missing_ok=True)

    def stop(self) -> None:
        if self._proc is not None:
            if self._proc.poll() is None:
                self._proc.terminate()  # graceful: the worker unloads engines and stops llama-server
                try:
                    self._proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
            try:
                os.killpg(self._proc.pid, signal.SIGKILL)  # anything left in the worker's group
            except (ProcessLookupError, PermissionError):
                pass
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
        self._kill_recorded_children()
        self._proc = None
        self._token = ""
        self.socket_path.unlink(missing_ok=True)  # a killed worker cannot remove its own socket

    def call(self, path: str, body: bytes | None, timeout_s: float) -> dict:
        with self._lock:  # one request at a time: one large engine resident on a 16 GB machine
            if self._proc is None or self._proc.poll() is not None:
                self._start()
            transport = httpx.HTTPTransport(uds=str(self.socket_path))
            headers = {"X-SEHAT-Worker-Token": self._token}
            try:
                with httpx.Client(transport=transport, base_url="http://ocr-worker", timeout=timeout_s) as client:
                    resp = client.get(path, headers=headers) if body is None else client.post(path, content=body, headers=headers)
            except httpx.TimeoutException:
                self.stop()  # hard cancellation: the engine process is killed
                raise EngineError("ocr_timeout", 504) from None
            except httpx.HTTPError:
                self.stop()
                raise EngineError("worker_unavailable") from None
            if resp.status_code == 200:
                try:
                    data = resp.json()
                except ValueError:
                    raise EngineError("engine_failed", 500) from None
                if not isinstance(data, dict):
                    raise EngineError("engine_failed", 500)
                return data
            reason = (resp.json() or {}).get("error", "engine_failed") if resp.headers.get("content-type", "").startswith("application/json") else "engine_failed"
            raise EngineError(reason if reason in ("model_not_installed", "runtime_not_installed", "model_integrity_failed") else "engine_failed",
                              503 if resp.status_code == 503 else 500)


_workers: dict[str, OcrWorker] = {}


def get_worker(python: Path, script: Path, run_dir: Path, models_dir: Path) -> OcrWorker:
    key = f"{python}|{script}|{run_dir}"
    if key not in _workers:
        _workers[key] = OcrWorker(python, script, run_dir, models_dir)
    return _workers[key]


def shutdown_all() -> None:
    for w in _workers.values():
        w.stop()
