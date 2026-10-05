"""Start the loopback-only llama-server for AI_PROVIDER=local (docs/16 §2a). Runs in the foreground; stop with Ctrl-C.

    # from backend/ (after scripts/download_local_llm.py <model>):
    ../.venv/bin/python scripts/start_local_llm.py                 # uses LOCAL_LLM_MODEL / LOCAL_LLM_URL from .env
    ../.venv/bin/python scripts/start_local_llm.py --check-only    # verify the file hash and llama.cpp build, then exit

Reads only LOCAL_LLM_MODEL, LOCAL_LLM_MODEL_DIR, LOCAL_LLM_URL and AI_MAKER_PASSES from the environment / repo .env, so an
unrelated .env problem cannot stop the model server. Before starting it re-checks the model file's SHA-256 against the pin,
then writes a fresh random API key to <LOCAL_LLM_MODEL_DIR>/run/api_key (0600) that the backend reads. After the server is
up it sends one synthetic warm-up request, so the first real case does not pay the cold-start cost.

Server flags, and why:
  --host 127.0.0.1           loopback only (LOCAL_LLM_URL is also restricted to loopback in app.config)
  --api-key-file             other local processes cannot use the model without the key
  --no-webui --no-slots      no browser UI; /slots would expose prompt text
  --offline                  no network access from llama.cpp
  --reasoning off            these are used as non-thinking extractors
  -cram 0                    no host-memory prompt cache (prompts are not kept after a request)
  no -v / --verbose          verbose logging would print prompts
  -np = AI_MAKER_PASSES      the MAKER passes run in parallel slots
Tested with Homebrew llama.cpp 0.5.0 (build 11146); other builds may not accept every flag.
"""

import os
import secrets
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

from app.ai.local_models import MODELS, installed, sha256_file  # noqa: E402
from app.ai.local_provider import API_KEY_FILE  # noqa: E402
from app.config import REPO_ROOT, _env, _local_llm_url, _path  # noqa: E402

CTX_PER_SLOT = 6144
TESTED_BUILD = "11146"


def main(argv: list[str]) -> int:
    load_dotenv(REPO_ROOT / ".env")
    key = _env("LOCAL_LLM_MODEL") or "qwen3-4b-instruct-2507-q4km"
    if key not in MODELS:
        print(f"LOCAL_LLM_MODEL must be one of: {', '.join(MODELS)}", file=sys.stderr)
        return 1
    model = MODELS[key]
    model_dir = _path(_env("LOCAL_LLM_MODEL_DIR") or "./models/llm")
    url = urlsplit(_local_llm_url(_env("LOCAL_LLM_URL") or "http://127.0.0.1:8091"))
    slots = int(_env("AI_MAKER_PASSES") or "3")
    path = model_dir / model.filename
    if model.gate != "passed":
        print(f"warning: {model.key} did not pass the measurement gate (gate={model.gate}, docs/16 §2a.4)", file=sys.stderr)
    if not installed(model_dir, model.key):
        print(f"{model.key} is not installed: run scripts/download_local_llm.py {model.key}", file=sys.stderr)
        return 1
    if sha256_file(path) != model.sha256:
        print(f"{path.name}: SHA-256 does not match the pin; refusing to start", file=sys.stderr)
        return 1
    print(f"{model.key}: SHA-256 verified")
    binary = shutil.which("llama-server")
    if binary is None:
        print("llama-server not found (brew install llama.cpp)", file=sys.stderr)
        return 1
    version = subprocess.run([binary, "--version"], capture_output=True, text=True).stderr + ""
    build = next((w.strip(",") for w in version.split() if w.strip(",").isdigit()), "unknown")
    print(f"llama.cpp build {build}" + ("" if build == TESTED_BUILD else f" (tested with {TESTED_BUILD}; flags may differ)"))
    if "--check-only" in argv:
        return 0

    key_path = model_dir / API_KEY_FILE
    key_path.parent.mkdir(mode=0o700, exist_ok=True)
    key_path.unlink(missing_ok=True)
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    api_key = secrets.token_urlsafe(32)
    with os.fdopen(fd, "w") as f:
        f.write(api_key)
    base = f"http://{url.hostname if url.hostname != '::1' else '[::1]'}:{url.port}"
    args = [binary, "-m", str(path), "--alias", model.key, "--host", url.hostname or "127.0.0.1", "--port", str(url.port),
            "--api-key-file", str(key_path), "--no-webui", "--no-slots", "--offline", "--reasoning", "off",
            "-cram", "0", "-ngl", "99", "-np", str(slots), "-c", str(CTX_PER_SLOT * slots)]
    print(f"Starting llama-server on {base} with {slots} slots")
    proc = subprocess.Popen(args)
    signal.signal(signal.SIGTERM, lambda *_: proc.terminate())
    try:
        warm_up(base, api_key, model.key, proc)
        return proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        return proc.wait()


def warm_up(base: str, api_key: str, alias: str, proc: subprocess.Popen) -> None:
    """Wait for /health, then one short synthetic completion (no case data)."""
    import httpx

    with httpx.Client(timeout=120, trust_env=False) as c:
        for _ in range(240):
            if proc.poll() is not None:
                print("llama-server exited during startup", file=sys.stderr)
                return
            try:
                if c.get(f"{base}/health").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        t = time.perf_counter()
        r = c.post(f"{base}/v1/chat/completions", headers={"Authorization": f"Bearer {api_key}"},
                   json={"model": alias, "max_tokens": 8, "messages": [{"role": "user", "content": "Reply OK."}]})
        print(f"warm-up {'ok' if r.status_code == 200 else f'failed ({r.status_code})'} in {time.perf_counter() - t:.1f}s — ready", flush=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
