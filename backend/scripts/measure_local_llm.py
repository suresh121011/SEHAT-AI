"""Measurement gate for AI_PROVIDER=local (docs/16 §2a). Runs 20 SYNTHETIC English intake cases (6 with no red
flag) through the real path: redaction → LocalLlamaProvider (3 MAKER passes at 0.1/0.2/0.3) → strict schema →
grounding → vote. Needs a running server (scripts/start_local_llm.py). Prints per-case rows and a summary;
writes JSON to --out if given. Synthetic text only; nothing is stored in the database.

    ../.venv/bin/python scripts/measure_local_llm.py [--out results.json]
    ../.venv/bin/python scripts/measure_local_llm.py --keywords-only   # no server: keyword suggester on smoke + held-out

With a server, red-flag recall is reported three ways: model only, keyword rules only (app/ai/redflag_keywords.py,
not a model) and the hybrid union the reviewer sees. Grounding and latency are model-only by definition: the keyword
rules add no model call and their quotes are verbatim by construction.

Gate (council, 2026-10-05): grounding pass rate ≥ 70 % and median 3-pass wall time ≤ 30 s. Also reported:
schema-valid passes, field unanimity, expected-value recall, expected red-flag recall and false raises.
"""

import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import ValidationError  # noqa: E402

from app.ai import maker  # noqa: E402
from app.ai.grounding import ground  # noqa: E402
from app.ai.inputs import split_sentences  # noqa: E402
from app.ai.local_provider import LocalLlamaProvider  # noqa: E402
from app.ai.redflag_keywords import suggest  # noqa: E402
from app.ai.schemas import ExtractionOutput  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.privacy.pii import redact_segments  # noqa: E402

# (id, text, expected numbers by measurement name, expected red flags). Synthetic; written for this test.
CASES = [
    ("c01", "Fever for 3 days. Temperature 39.4 C. Pulse 112. Severe body pain since yesterday.", {"temp": [39.4], "pulse": [112]}, {"severe_pain"}),
    ("c02", "Chest pain started 2 hours ago, crushing, going to left arm. BP 150/94. Pulse 104.", {"bp": [150, 94], "pulse": [104]}, {"chest_pain_acute_24h"}),
    ("c03", "Mild cough and runny nose for 2 days. No fever. SpO2 98%. Eating well.", {"spo2": [98]}, set()),
    ("c04", "Came for routine BP check. BP 128/82. No complaints today.", {"bp": [128, 82]}, set()),
    ("c05", "Sudden weakness of right arm and leg since this morning, about 3 hours. Speech slurred. BP 182/104.", {"bp": [182, 104]}, {"limb_weakness_24h"}),
    ("c06", "Breathless since 6 hours, cannot complete sentences. SpO2 86%. Respiratory rate 32.", {"spo2": [86], "resp_rate": [32]}, {"incomplete_sentences"}),
    ("c07", "Patient denies chest pain. Has mild headache for 1 day. Temperature 37.2 C.", {"temp": [37.2]}, set()),
    ("c08", "Fainted at home today, lost consciousness for a minute. Pulse 46.", {"pulse": [46]}, {"syncope"}),
    ("c09", "Snake bite on left foot 1 hour ago while working in the field. Swelling present.", {}, {"poisoning_envenomation"}),
    ("c10", "Pregnant, 34 weeks. Abdominal pain and some vaginal bleeding since morning. Hb 8.2.", {"hb": [8.2]}, {"third_trimester_pain_or_bleeding"}),
    ("c11", "Fever 4 days with headache. Platelets 85000 on report. Temperature 38.6 C. No bleeding.", {"platelets": [85000], "temp": [38.6]}, set()),
    ("c12", "Known diabetic. Glucose 312 this morning. Taking metformin 500 mg twice daily. Feels tired.", {"glucose": [312]}, set()),
    ("c13", "Sudden severe abdominal pain started 1 hour ago. Vomiting twice. Pulse 118.", {"pulse": [118]}, {"sudden_abdominal_pain"}),
    ("c14", "Worst headache of her life, started suddenly 30 minutes ago. BP 168/100.", {"bp": [168, 100]}, {"sudden_headache"}),
    ("c15", "Needle prick injury from used syringe during dressing, 20 minutes ago.", {}, {"needle_prick_injury"}),
    ("c16", "Ankle sprain while playing, walking with limp. Pain score 4 out of 10. No swelling of face.", {"pain_severity": [4]}, set()),
    ("c17", "Lips and face swelling after eating prawns, itching all over. Audible wheeze.", {}, {"angioedema_face", "allergic_reaction", "audible_wheeze"}),
    ("c18", "Unable to pass urine since last night, lower belly very full and painful.", {}, {"urinary_retention"}),
    ("c19", "Fall from a moving bus, about 40 minutes ago. Pain in neck. Pulse 96. BP 110/70.", {"pulse": [96], "bp": [110, 70]}, {"dangerous_mechanism_trauma"}),
    ("c20", "Follow-up visit. Wound healing well. No fever, no pain, no discharge. Temperature 36.8 C.", {"temp": [36.8]}, set()),
]


def _prompt(text: str):
    return redact_segments([(f"S{i + 1}", text[s:e]) for i, (s, e) in enumerate(split_sentences(text))])


def keyword_flags(text: str) -> set[str]:
    return {h.flag for h in suggest(_prompt(text).segment_texts())}


def keyword_report(name: str, cases) -> dict:
    """Keyword rules alone over (id, text, ..., expected flags) cases. No model, no server."""
    hits = total = extra = neg_hit = neg = 0
    misses = []
    for case in cases:
        want, got = case[-1], keyword_flags(case[1])
        hits, total, extra = hits + len(want & got), total + len(want), extra + len(got - want)
        if not want:
            neg, neg_hit = neg + 1, neg_hit + bool(got)
        if got != want:
            misses.append({"id": case[0], "missed": sorted(want - got), "extra": sorted(got - want)})
    return {"set": name, "recall": f"{hits}/{total}", "extra_suggestions": extra, "negative_cases_with_suggestion": f"{neg_hit}/{neg}", "differences": misses}


async def run_case(provider, text: str, passes: int) -> dict:
    prompt = _prompt(text)
    segments = prompt.segment_texts()
    timings, grounded, abst = [], [], []
    kept = dropped = valid = 0

    async def one(i: int):
        t = time.perf_counter()
        try:
            reply = await provider.generate(prompt, temperature=maker.TEMPERATURES[i], pass_index=i)
        except Exception as exc:  # reason only
            abst.append({"pass": i, "reason": f"provider_error:{type(exc).__name__}:{exc}"[:120]})
            return None, time.perf_counter() - t
        took = time.perf_counter() - t
        try:
            out = ExtractionOutput.model_validate_json(reply.raw_json)
        except ValidationError:
            abst.append({"pass": i, "reason": "schema_invalid"})
            return None, took
        return ground(segments, out), took

    t0 = time.perf_counter()
    results = await asyncio.gather(*(one(i) for i in range(passes)))
    wall = time.perf_counter() - t0
    for g, took in results:
        timings.append(took)
        grounded.append(g)
        if g is not None:
            valid += 1
            kept += len(g.entries)
            dropped += len(g.dropped)
    voted = maker.vote(grounded, passes, abst)
    return {"wall_s": wall, "pass_s": timings, "valid": valid, "kept": kept, "dropped": dropped, "abstentions": abst,
            "drop_reasons": sorted({d["reason"] for g in grounded if g for d in g.dropped}), "vote": voted}


def score(case, res) -> dict:
    _, _, want_nums, want_flags = case
    v = res["vote"]
    fields = {f.key: f for f in v.fields}
    hits = total = 0
    for name, nums in want_nums.items():
        total += 1
        f = fields.get(name)
        if f and f.status in ("agreed", "majority") and f.value:
            got = [f.value["value"]] + ([f.value["value2"]] if f.value.get("value2") is not None else [])
            hits += all(any(abs(g - n) < 1e-6 for g in got) for n in nums)
    surfaced = {k.split(":", 1)[1] for k, f in fields.items() if f.kind == "red_flag" and f.status in ("agreed", "disputed_raise") and f.priority_review}
    keyword = keyword_flags(case[1])
    hybrid = surfaced | keyword
    return {
        "flags_keyword": sorted(keyword), "keyword_hits": len(want_flags & keyword), "hybrid_hits": len(want_flags & hybrid),
        "hybrid_false_raises": sorted(hybrid - want_flags),
        "value_recall": (hits, total),
        "flags_expected": sorted(want_flags), "flags_surfaced": sorted(surfaced),
        "flag_hits": len(want_flags & surfaced), "false_raises": sorted(surfaced - want_flags),
        "agreed": sum(f.status == "agreed" for f in v.fields), "fields": len(v.fields), "status": v.status,
        "urgency": v.urgency_suggestion, "urgency_candidates": v.urgency_candidates,
    }


async def main(argv: list[str]) -> int:
    from redflag_heldout_cases import HELDOUT

    keyword = [keyword_report("smoke", CASES), keyword_report("heldout", HELDOUT)]
    if "--keywords-only" in argv:
        print(json.dumps(keyword, indent=1))
        return 0
    s = get_settings()
    provider = LocalLlamaProvider(s)
    reason = await provider.readiness()
    if reason:
        print(f"local model not ready: {reason}", file=sys.stderr)
        return 1
    passes = s.ai_maker_passes
    print(f"model {provider.model_id}, {passes} passes, {len(CASES)} synthetic cases")
    rows = []
    for case in CASES:
        res = await run_case(provider, case[1], passes)
        sc = score(case, res)
        rows.append({"id": case[0], **{k: v for k, v in res.items() if k != "vote"}, **sc})
        r = rows[-1]
        print(f"{r['id']} wall {r['wall_s']:5.1f}s valid {r['valid']}/{passes} kept {r['kept']:2} dropped {r['dropped']:2} "
              f"agreed {r['agreed']}/{r['fields']} values {r['value_recall'][0]}/{r['value_recall'][1]} "
              f"flags {r['flag_hits']}/{len(r['flags_expected'])} false {r['false_raises']} urg {r['urgency']} {r['drop_reasons'] or ''}")
    kept, dropped = sum(r["kept"] for r in rows), sum(r["dropped"] for r in rows)
    vh, vt = sum(r["value_recall"][0] for r in rows), sum(r["value_recall"][1] for r in rows)
    fh, ft = sum(r["flag_hits"] for r in rows), sum(len(r["flags_expected"]) for r in rows)
    negatives = [r for r in rows if not r["flags_expected"]]
    summary = {
        "model": provider.model_id, "cases": len(rows),
        "median_wall_s": statistics.median(r["wall_s"] for r in rows), "max_wall_s": max(r["wall_s"] for r in rows),
        "schema_valid_passes": f"{sum(r['valid'] for r in rows)}/{passes * len(rows)}",
        "grounding_pass_rate": kept / (kept + dropped) if kept + dropped else 0.0,
        "field_unanimity": sum(r["agreed"] for r in rows) / max(1, sum(r["fields"] for r in rows)),
        "value_recall": f"{vh}/{vt}", "red_flag_recall": f"{fh}/{ft}",
        "red_flag_recall_keyword_only": f"{sum(r['keyword_hits'] for r in rows)}/{ft}",
        "red_flag_recall_hybrid": f"{sum(r['hybrid_hits'] for r in rows)}/{ft}",
        "hybrid_false_raises_total": sum(len(r["hybrid_false_raises"]) for r in rows),
        "hybrid_negative_cases_with_false_raise": f"{sum(bool(r['hybrid_false_raises']) for r in negatives)}/{len(negatives)}",
        "false_raises_total": sum(len(r["false_raises"]) for r in rows),
        "negative_cases_with_false_raise": f"{sum(bool(r['false_raises']) for r in negatives)}/{len(negatives)}",
        "insufficient_agreement": sum(r["status"] != "completed" for r in rows),
    }
    summary["gate_pass"] = summary["grounding_pass_rate"] >= 0.70 and summary["median_wall_s"] <= 30
    print(json.dumps(summary, indent=1))
    print(json.dumps(keyword, indent=1))
    if "--out" in argv:
        Path(argv[argv.index("--out") + 1]).write_text(json.dumps({"summary": summary, "keyword_only": keyword, "rows": rows}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
