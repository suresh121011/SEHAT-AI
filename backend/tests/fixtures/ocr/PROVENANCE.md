# OCR test fixtures — provenance

**Every document here is synthetic. None comes from a real patient, lab or clinic.** The names and
phone numbers are made-up canaries ("Zzyzx Canary-Testpatient", "+91 90000 00042"). Tests check that
these canaries never appear in logs or audit rows.

| File | How it was made |
|---|---|
| `cbc_normal.png`, `cbc_low_platelet.png`, `two_page_scan.pdf` | `make_fixtures.py`, drawn with Pillow's embedded font (Aileron Regular, SIL OFL). The PDF holds two grayscale page images, i.e. a scan with no text layer. |
| `rx_handwritten.png` | `make_fixtures.py`, drawn with the handwriting-style font `fonts/Caveat-Variable.ttf` (Google Fonts "Caveat", SIL OFL 1.1, see `fonts/Caveat-OFL.txt`; sha256 `0bdb6b660482d31531b3945849fba5916b3ef8695da7024a9e6b9ee3c4157988`), with seeded jitter. It is a font, not real handwriting. |
| `*.truth.json` | Written by `make_fixtures.py` while drawing: every field's role, text and pixel box, plus every drawn text item (`drawn`). This is the ground truth for value and geometry tests. |
| `engine_outputs/surya_cbc_low_platelet.json` | Recorded output of Surya OCR 2 (`datalab-to/surya-ocr-2-gguf@6a3a4c30`, via llama.cpp) on `cbc_low_platelet.png`, 2026-10-01. |
| `engine_outputs/chandra_rx_handwritten.json` | Recorded output of Chandra OCR 2 (`datalab-to/chandra-ocr-2@af93b47`, quantized locally to 8-bit MLX) on `rx_handwritten.png`, 2026-10-01. Note line 2: Chandra wrote "Amoxicillin" where the page says "Amoxycillin". |

Regenerate the images (from `backend/`): `../.venv/bin/python tests/fixtures/ocr/make_fixtures.py`

These fixtures prove pipeline behaviour on clean synthetic layouts. They are **not** evidence of OCR
accuracy on real, photographed or handwritten reports.
