"""Local Hindi/Odia → English translation with IndicTrans2 (docs/16 §8, P2). Off unless TRANSLATION_ENABLED=1.

Runs on this machine only; the Hub is forced offline at runtime. The model (`indictrans2-indic-en-dist-200M`,
MIT) is gated on Hugging Face and loads with `trust_remote_code`, so it is downloaded once, explicitly, by
`scripts/download_translation_models.py` at a pinned revision, and refused at startup if the manifest does
not match. Translation is per sentence: each English segment keeps the character range of its source sentence
in the original transcript, and every field extracted from it is flagged `machine_translated_unreviewed`.
The English text then goes through the same PII redaction as any other input.

Verification status: the code path is covered with a mocked translator only. It has NOT been run against the
real model in this build (the gated download needs the project owner's Hugging Face account), and the
translation quality for clinical speech is unevaluated.
"""

import hashlib
import json
import os
import threading
from pathlib import Path

import anyio

from app.ai.inputs import split_sentences

MODEL_ID = "ai4bharat/indictrans2-indic-en-dist-200M"
MODEL_REVISION = "eb9e49d81077cfc5311e82ff36d8c1fc11557b5d"  # pinned; remote code to be reviewed at this commit
SRC_CODES = {"hi": "hin_Deva", "or": "ory_Orya"}
TGT_CODE = "eng_Latn"


class Translator:
    def __init__(self, model_dir: Path):
        self.model_dir = model_dir
        self.model_id = f"{MODEL_ID}@{MODEL_REVISION[:12]}"
        self._loaded = None
        self._lock = threading.Lock()

    def _load(self):
        with self._lock:  # concurrent first requests load the model once
            return self._load_locked()

    def _load_locked(self):
        if self._loaded is None:
            os.environ["HF_HUB_OFFLINE"] = "1"  # nothing below may reach the Hub
            import torch  # noqa: F401  (voice extras)
            from IndicTransToolkit.processor import IndicProcessor
            from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

            tok = AutoTokenizer.from_pretrained(self.model_dir, trust_remote_code=True, local_files_only=True)
            model = AutoModelForSeq2SeqLM.from_pretrained(self.model_dir, trust_remote_code=True, local_files_only=True).eval()
            self._loaded = (tok, model, IndicProcessor(inference=True))
        return self._loaded

    def _translate_sync(self, sentences: list[str], language: str) -> list[str]:
        import torch

        tok, model, ip = self._load()
        batch = ip.preprocess_batch(sentences, src_lang=SRC_CODES[language], tgt_lang=TGT_CODE)
        inputs = tok(batch, truncation=True, padding="longest", return_tensors="pt")
        with torch.inference_mode():
            # use_cache=False: the pinned remote code indexes past_key_values as legacy tuples, which breaks on the Cache
            # objects transformers 4.5x passes (AttributeError in the decoder). The remote code is hash-pinned, so it is not
            # patched; disabling the cache is slower but gives the same output.
            out = model.generate(**inputs, num_beams=5, max_length=256, use_cache=False)
        decoded = tok.batch_decode(out, skip_special_tokens=True, clean_up_tokenization_spaces=True)
        return ip.postprocess_batch(decoded, lang=TGT_CODE)

    async def translate_transcript(self, text: str, language: str) -> list[tuple[tuple[int, int], str]]:
        if language not in SRC_CODES:
            raise ValueError("unsupported language")
        spans = split_sentences(text)
        english = await anyio.to_thread.run_sync(self._translate_sync, [text[s:e] for s, e in spans], language)
        return list(zip(spans, english))


def build_translator(settings) -> Translator:
    """Refuses to start when enabled without the pinned, downloaded model (no silent fallback, no download)."""
    manifest = settings.translation_model_dir / "SEHAT_MANIFEST.json"
    try:
        data = json.loads(manifest.read_text())
    except (OSError, ValueError):
        raise RuntimeError("TRANSLATION_ENABLED=1 but the IndicTrans2 model is not downloaded: run scripts/download_translation_models.py") from None
    if data.get("repo") != MODEL_ID or data.get("revision") != MODEL_REVISION:
        raise RuntimeError("TRANSLATION_ENABLED=1 but the downloaded IndicTrans2 model is not the pinned revision")
    verify_files(settings.translation_model_dir, data.get("files"))
    return Translator(settings.translation_model_dir)


# Loaded with trust_remote_code: these must be present and unchanged since the download (reviewed at the pinned commit).
REMOTE_CODE = ("configuration_indictrans.py", "modeling_indictrans.py", "tokenization_indictrans.py")


def verify_files(model_dir: Path, files: dict | None) -> None:
    """Every file recorded at download time must still hash the same (the remote code above is mandatory), so an
    edited or swapped file is refused at startup instead of being executed."""
    if not isinstance(files, dict) or not all(name in files for name in REMOTE_CODE):
        raise RuntimeError("TRANSLATION_ENABLED=1 but the IndicTrans2 manifest has no file hashes: run scripts/download_translation_models.py again")
    for name, digest in files.items():
        path = model_dir / name
        h = hashlib.sha256()
        try:
            with path.open("rb") as f:
                while chunk := f.read(1 << 24):
                    h.update(chunk)
        except OSError:
            raise RuntimeError("TRANSLATION_ENABLED=1 but an IndicTrans2 model file is missing: run scripts/download_translation_models.py again") from None
        if h.hexdigest() != digest:
            raise RuntimeError("TRANSLATION_ENABLED=1 but an IndicTrans2 model file does not match its download-time SHA-256; refusing to load it")
