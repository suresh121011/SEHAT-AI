"""Encounter-summary evaluation on the synthetic corpus (docs/19 §8). Offline: fake provider (a keyword extractor, not
an LLM) and replayed OCR, in a throwaway test database. Prints the metrics table with denominators.

    # from backend/:
    ../.venv/bin/python scripts/eval_encounter_summary.py [report.md]

The numbers measure the summary stage on hand-written synthetic cases. They are NOT evidence of clinical accuracy or of
handwriting-recognition accuracy (see docs/15 for the governed real-document evaluation plan).
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

backend = Path(__file__).resolve().parents[1]
out = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(tempfile.mkdtemp()) / "encounter_eval.md"
env = {**os.environ, "ENCOUNTER_EVAL_REPORT": str(out)}
code = subprocess.call([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/ai/test_encounter_eval.py"], cwd=backend, env=env)
if out.exists():
    print(out.read_text())
sys.exit(code)
