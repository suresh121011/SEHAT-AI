"""`python -m safety_eval [--quick] [--out DIR]` from `backend/` (or `make safety-eval` / `make safety-eval-quick`).

Exit code 0 when every gate passes, 1 when any gate fails, 2 on a usage error.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

from safety_eval import metrics, report


def _default_out() -> Path:
    return Path(os.environ.get("TMPDIR") or tempfile.gettempdir()) / "sehat-safety-eval"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m safety_eval", description="SEHAT AI safety scorecard (docs/25)")
    parser.add_argument("--quick", action="store_true", help="in-process checks only; skip the pytest suites")
    parser.add_argument("--out", type=Path, default=None, help="output folder (default: $TMPDIR/sehat-safety-eval)")
    args = parser.parse_args(argv)
    out = (args.out or _default_out()).resolve()
    out.mkdir(parents=True, exist_ok=True)

    sections = metrics.run_in_process()
    if not args.quick:
        from safety_eval import suites

        sections += suites.run_full(out)
    info = report.run_info(mode="quick" if args.quick else "full")
    (out / "safety-scorecard.md").write_text(report.markdown(sections, info), encoding="utf-8")
    (out / "safety-scorecard.json").write_text(report.as_json(sections, info), encoding="utf-8")

    failed = [(s.title, x) for s in sections for x in s.metrics if not x.ok]
    for s in sections:
        for x in s.metrics:
            mark = "  " if x.expect is None else ("ok" if x.ok else "!!")
            print(f"{mark} {x.value:>4} / {x.total:<4} {x.label.strip()}")
    print(f"\nScorecard: {out / 'safety-scorecard.md'}")
    if failed:
        print(f"{len(failed)} gate(s) FAILED: " + "; ".join(f"{title}: {x.label.strip()}" for title, x in failed))
        return 1
    print("All gates passed. Synthetic data only; not a clinical validation.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
