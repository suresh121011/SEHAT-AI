"""SEHAT AI safety evaluation (docs/25; feature #31 "test evidence"). Run from `backend/`:

    python -m safety_eval            # full: in-process checks + the safety test suites and the two synthetic evaluations
    python -m safety_eval --quick    # in-process checks only (a few seconds)

It writes `safety-scorecard.md` and `safety-scorecard.json` outside the repo and exits 1 if any gate fails.

What it is: a reproducible scorecard of what the safety controls do on SYNTHETIC inputs, every number with its
denominator. What it is not: a clinical validation, an accuracy figure, or evidence about real patients. The labels
were written by the project author from the cited criteria, not by clinicians. No model is called, nothing is sent over
the network, no `.env` is read and no database is touched by the in-process part; the full mode runs pytest on throwaway
test databases exactly as CI does.
"""

SCORECARD_VERSION = "safety-scorecard-2026-11-13.1"
