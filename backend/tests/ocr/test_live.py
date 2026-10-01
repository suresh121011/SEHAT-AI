"""REAL engines, opt-in (docs/14 §9): SEHAT_LIVE_OCR=1 ../.venv/bin/python -m pytest -m live tests/ocr -s

Runs PaddleOCR in-process and Surya OCR 2 / Chandra OCR 2 through the local worker, with every non-local
network connection blocked. Skips (never passes) when models or the OCR environment are missing.
Prints a scorecard against the synthetic fixtures' ground truth. These numbers describe synthetic
documents only — they are NOT a measure of accuracy on real reports.
"""

import json
import os
import socket
import time
from pathlib import Path

import pytest

from app.config import REPO_ROOT
from app.ocr import engine as paddle
from app.ocr import files
from app.ocr.extract import extract_lab

pytestmark = pytest.mark.live
if not os.environ.get("SEHAT_LIVE_OCR"):
    pytest.skip("set SEHAT_LIVE_OCR=1 to run real OCR engines", allow_module_level=True)

FIX = Path(__file__).parents[1] / "fixtures" / "ocr"
MODELS = REPO_ROOT / "models" / "ocr"


@pytest.fixture(autouse=True)
def no_internet(monkeypatch):
    real = socket.socket.connect

    def guarded(self, address):
        if self.family == socket.AF_UNIX or (isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1", "localhost")):
            return real(self, address)
        raise OSError("network blocked in live OCR test")

    monkeypatch.setattr(socket.socket, "connect", guarded)


def _contains(outer, inner) -> bool:
    return outer[0] <= inner[0] and outer[1] <= inner[1] and outer[2] >= inner[2] and outer[3] >= inner[3]


def _overlaps(a, b) -> bool:
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


@pytest.mark.skipif(not paddle.model_installed(MODELS), reason="PaddleOCR models not installed")
def test_paddleocr_scorecard_on_synthetic_lab_reports():
    total = exact = located = 0
    lat = []
    for fx in ("cbc_normal.png", "cbc_low_platelet.png", "two_page_scan.pdf"):
        truth = json.loads((FIX / (fx.rsplit(".", 1)[0] + ".truth.json")).read_text())
        _, pages = files.normalize((FIX / fx).read_bytes())
        for p in pages:
            t = time.perf_counter()
            ocr = paddle.read_page(p.png, p.index, model_dir=MODELS)
            lat.append(time.perf_counter() - t)
            cands = {(c.name_raw, c.row_index): c for c in extract_lab(ocr)}
            by_name = {c.name_raw: c for c in cands.values()}
            fields = truth["pages"][p.index]["fields"]
            rows = {}
            for f in fields:
                if f["row"] is not None:
                    rows.setdefault(f["row"], {})[f["role"]] = f
            values = [f for f in fields if f["role"] == "value"]
            for row in rows.values():
                total += 1
                c = by_name.get(row["name"]["text"])
                if c and c.value.raw == row["value"]["text"]:
                    exact += 1
                    reg = next((r for r in c.regions if r.role == "value"), None)
                    others = [v["bbox"] for v in values if v is not row["value"]]
                    if reg and _contains(reg.bbox, row["value"]["bbox"]) and not any(_overlaps(reg.bbox, o) for o in others):
                        located += 1
    print(f"\nPaddleOCR synthetic scorecard: values exact {exact}/{total}; value region contains truth & no other row {located}/{total}; "
          f"median page {sorted(lat)[len(lat) // 2]:.2f}s (synthetic fixtures only — not real-report accuracy)")
    assert exact >= total - total // 20 and located >= total - total // 20


def _worker():
    from app.ocr.worker_client import OcrWorker

    py = REPO_ROOT / ".venv-ocr" / "bin" / "python"
    if not py.is_file():
        pytest.skip(".venv-ocr not installed")
    return OcrWorker(py, REPO_ROOT / "backend" / "ocr_worker" / "worker.py", MODELS / "run-test", MODELS)


def test_surya_reads_the_lab_table_offline():
    from app.ocr.surya_parse import table_rows

    w = _worker()
    try:
        _, pages = files.normalize((FIX / "cbc_low_platelet.png").read_bytes())
        t = time.perf_counter()
        rows = table_rows(w.call("/surya/page", pages[0].png, 240)["blocks"])
        print(f"\nSurya OCR 2: {len(rows)} rows in {time.perf_counter() - t:.1f}s (incl. load)")
        values = {r.analyte_key: r.cells.get("value") for r in rows}
        assert values["platelets"] == "85,000" and values["hemoglobin"] == "11.2"
    finally:
        w.stop()


def test_chandra_reads_the_handwriting_style_prescription_offline():
    from app.ocr.rx import RxLine, extract_rx
    from app.ocr.service import _html_lines

    w = _worker()
    try:
        _, pages = files.normalize((FIX / "rx_handwritten.png").read_bytes())
        t = time.perf_counter()
        out = w.call("/chandra/page", pages[0].png, 300)
        lines = [RxLine(tx, tuple(b["bbox"]), 0, f"b{i}") for i, b in enumerate(out["blocks"]) for tx in _html_lines(b["html"])]
        meds = extract_rx(lines)
        print(f"\nChandra OCR 2 (8-bit MLX): {len(meds)} medication lines in {time.perf_counter() - t:.1f}s (incl. load): "
              + "; ".join(f"{m.drug_raw} {m.strength_raw} {m.dosage_pattern}" for m in meds))
        assert [m.dosage_pattern for m in meds][:3] == ["1-0-1", "1-1-1", "1-0-0"]
        assert {m.drug_raw.lower() for m in meds} >= {"paracetamol", "pantoprazole"}
    finally:
        w.stop()
