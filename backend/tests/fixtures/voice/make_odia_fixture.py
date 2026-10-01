"""Generate the synthetic Odia voice fixture with Sarvam Bulbul v3 (docs/12 §9).

macOS has no Odia voice, so this clip is made by Sarvam text-to-speech. It is synthetic: no human
speaker, no patient. A clip made by Sarvam TTS and transcribed by Sarvam STT is circular, so cloud
results on it are labelled `circular_if_cloud` (tests/voice/test_live.py). The on-device engine is a
different system, so a local transcription of it is not circular, but it is still synthetic speech.

Run from backend/ (needs SARVAM_API_KEY in .env and network access to api.sarvam.ai):

    SEHAT_LIVE_SARVAM=1 ../.venv/bin/python tests/fixtures/voice/make_odia_fixture.py

Writes or_fever_102.wav (the `or_` prefix is what the live tests collect). The key is never printed.
Existing files are not overwritten unless --force is given.
"""

from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # backend/

from app.config import get_settings  # noqa: E402
from app.errors import ApiError  # noqa: E402
from app.voice import tts  # noqa: E402
from app.voice.audio import validate_wav  # noqa: E402

OUT = Path(__file__).with_name("or_fever_102.wav")
# "I have had fever for three days, temperature one hundred and two degrees."
TEXT = "ମୋର ତିନି ଦିନ ହେଲା ଜ୍ୱର, ତାପମାତ୍ରା ଏକ ଶହ ଦୁଇ ଡିଗ୍ରୀ"


def main() -> int:
    if os.getenv("SEHAT_LIVE_SARVAM") != "1":
        print("Refusing to call Sarvam: set SEHAT_LIVE_SARVAM=1 to confirm (synthetic text only).")
        return 2
    if OUT.exists() and "--force" not in sys.argv:
        print(f"{OUT.name} exists; pass --force to regenerate.")
        return 1
    settings = get_settings()
    if not settings.sarvam_configured:
        print("SARVAM_API_KEY is not set.")
        return 2
    try:
        audio = tts.synthesize(TEXT, "or", base_url=settings.sarvam_base_url, api_key=settings.sarvam_api_key, timeout_s=settings.sarvam_timeout_s)
    except ApiError as exc:  # fixed reason codes only; provider text and the key are never shown
        print(f"Sarvam TTS failed: {exc.details.get('reason')}. Nothing was written.")
        return 3
    clip = validate_wav(audio, settings.voice_max_seconds)  # 16 kHz mono PCM16, ≤30 s, or raises
    OUT.write_bytes(audio)
    print(f"wrote {OUT.name}: {len(audio)} bytes, {clip.duration_s:.2f} s")
    print(f"Add to PROVENANCE.md: | {OUT.name} | Sarvam Bulbul v3 (od-IN), {date.today().isoformat()} | \"{TEXT}\" | circular_if_cloud |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
