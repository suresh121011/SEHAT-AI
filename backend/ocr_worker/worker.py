"""SEHAT AI local OCR worker (docs/14 §4) — runs Surya OCR 2 and Chandra OCR 2 on this machine, and the local
MedGemma 1.5 image describer when MEDGEMMA_BACKEND=local (docs/18 §4a).

Why a separate process: Surya 0.22 and Chandra need transformers 5.x; the voice model needs transformers <5
(requirements-voice.txt). So these engines live in `.venv-ocr` and the backend talks to this worker.

Security and privacy (council R3.6):
- Listens ONLY on a Unix domain socket created with mode 0600 (no TCP port). Every request must carry the
  random token the backend generated when it spawned the worker (env SEHAT_OCR_WORKER_TOKEN).
- Hugging Face / Datalab hubs are forced offline: models load from pinned local paths only.
- Surya's llama-server is started on 127.0.0.1 with a random --api-key (Surya's client is patched to send it).
- One large engine resident at a time (Surya, Chandra or MedGemma); the others are unloaded first (16 GB machine).
- Page images arrive in the request body and are never written to disk; nothing is logged about content.

Run (the backend does this; manual use is for the spike only):
    SEHAT_OCR_WORKER_TOKEN=... SEHAT_OCR_SOCKET=/path/ocr.sock .venv-ocr/bin/python backend/ocr_worker/worker.py
"""

from __future__ import annotations

import gc
import hmac
import io
import json
import logging
import os
import secrets
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from socketserver import ThreadingMixIn, UnixStreamServer

REPO_ROOT = Path(__file__).resolve().parents[2]
MODELS = Path(os.environ.get("SEHAT_OCR_MODELS", REPO_ROOT / "models" / "ocr"))
SURYA_GGUF_DIR = MODELS / "hf-home" / "hub" / "models--datalab-to--surya-ocr-2-gguf" / "snapshots" / "6a3a4c30e5e74446d4f8b6afd05b2f2da970f470"
CHANDRA_DIR = MODELS / "chandra-ocr-2-mlx-8bit"
# Official Surya GGUF hashes (Hugging Face LFS, datalab-to/surya-ocr-2-gguf@6a3a4c30). The Chandra 8-bit build is
# produced locally from SHA-checked official weights; its hash is recorded by the install script at conversion.
SURYA_SHA256 = {
    "surya-2.gguf": "1f18abe17b1ed8b4e47ee9b1ad0e274c93daf5efbb6b29a04ff1712e37051e05",
    "surya-2-mmproj.gguf": "98c0563673b1657ff6d021d1e5f04af06cbf61bb40c63ac613e8bb71b42fb2c0",
}
CHANDRA_MANIFEST = CHANDRA_DIR / "SEHAT_ENGINE_MANIFEST.json"
# Local MedGemma 1.5 4B: converted HERE to MLX 8-bit from the official, SHA-checked weights (scripts/download_medgemma_local.py).
MEDGEMMA_DIR = Path(os.environ.get("SEHAT_MEDGEMMA_DIR", REPO_ROOT / "models" / "medgemma" / "medgemma-1.5-4b-it-mlx-8bit"))
MEDGEMMA_MANIFEST = MEDGEMMA_DIR / "SEHAT_ENGINE_MANIFEST.json"
MEDGEMMA_MAX_TOKENS = 700
_MANIFEST_ENGINES = {"chandra": (CHANDRA_DIR, CHANDRA_MANIFEST), "medgemma": (MEDGEMMA_DIR, MEDGEMMA_MANIFEST)}
_verified: set[str] = set()


def _sha256(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 24):
            h.update(chunk)
    return h.hexdigest()


def _verify_engine(name: str) -> None:
    """Fail closed: an engine whose files are missing or do not match the pinned hashes is never loaded.
    Checked once per worker process (hashing the 4.8 GB Chandra file takes a few seconds)."""
    if name in _verified:
        return
    if name == "surya":
        for fname, sha in SURYA_SHA256.items():
            f = SURYA_GGUF_DIR / fname
            if not f.is_file():
                raise FileNotFoundError(fname)
            if _sha256(f) != sha:
                raise IntegrityError(fname)
    else:
        directory, manifest_path = _MANIFEST_ENGINES[name]
        if not manifest_path.is_file():
            raise FileNotFoundError(f"{name} manifest")
        manifest = json.loads(manifest_path.read_text())
        files = manifest.get("files", {})
        for fname, sha in files.items():
            f = directory / fname
            if not f.is_file():
                raise FileNotFoundError(fname)
            if _sha256(f) != sha:
                raise IntegrityError(fname)
        if not any(f.endswith(".safetensors") for f in files) or "config.json" not in files:
            raise IntegrityError("manifest incomplete")
        # every weight file on disk must be in the manifest: an added shard would otherwise load unchecked
        if any(p.name not in files for p in directory.glob("*.safetensors")):
            raise IntegrityError("unlisted weights")
    _verified.add(name)


class IntegrityError(Exception):
    pass
MAX_BODY = 40 * 1024 * 1024
CHANDRA_MAX_TOKENS = 4096

os.environ.update({
    "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HOME": str(MODELS / "hf-home"),
    "MODEL_CACHE_DIR": str(MODELS / "surya-cache"),
    "SURYA_INFERENCE_BACKEND": "llamacpp", "SURYA_INFERENCE_PARALLEL": "1", "SURYA_INFERENCE_HOST": "127.0.0.1",
    "SURYA_GGUF_LOCAL_MODEL_PATH": str(SURYA_GGUF_DIR / "surya-2.gguf"),
    "SURYA_GGUF_LOCAL_MMPROJ_PATH": str(SURYA_GGUF_DIR / "surya-2-mmproj.gguf"),
})
_LLAMA_KEY = secrets.token_hex(16)
os.environ["LLAMA_CPP_EXTRA_ARGS"] = f"--api-key {_LLAMA_KEY}"
logging.basicConfig(level=logging.WARNING, stream=sys.stderr)


def _block_network() -> None:
    """Defence in depth, Python-level only: this process's sockets may only connect to 127.0.0.1 (its own
    llama-server). Not an OS sandbox: child processes (llama-server, bound to 127.0.0.1) are not covered."""
    real_connect = socket.socket.connect

    def guarded(self, address):
        if self.family == socket.AF_UNIX or (isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1", "localhost")):
            return real_connect(self, address)
        raise OSError("network egress blocked in SEHAT OCR worker")

    real_connect_ex = socket.socket.connect_ex

    def guarded_ex(self, address):
        if self.family == socket.AF_UNIX or (isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1", "localhost")):
            return real_connect_ex(self, address)
        raise OSError("network egress blocked in SEHAT OCR worker")

    socket.socket.connect = guarded  # type: ignore[method-assign]
    socket.socket.connect_ex = guarded_ex  # type: ignore[method-assign]


def _kill_children() -> None:
    try:
        import psutil

        for child in psutil.Process().children(recursive=True):
            child.kill()
    except Exception:  # noqa: BLE001
        pass
    _record_children()


def _record_children() -> None:
    """Surya starts llama-server with start_new_session=True, so it would survive a kill of this worker's
    process group. Record engine child PIDs next to the socket so the backend can stop them too."""
    path = os.environ.get("SEHAT_OCR_SOCKET")
    if not path:
        return
    try:
        import psutil

        pids = [c.pid for c in psutil.Process().children(recursive=True)]
        Path(path).with_name("children.pid").write_text("\n".join(map(str, pids)))
    except Exception:  # noqa: BLE001
        pass


class Engines:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.resident: str | None = None
        self._surya = None
        self._chandra = None
        self._medgemma = None

    # ── residency ──
    def _unload(self) -> None:
        if self._surya is not None:
            try:
                self._surya[0].stop()
            except Exception:  # noqa: BLE001
                pass
            self._surya = None
        _kill_children()  # Surya's stop() does not always end its llama-server (seen 2026-10-01)
        self._chandra = None
        self._medgemma = None
        self.resident = None
        gc.collect()
        try:
            import mlx.core as mx

            mx.clear_cache()
        except Exception:  # noqa: BLE001
            pass

    def _ensure(self, name: str) -> None:
        if self.resident == name:
            return
        _verify_engine(name)  # before unloading the other engine: a bad file changes nothing
        self._unload()
        if name == "surya":
            import surya.inference.backends.llamacpp as llb
            from openai import OpenAI
            from surya.inference import SuryaInferenceManager
            from surya.recognition import RecognitionPredictor

            def _client(*a, **k):
                k["api_key"] = _LLAMA_KEY
                return OpenAI(*a, **k)

            llb.OpenAI = _client  # Surya's client otherwise sends api_key="EMPTY"
            mgr = SuryaInferenceManager()
            self._surya = (mgr, RecognitionPredictor(mgr))
        elif name == "chandra":
            from mlx_vlm import load

            model, processor = load(str(CHANDRA_DIR))
            self._chandra = (model, processor)
        else:
            from mlx_vlm import load

            model, processor = load(str(MEDGEMMA_DIR))
            self._medgemma = (model, processor)
        self.resident = name

    def status(self) -> dict:
        return {
            "surya": {"installed": (SURYA_GGUF_DIR / "surya-2.gguf").is_file(), "model": "datalab-to/surya-ocr-2-gguf@6a3a4c30"},
            "chandra": {"installed": (CHANDRA_DIR / "model.safetensors").is_file(), "model": "datalab-to/chandra-ocr-2@af93b47 (local MLX 8-bit)"},
            "medgemma": {"installed": MEDGEMMA_MANIFEST.is_file(), "model": "google/medgemma-1.5-4b-it@91850547 (local MLX 8-bit)"},
            "resident": self.resident,
        }

    # ── engines ──
    def surya_page(self, png: bytes) -> dict:
        from PIL import Image

        img = Image.open(io.BytesIO(png)).convert("RGB")
        with self.lock:
            self._ensure("surya")
            result = self._surya[1]([img], full_page=True)[0]
            _record_children()
        blocks = []
        for b in result.blocks:
            d = b.model_dump()
            blocks.append({k: d.get(k) for k in ("label", "bbox", "polygon", "confidence", "html", "reading_order", "error")})
        return {"engine": "surya-ocr-2", "blocks": blocks, "image_bbox": list(result.image_bbox)}

    def _chandra_generate(self, img, prompt_key: str) -> str:
        from chandra.prompts import PROMPT_MAPPING
        from mlx_vlm import generate
        from mlx_vlm.prompt_utils import apply_chat_template

        model, processor = self._chandra
        prompt = apply_chat_template(processor, model.config, PROMPT_MAPPING[prompt_key], num_images=1)
        res = generate(model, processor, prompt, [img], max_tokens=CHANDRA_MAX_TOKENS, temperature=0.0, verbose=False)
        return res.text if hasattr(res, "text") else str(res)

    def chandra_page(self, png: bytes) -> dict:
        from chandra.output import parse_layout
        from PIL import Image

        img = Image.open(io.BytesIO(png)).convert("RGB")
        with self.lock:
            self._ensure("chandra")
            html = self._chandra_generate(img, "ocr_layout")
        blocks = [{"label": b.label, "bbox": list(b.bbox), "html": b.content} for b in parse_layout(html, img)]
        return {"engine": "chandra-ocr-2-mlx-8bit", "blocks": blocks, "truncated": not html.rstrip().endswith(">")}

    def chandra_crop(self, png: bytes) -> dict:
        from PIL import Image

        img = Image.open(io.BytesIO(png)).convert("RGB")
        with self.lock:
            self._ensure("chandra")
            html = self._chandra_generate(img, "ocr")
        return {"engine": "chandra-ocr-2-mlx-8bit", "html": html}


    def medgemma_describe(self, body: bytes) -> dict:
        """Body: JSON {image_b64, prompt, temperature}. The image is decoded in memory and never written to disk;
        the prompt is the server's fixed per-type prompt (app.ocr.medgemma.build_prompt), never case text."""
        import base64

        from mlx_vlm import generate
        from mlx_vlm.prompt_utils import apply_chat_template
        from PIL import Image

        req = json.loads(body)
        img = Image.open(io.BytesIO(base64.b64decode(req["image_b64"], validate=True))).convert("RGB")
        temperature = float(req.get("temperature", 0.0))
        if not 0.0 <= temperature <= 1.0:
            raise ValueError("temperature")
        with self.lock:
            self._ensure("medgemma")
            model, processor = self._medgemma
            prompt = apply_chat_template(processor, model.config, str(req["prompt"]), num_images=1)
            res = generate(model, processor, prompt, [img], max_tokens=MEDGEMMA_MAX_TOKENS, temperature=temperature, verbose=False)
        text = res.text if hasattr(res, "text") else str(res)
        return {"engine": "medgemma-1.5-4b-it-mlx-8bit", "text": text}


ENGINES = Engines()
TOKEN = os.environ.get("SEHAT_OCR_WORKER_TOKEN", "")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:  # never log requests (paths are fine, but keep it silent)
        return

    def _reply(self, status: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self) -> bool:
        return bool(TOKEN) and hmac.compare_digest(self.headers.get("X-SEHAT-Worker-Token", ""), TOKEN)

    def do_GET(self) -> None:  # noqa: N802
        if not self._authorized():
            return self._reply(401, {"error": "unauthorized"})
        if self.path == "/health":
            return self._reply(200, ENGINES.status())
        return self._reply(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorized():
            return self._reply(401, {"error": "unauthorized"})
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            return self._reply(413, {"error": "bad_size"})
        body = self.rfile.read(length)
        routes = {"/surya/page": ENGINES.surya_page, "/chandra/page": ENGINES.chandra_page, "/chandra/crop": ENGINES.chandra_crop,
                  "/medgemma/describe": ENGINES.medgemma_describe}
        fn = routes.get(self.path)
        if fn is None:
            return self._reply(404, {"error": "not_found"})
        try:
            return self._reply(200, fn(body))
        except FileNotFoundError:
            return self._reply(503, {"error": "model_not_installed"})
        except IntegrityError:
            return self._reply(503, {"error": "model_integrity_failed"})
        except ImportError:
            return self._reply(503, {"error": "runtime_not_installed"})
        except Exception as exc:  # noqa: BLE001 - class name only, never content
            return self._reply(500, {"error": "engine_failed", "type": type(exc).__name__})


class Server(ThreadingMixIn, UnixStreamServer):
    daemon_threads = True


def watch_parent(parent_pid: int, on_orphaned, interval_s: float = 2.0) -> threading.Thread:
    """Exit when the backend that spawned us is gone. The worker runs in its own session (so it can take
    llama-server down with it), which means a backend that is killed or crashes cannot stop it; without this
    an orphaned worker kept the socket and stayed resident for good (seen 2026-10-02)."""
    import time

    def _loop() -> None:
        while True:
            if os.getppid() != parent_pid:
                on_orphaned()
                return
            time.sleep(interval_s)

    t = threading.Thread(target=_loop, name="parent-watch", daemon=True)
    t.start()
    return t


def main() -> int:
    if len(TOKEN) < 32:
        print("SEHAT_OCR_WORKER_TOKEN missing or too short", file=sys.stderr)
        return 2
    sock = Path(os.environ["SEHAT_OCR_SOCKET"])
    sock.unlink(missing_ok=True)
    _block_network()
    old = os.umask(0o177)  # socket file created 0600
    try:
        server = Server(str(sock), Handler)
    finally:
        os.umask(old)
    os.chmod(sock, 0o600)
    import signal

    def _term(*_):
        ENGINES._unload()
        sock.unlink(missing_ok=True)
        os._exit(0)

    signal.signal(signal.SIGTERM, _term)
    parent = int(os.environ.get("SEHAT_OCR_PARENT_PID") or 0)
    if parent:
        watch_parent(parent, _term)
    print("ready", flush=True)
    try:
        server.serve_forever()
    finally:
        ENGINES._unload()
        sock.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
