"""Build the offline RxNorm index used by app/ocr/rxnorm.py (docs/14 §5). Never run automatically.

    # 1. download (no licence required) the NLM "RxNorm Current Prescribable Content" monthly zip:
    #    https://download.nlm.nih.gov/rxnorm/RxNorm_full_prescribe_<MMDDYYYY>.zip  (MD5 is on the NLM page)
    # 2. from backend/:  ../.venv/bin/python scripts/build_rxnorm_index.py ../models/rxnorm/RxNorm_full_prescribe_09082026.zip

Reads only rrf/RXNCONSO.RRF (names and codes), keeps SAB=RXNORM English names of the term types used for
matching (IN, PIN, MIN, BN, SCD, SBD), and writes models/rxnorm/rxnorm.sqlite (git-ignored).
"""

import sqlite3
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import REPO_ROOT  # noqa: E402
from app.ocr.rxnorm import norm  # noqa: E402

KEEP_TTY = {"IN", "PIN", "MIN", "BN", "SCD", "SBD"}


def main(zip_path: str) -> int:
    out = REPO_ROOT / "models" / "rxnorm" / "rxnorm.sqlite"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".building")
    tmp.unlink(missing_ok=True)
    conn = sqlite3.connect(tmp)
    conn.execute("CREATE TABLE names (rxcui TEXT, str TEXT, tty TEXT, norm TEXT)")
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    n = 0
    with zipfile.ZipFile(zip_path) as zf:
        member = next(m for m in zf.namelist() if m.endswith("RXNCONSO.RRF"))
        with zf.open(member) as fh:
            for raw in fh:
                f = raw.decode("utf-8").rstrip("\n").split("|")
                rxcui, lat, sab, tty, s, suppress = f[0], f[1], f[11], f[12], f[14], f[16]
                if lat != "ENG" or sab != "RXNORM" or tty not in KEEP_TTY or suppress not in ("N", ""):
                    continue
                conn.execute("INSERT INTO names VALUES (?, ?, ?, ?)", (rxcui, s, tty, norm(s)))
                n += 1
    conn.execute("CREATE INDEX idx_norm ON names(norm)")
    conn.execute("INSERT INTO meta VALUES ('release', ?)", (Path(zip_path).stem,))
    conn.commit()
    conn.close()
    tmp.replace(out)
    print(f"{n} names indexed → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
