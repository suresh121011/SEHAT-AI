"""One-command demo stack (`make demo`): local model, backend and frontend on this computer only, a scratch database
outside the repository, offline by default. Seeds the demo cases, runs the pre-demo check, then waits; Ctrl-C (or a
plain `kill`, or closing the terminal) stops every process it started, including their own child processes. Logs go to
the scratch folder.

Settings: the kit sets what the demo needs and forces it over the developer `.env`:
- scratch database and document folder;
- extraction provider and model address;
- guardrails and the keyword suggester;
- the demo facility mapping, with isolation on;
- offline: cloud AI, cloud speech-to-text and cloud spoken read-back all off.

The backend reads `.env` for everything else (OCR, voice and image engines). The kit never reads or prints `.env`.
In a worktree the local model script writes its one-time API key file into the main checkout's `models/llm` folder,
because that is where the model is.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HOST = "127.0.0.1"
LLM_PORT = 8091
# The demo's own facility mapping (as in .env.example): isolation on, every demo account at the demo PHC, the supervisor
# across facilities. Set explicitly so the demo is the same on every laptop and in a worktree without a `.env`.
DEMO_FACILITIES = "anm_demo=PHC-KHURDA-01;mo_demo=PHC-KHURDA-01;supervisor_demo=*;patient_demo=PHC-KHURDA-01"
# Forced off in the offline demo whatever `.env` says: nothing may leave the laptop.
OFFLINE = {"AI_CLOUD_ENABLED": "0", "VOICE_CLOUD_STT_ENABLED": "0", "VOICE_TTS_ENABLED": "0"}


class StackRefused(Exception):
    pass


@dataclass(frozen=True)
class Proc:
    name: str
    argv: list[str]
    cwd: Path
    port: int


def scratch_root(api_port: int = 8010) -> Path:
    """One scratch folder per backend port, so a second demo on other ports never deletes the first one's database."""
    return Path(os.environ.get("TMPDIR") or tempfile.gettempdir()) / f"sehat-demo-{api_port}"


def check_scratch(db: Path) -> Path:
    """The demo database must be a scratch file: never the shared `sehat.db`, never a file inside the repository."""
    resolved = db.resolve()
    if resolved.name == "sehat.db":
        raise StackRefused("the demo never uses sehat.db")
    if resolved == REPO.resolve() or REPO.resolve() in resolved.parents:
        raise StackRefused(f"{db} is inside the repository; use a scratch folder (default {scratch_root()})")
    return db


def main_checkout(repo: Path = REPO) -> Path | None:
    """The main checkout of this repository (a worktree shares its git directory), or None outside git."""
    try:
        out = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=repo, capture_output=True,
                             text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return Path(out).parent if out else None


def model_env(repo: Path = REPO, main: Path | None = None) -> dict[str, str]:
    """Model files are not in git. A worktree has none, so point the backend at the main checkout's local model folder,
    as the Makefile does for the venv. In the main checkout itself nothing is set and the developer settings apply."""
    if (repo / "models" / "llm").is_dir():
        return {}
    main = main or main_checkout(repo)
    if main is not None and (main / "models" / "llm").is_dir():
        return {"LOCAL_LLM_MODEL_DIR": str(main / "models" / "llm")}
    return {}


def backend_env(base: Mapping[str, str], scratch: Path, *, provider: str, online: bool, web_port: int) -> dict[str, str]:
    db = check_scratch(scratch / "sehat-demo.db")
    env = dict(base)
    env.update({
        "ENVIRONMENT": "development",
        "DATABASE_PATH": str(db),
        "OCR_DOCUMENT_DIR": str(scratch / "docs"),
        "AI_PROVIDER": provider,
        "LOCAL_LLM_URL": f"http://{HOST}:{LLM_PORT}",  # the address the model is started on and the port check uses
        "GUARDRAILS_ENABLED": "1",
        "AI_KEYWORD_RED_FLAGS": "1",
        "CORS_ALLOWED_ORIGINS": f"http://{HOST}:{web_port}",
        "ACCOUNT_FACILITIES": DEMO_FACILITIES,
    })
    env.pop("ALLOW_UNSCOPED_DEMO", None)  # isolation stays on in the demo
    if not online:
        env.update(OFFLINE)
    env.pop("SEHAT_DOTENV", None)  # the backend reads .env itself; process settings above win over it
    return env


def frontend_env(base: Mapping[str, str], api_port: int) -> dict[str, str]:
    return {**base, "NEXT_PUBLIC_API_URL": f"http://{HOST}:{api_port}/api/v1", "NEXT_TELEMETRY_DISABLED": "1"}


def processes(py: Path, repo: Path, *, with_llm: bool, api_port: int, web_port: int) -> list[Proc]:
    backend, frontend = repo / "backend", repo / "frontend"
    procs = [Proc("local model", [str(py), "scripts/start_local_llm.py"], backend, LLM_PORT)] if with_llm else []
    procs.append(Proc("backend", [str(py), "-m", "uvicorn", "app.main:app", "--host", HOST, "--port", str(api_port)], backend, api_port))
    # --no-install: never download a package at demo time; frontend/node_modules must already be there (make setup-frontend)
    procs.append(Proc("frontend", ["npx", "--no-install", "next", "dev", "-H", HOST, "-p", str(web_port)], frontend, web_port))
    return procs


def port_in_use(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex((HOST, port)) == 0


def _wait(url: str, seconds: float, proc: subprocess.Popen) -> bool:
    import httpx

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return False
        try:
            if httpx.get(url, timeout=3).status_code < 500:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(1)
    return False


def stop_all(running: list[tuple[Proc, subprocess.Popen]]) -> None:
    """Stop every process group this kit started (each child was started in its own group, so the model server under
    the start script and Next.js's workers under npx stop too). Nothing else is signalled. Further Ctrl-C presses are
    ignored while stopping, so a second press cannot leave processes behind."""
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, signal.SIG_IGN)
    for _, handle in reversed(running):
        if handle.poll() is None:
            try:
                os.killpg(handle.pid, signal.SIGINT)
            except ProcessLookupError:
                pass
    for _, handle in reversed(running):
        try:
            handle.wait(timeout=15)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(handle.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            handle.wait(timeout=5)


def run(*, provider: str = "local", online: bool = False, with_llm: bool = True, api_port: int = 8010, web_port: int = 3010,
        scratch: Path | None = None, fresh: bool = True) -> int:
    import httpx

    from demo_kit import preflight, seed

    scratch = scratch or scratch_root(api_port)
    try:  # every refusal before anything is created or deleted
        benv = {**model_env(), **backend_env(os.environ, scratch, provider=provider, online=online, web_port=web_port)}
    except StackRefused as exc:
        print(f"Refused: {exc}")
        return 2
    fenv = frontend_env(os.environ, api_port)
    py = Path(sys.executable)
    with_llm = with_llm and provider == "local"
    procs = processes(py, REPO, with_llm=with_llm, api_port=api_port, web_port=web_port)
    busy = [p for p in procs if port_in_use(p.port)]
    if busy:
        print("Ports already in use: " + ", ".join(f"{p.name} {p.port}" for p in busy) +
              ". Stop what runs there, or pick other ports (API_PORT=..., WEB_PORT=...); with the model already running, NO_LLM=1.")
        return 2
    if fresh:
        for suffix in ("", "-wal", "-shm"):
            (scratch / f"sehat-demo.db{suffix}").unlink(missing_ok=True)
    (scratch / "docs").mkdir(parents=True, exist_ok=True)
    (scratch / "logs").mkdir(parents=True, exist_ok=True)

    running: list[tuple[Proc, subprocess.Popen]] = []

    def _terminate(_signum, _frame):  # a plain `kill` or a closed terminal stops the children too, like Ctrl-C
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _terminate)
    signal.signal(signal.SIGHUP, _terminate)

    try:
        for p in procs:
            log = open(scratch / "logs" / f"{p.name.replace(' ', '-')}.log", "w", encoding="utf-8")  # noqa: SIM115
            env = fenv if p.name == "frontend" else benv
            handle = subprocess.Popen(p.argv, cwd=p.cwd, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            running.append((p, handle))
            url = {"local model": f"http://{HOST}:{LLM_PORT}/v1/models", "backend": f"http://{HOST}:{api_port}/api/v1/health",
                   "frontend": f"http://{HOST}:{web_port}/login"}[p.name]
            print(f"Starting {p.name} on {HOST}:{p.port} ...")
            if not _wait(url, 240 if p.name != "backend" else 180, handle):
                print(f"{p.name} did not start; see {scratch / 'logs'}")
                return 1
        print("\nSeeding the synthetic demo cases:")
        with httpx.Client(base_url=f"http://{HOST}:{api_port}", timeout=60) as client:
            seed.seed(client)
            print("\nPre-demo check:")
            checks = preflight.run_checks(client, expect_provider=provider, web=f"http://{HOST}:{web_port}")
        preflight.print_checks(checks)
        print(f"\nOpen http://{HOST}:{web_port} (not localhost). Accounts: anm_demo, mo_demo, supervisor_demo, patient_demo."
              f"\nDatabase and logs: {scratch}. Press Ctrl-C to stop everything.")
        while all(h.poll() is None for _, h in running):
            time.sleep(1)
        print("A demo process stopped; see the logs. Stopping the rest.")
        return 1
    except seed.SeedRefused as exc:
        print(f"Seeding refused: {exc}")
        return 1
    except KeyboardInterrupt:
        print("\nStopping ...")
        return 0
    finally:
        stop_all(running)
