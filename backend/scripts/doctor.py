"""Read-only environment check for SEHAT AI (RUNBOOK.md). Prints PASS / WARN / FAIL lines and exits 1 on any FAIL.

    make doctor                                 # from the repo root
    <venv>/bin/python backend/scripts/doctor.py

It never reads the contents of .env (only whether the file exists), never opens sehat.db, never downloads or installs
anything and never starts a server. WARN means an optional part (voice, OCR, guardrails, the local model) is not ready.
"""

from __future__ import annotations

import importlib.util
import shutil
import socket
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LOCAL_MODEL_FILE = REPO / "models" / "llm" / "Qwen3-4B-Instruct-2507-Q4_K_M.gguf"  # default pin, backend/app/ai/local_models.py
levels: list[str] = []


def report(level: str, name: str, detail: str = "") -> None:
    levels.append(level)
    print(f"{level:<4}  {name}" + (f": {detail}" if detail else ""))


def has(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def python_and_venv() -> None:
    v = sys.version_info
    if v < (3, 11):
        report("FAIL", "Python", f"{v.major}.{v.minor} found; 3.11 or later is needed (CI uses 3.11)")
    else:
        report("PASS", "Python", f"{v.major}.{v.minor}.{v.micro} ({sys.executable})")
    if sys.prefix != sys.base_prefix:
        report("PASS", "Virtual environment", sys.prefix)
    else:
        report("WARN", "Virtual environment", "not running in a venv; create one with: python3.11 -m venv .venv")


def packages() -> None:
    core = ["fastapi", "uvicorn", "aiosqlite", "jwt", "semantic_kernel", "dotenv", "presidio_analyzer", "spacy",
            "en_core_web_sm", "numpy", "httpx"]
    missing = [m for m in core if not has(m)]
    if missing:
        report("FAIL", "Backend core packages", f"missing {', '.join(missing)}: make setup-backend")
    else:
        report("PASS", "Backend core packages", f"{len(core)} found")
    dev = [m for m in ("pytest", "pytest_asyncio") if not has(m)]
    report("WARN" if dev else "PASS", "Test packages", f"missing {', '.join(dev)}: make setup-backend" if dev else "pytest found")
    optional = {
        "NeMo Guardrails (GUARDRAILS_ENABLED=1)": ["nemoguardrails"],
        "Voice: Silero VAD (requirements-voice.txt)": ["silero_vad", "torch"],
        "OCR runtime (requirements-ocr.txt, docs/14 §9)": ["onnxruntime", "rapidocr"],
    }
    for name, modules in optional.items():
        gone = [m for m in modules if not has(m)]
        report("WARN" if gone else "PASS", name, f"missing {', '.join(gone)}" if gone else "installed")


def node_and_frontend() -> None:
    node = shutil.which("node")
    if not node:
        report("FAIL", "Node.js", "not found; Node 20 or later is needed (CI uses 24)")
        return
    version = subprocess.run([node, "--version"], capture_output=True, text=True).stdout.strip()
    major = int(version.lstrip("v").split(".")[0] or 0) if version else 0
    report("PASS" if major >= 20 else "FAIL", "Node.js", version or "unknown version")
    modules = REPO / "frontend" / "node_modules"
    report("PASS" if modules.is_dir() else "WARN", "Frontend packages", "installed" if modules.is_dir() else "run: make setup-frontend")


def files_and_models() -> None:
    env = REPO / ".env"
    if env.is_file():
        report("PASS", ".env", "present (contents not read)")
    else:
        report("WARN", ".env", "missing: copy .env.example to .env and fill in your own values (never commit it)")
    if LOCAL_MODEL_FILE.is_file():
        report("PASS", "Local model file", f"{LOCAL_MODEL_FILE.name} ({LOCAL_MODEL_FILE.stat().st_size / 1e9:.2f} GB); "
                                          "hash check: make llm-check")
    else:
        report("WARN", "Local model file", "not in models/llm (the default LOCAL_LLM_MODEL_DIR); needed for "
                                          "AI_PROVIDER=local: make llm-download")
    server = shutil.which("llama-server")
    report("PASS" if server else "WARN", "llama.cpp llama-server", server or "not on PATH (needed for AI_PROVIDER=local; docs/16 §2a)")


def sqlite_fts5() -> None:
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        report("PASS", "SQLite", f"{sqlite3.sqlite_version} with FTS5")
    except sqlite3.OperationalError:
        report("WARN", "SQLite", f"{sqlite3.sqlite_version} without FTS5 (needed by the planned evidence retrieval)")
    finally:
        conn.close()


def ports() -> None:
    names = {8010: "make dev-backend", 3010: "make dev-frontend", 8000: "usual backend", 3000: "usual frontend",
             8091: "local model server"}
    busy = []
    for port, use in names.items():
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.3)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                busy.append(f"{port} ({use})")
    report("WARN" if busy else "PASS", "Ports", f"in use: {', '.join(busy)}" if busy else "8010, 3010, 8000, 3000, 8091 free")


def disk_and_git() -> None:
    free_gb = shutil.disk_usage(REPO).free / 1e9
    report("PASS" if free_gb >= 10 else "WARN", "Free disk", f"{free_gb:.0f} GB")
    branch = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True).stdout.strip()
    if branch == "main":
        report("WARN", "Git branch", "main: work on a feature branch in a worktree (AGENTS.md)")
    else:
        report("PASS", "Git branch", branch or "unknown")


def main() -> int:
    print(f"SEHAT AI doctor ({REPO})")
    for check in (python_and_venv, packages, node_and_frontend, files_and_models, sqlite_fts5, ports, disk_and_git):
        check()
    fails, warns = levels.count("FAIL"), levels.count("WARN")
    print(f"\n{len(levels)} checks: {fails} FAIL, {warns} WARN")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
