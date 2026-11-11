"""Real-model check of per-symptom extraction (docs/plans/clinical-note-v2-plan.md step 1). Opt-in; never run in CI.

Runs the configured, non-fake provider (AI_PROVIDER=local needs scripts/start_local_llm.py running) on a few
hand-written synthetic sentences in English, romanised Hindi and Devanagari, then applies the same schema validation
and grounding the server applies. Gold labels were written before the run. One pass per sentence (the app runs
several and votes), temperature 0.

    # from backend/, with the model server up:
    ../.venv/bin/python scripts/eval_symptom_extraction_live.py

It reports what survived grounding, with denominators. A dozen sentences are a smoke check, NOT an accuracy claim.
"""

import asyncio
import json
import sys
import time

sys.path.insert(0, ".")

from app.ai.grounding import ground  # noqa: E402
from app.ai.providers import build_provider  # noqa: E402
from app.ai.schemas import ExtractionOutput  # noqa: E402
from app.config import get_settings  # noqa: E402

# (language, sentence, {term: duration as "amount unit" or None}, [denied terms])
GOLD = [
    ("en", "I am suffering from fever since 2 days and my head is paining since 2 hours. No vomiting.", {"fever": "2 day", "headache": "2 hour"}, ["vomiting"]),
    ("en", "Cough for two weeks and loose motions since yesterday.", {"cough": "2 week", "diarrhoea": None}, []),
    ("en", "My stomach is hurting for 3 days. I do not have fever.", {"abdominal pain": "3 day"}, ["fever"]),
    ("en", "Fever and cough for 3 days.", {"fever": "3 day", "cough": "3 day"}, []),
    ("hi-Latn", "Mujhe do din se bukhar hai aur do ghante se sir dard ho raha hai.", {"fever": None, "headache": None}, []),
    ("hi-Latn", "Teen din se khansi hai, bukhar nahi hai.", {"cough": None}, ["fever"]),
    ("hi", "मुझे दो दिन से बुखार है और दो घंटे से सिर में दर्द हो रहा है।", {"fever": "2 day", "headache": "2 hour"}, []),
    ("hi", "तीन दिन से खांसी है और एक हफ्ते से कमजोरी है।", {"cough": "3 day", "weakness": "1 week"}, []),
    ("hi", "पेट में दर्द पांच दिन से है। उल्टी नहीं है।", {"abdominal pain": "5 day"}, ["vomiting"]),
]


async def main() -> int:
    provider = build_provider(get_settings())
    if provider is None or provider.name == "fake":
        print("This check needs a real provider (AI_PROVIDER=local or azure). Nothing was run.")
        return 2
    if (reason := await provider.readiness()) is not None if hasattr(provider, "readiness") else False:
        print(f"Provider not ready: {reason}. Start scripts/start_local_llm.py. Nothing was run.")
        return 2
    print("provider:", provider.name, provider.model_id, "| one pass per sentence, temperature 0, grounded as the server grounds")
    tally = {"term_found": [0, 0], "duration_exact": [0, 0], "duration_words_kept_when_number_unverified": [0, 0], "denial_found": [0, 0],
             "no_extra_present_term": [0, 0], "valid_reply": [0, 0]}
    times = []
    for lang, text, want, denied in GOLD:
        t0 = time.perf_counter()
        tally["valid_reply"][1] += 1
        try:
            raw = await provider._generate((("S1", text),), temperature=0.0, pass_index=0)
            out = ExtractionOutput.model_validate_json(raw)
        except Exception as exc:  # an invalid reply is an abstaining pass in the app; counted, never read as text
            print(f"[{lang}] INVALID REPLY ({type(exc).__name__}) for: {text}")
            continue
        times.append(time.perf_counter() - t0)
        tally["valid_reply"][0] += 1
        g = ground({"S1": text}, out)
        got = {e.key.split(":", 1)[1]: e.value for e in g.entries if e.kind == "symptom"}
        present = {k: v for k, v in got.items() if not v["negated"]}
        row = {}
        for term, dur in want.items():
            v = present.get(term)
            tally["term_found"][1] += 1
            tally["term_found"][0] += v is not None
            d = (v or {}).get("duration") or {}
            have = f"{d['amount']:g} {d['unit']}" if d.get("amount") is not None else None
            row[term] = have or (f"words only: {d['text']}" if d.get("text") else None)
            if dur is not None:
                tally["duration_exact"][1] += 1
                tally["duration_exact"][0] += have == dur
            else:  # no number can be verified from this script (romanised) or none was said: the words, or nothing, never a guessed number
                tally["duration_words_kept_when_number_unverified"][1] += 1
                tally["duration_words_kept_when_number_unverified"][0] += have is None
        for term in denied:
            tally["denial_found"][1] += 1
            tally["denial_found"][0] += bool(got.get(term, {}).get("negated"))
        extra = sorted(set(present) - set(want))
        tally["no_extra_present_term"][1] += 1
        tally["no_extra_present_term"][0] += not extra
        print(f"[{lang}] {text}\n    kept: {json.dumps(row, ensure_ascii=False)} | denied: {sorted(k for k, v in got.items() if v['negated'])}"
              f"{' | EXTRA: ' + str(extra) if extra else ''}{' | dropped: ' + json.dumps(g.dropped, ensure_ascii=False) if g.dropped else ''}")
    print("\n| Metric (real model, after grounding) | Passed / total |\n|:---|---:|")
    for k, (ok, n) in tally.items():
        print(f"| {k} | {ok} / {n} |")
    if times:
        print(f"\nMean time per sentence: {sum(times) / len(times):.1f} s. {len(GOLD)} synthetic sentences: a smoke check, not an accuracy claim.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
