"""Render the scorecard as Markdown (for people and slides) and JSON (for tools). Every number is `value / total`."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from app.rules import ENGINE_VERSION, RULESET_VERSION
from safety_eval import SCORECARD_VERSION
from safety_eval.metrics import Section

HEADER = (
    "> SEHAT AI is a research prototype, not a clinically validated device, and it does not diagnose. "
    "Synthetic data only: every case was written by the project author from the criterion it cites and is "
    "not clinician-labelled. This scorecard checks that the safety controls do what their sources and "
    "specifications say and that they have not regressed. It is not a clinical validation and not a measure "
    "of clinical accuracy."
)

NOT_SHOWN = [
    "How the product performs for real patients: no real patient data has been used.",
    "Whether the cited rules are right for a given patient: that needs clinical review of the sources.",
    "Model quality: no model is called here. The summary evaluation uses a keyword extractor and the assistant "
    "evaluation uses scripted replies. Real local-model numbers are in MODEL_CARD.md.",
    "Speech recognition, OCR on real documents, Hindi or Odia text (docs/13, docs/15).",
    "Fairness across language, age, sex or literacy: not evaluated yet (GUARDRAILS.md §6).",
]


def _commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=Path(__file__).resolve().parent,
                              capture_output=True, text=True, timeout=5).stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def run_info(mode: str) -> dict:
    return {"mode": mode, "commit": _commit(), "engine_version": ENGINE_VERSION, "ruleset_version": RULESET_VERSION,
            "scorecard_version": SCORECARD_VERSION, "date_utc": datetime.now(timezone.utc).date().isoformat()}


def _gate(expect) -> str:
    return {"all": "all must pass", "none": "none allowed"}.get(expect, "reported")


def _status(metric) -> str:
    if metric.expect is None:
        return "-"
    return "pass" if metric.ok else "**FAIL**"


def _finding(f: dict) -> str:
    parts = [f"`{f['id']}`"] if "id" in f else []
    for key, value in f.items():
        if key == "id":
            continue
        parts.append(f"{key}: {json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value}")
    return "; ".join(parts)


def markdown(sections: list[Section], info: dict, max_findings: int = 40) -> str:
    failed = [x for s in sections for x in s.metrics if not x.ok]
    lines = ["# SEHAT AI safety scorecard", "", HEADER, "",
             f"Run: commit `{info['commit']}`, rules engine {info['engine_version']}, ruleset {info['ruleset_version']}, "
             f"{info['scorecard_version']}, {info['date_utc']} (UTC), mode `{info['mode']}`.", "",
             "**All gates passed.**" if not failed else f"**{len(failed)} gate(s) failed.**", "",
             "| Check | Result | Gate | Status |", "|:---|---:|:---|:---|"]
    for s in sections:
        lines.append(f"| **{s.title}** | | | |")
        for x in s.metrics:
            lines.append(f"| {x.label.strip() if not x.label.startswith('  ') else '— ' + x.label.strip()} | "
                         f"{x.value} / {x.total} | {_gate(x.expect)} | {_status(x)} |")
    for s in sections:
        lines += ["", f"## {s.title}", "", s.what]
        if s.findings:
            lines += ["", f"Findings ({len(s.findings)}):", ""]
            lines += [f"- {_finding(f)}" for f in s.findings[:max_findings]]
            if len(s.findings) > max_findings:
                lines.append(f"- … and {len(s.findings) - max_findings} more (see the JSON file)")
        else:
            lines += ["", "Findings: none."]
        if s.limits:
            lines += ["", "Limits:", ""] + [f"- {limit}" for limit in s.limits]
    lines += ["", "## What this scorecard does not show", ""] + [f"- {item}" for item in NOT_SHOWN] + [""]
    return "\n".join(lines)


def as_json(sections: list[Section], info: dict) -> str:
    data = {"scorecard_version": SCORECARD_VERSION, "run": info, "gates_passed": all(s.ok for s in sections),
            "sections": [s.as_dict() for s in sections]}
    return json.dumps(data, indent=2, ensure_ascii=False, default=str)
