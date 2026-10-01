# 13 — Language Review Checklist (Hindi / Odia voice and consent wording)

> **This is a hand-off list for reviewers. No review has taken place.** Every Hindi and Odia string below
> is a machine-assisted draft written by the project team (`draft_unreviewed` /
> `draft_unreviewed_translation`). Passing unit tests or reading plausibly does not count as review. A
> string's status changes only when a named, qualified reviewer signs it off (column "Reviewed by"),
> recorded with the date. Research prototype, not a clinically validated device.

Created 2026-10-01 (Phase 4 gap closure). Code locations are given by file and symbol, not line number.

## A. Consent notice: material facts every language must state

Source: `backend/app/consent_notice.py`, notice version `2026-10-01.1`. Fact presence in all three languages
is tested by `tests/privacy/test_consent_notice_facts.py`. That test checks key words only, not meaning.

| # | Material fact (en wording is the reference) | Basis |
|---|---|---|
| F1 | Research prototype, not a medical device, does not diagnose; a doctor or nurse reviews every result | project policy |
| F2 | What is collected (symptoms, basic measurements, answers); no name or ID numbers in this step | docs/11 |
| F3 | The written text of what was said is kept; SEHAT AI does not store the voice recording | docs/12 §3 |
| F4 | Online speech is optional; voice (and read-back values) go **over the internet to Sarvam AI, a private company** | docs/12 §3 |
| F5 | Sarvam keeps content for a period the account holder sets (**default 30 days after last use**) | Sarvam privacy policy, last updated 2026-07-29, read 2026-10-01 |
| F6 | Sarvam **may use it to train its models unless the account has opted out** | same |
| F7 | Sarvam **may process data outside India** | same |
| F8 | This software does not check the account's retention or training settings | project fact |
| F9 | Saying no is possible; on-device speech or typing still work | docs/12 §4 |
| F10 | Withdrawal stops further processing; data already recorded is kept for now | docs/11 |

**Provider-policy conflict (2026-10-01).** Sarvam's product pages (for example sarvam.ai/apis/speech-to-text/tamil)
claim no retention, no training on API data and India-only processing. Its privacy policy says F5–F7. The
notice states the privacy policy, the less protective of the two. The deploying organisation should obtain
written terms from Sarvam and then revise F5–F7 (new notice version).

## B. Strings to review

| ID | Lang | Where | Context | English meaning | Questions for the reviewer |
|---|---|---|---|---|---|
| C-hi-1…9, C-or-1…9 | hi, or | `consent_notice.py` `paragraphs` | consent screen, read aloud by the ANM or a browser voice | en paragraphs 1–9 | Does each paragraph state F1–F10 plainly for a low-literacy listener? Is "निजी कंपनी" / "ଘରୋଇ କମ୍ପାନୀ" understood as "private company"? Is "खाताधारक" / "ଖାତାଧାରୀ" (account holder) clear, or should it say "the organisation running this system"? |
| C-hi-P, C-or-P | hi, or | `consent_notice.py` `purposes` (triage, ai_assist, voice_cloud) | consent checkboxes | en purposes | Is the voice_cloud checkbox complete without the paragraph (F4–F7)? |
| R-frame | hi, or | `readback.py` `_FRAME` | spoken and shown read-back: "I heard {label}: {value}. Is that correct?" | as en | Natural phrasing for a health worker reading to a patient? |
| R-labels | hi, or | `readback.py` `_LABELS`, `_UNIT_TEXT`, `_PREGNANT` | field names and units in read-back | temperature, oxygen level, pulse, breathing rate, BP, age, pregnancy, duration | Clinically usual terms? (e.g. नब्ज़ vs नाड़ी; ନାଡି vs ନାଡ଼ି) |
| R-unit | hi, or | `readback.py` `display_value` | the strings "(unit unclear)" and "?" | English text inside hi/or read-back | **Known gap:** an English fragment appears in Hindi/Odia read-back. Provide hi/or wording. |
| L-kw | hi, or | `extract.py` `KEYWORDS`, `UNIT_WORDS` | words that label a number (तापमान, ନାଡ଼ି, …) | field keywords | Missing common or dialect words? Words that cause false labels? |
| L-ctx | hi, or | `extract.py` `NEGATION`, `UNCERTAINTY`, `TEMPORAL`, `OXYGEN_CONTEXT`, `AGE_OLD_MARKERS` | words that block one-click confirmation | no / maybe / yesterday / on oxygen | Missing forms? Postpositional negation coverage? |
| L-num-hi | hi | `extract.py` `_HI_WORDS`, `_HI_VARIANTS` | number words 0–100, सौ | numbers | Spellings the ASR model actually writes (e.g. -नवे vs -नबे)? |
| L-num-or | or | `extract.py` `_OR_WORDS`, `_OR_CLDR`, `_OR_LEADING_HUNDRED` | number words 0–99 (Unicode CLDR spellings), ଶହ, ଶହେ | numbers | Spoken forms that differ from CLDR (e.g. final ି: ଏକୋଇଶି vs ଏକୋଇଶ)? Is "ଦୁଇ ଶହେ" ever said for 200 (currently blocked)? |
| L-homograph | hi, or | `extract.py` `_HOMOGRAPH_NUMBER_WORDS`, `_LONE_ONE_WORDS`, `_COUNT_WORDS` | number words that also mean "times" or "a" | ବାର (12 / times), ଏକ / एक (one / a), बार, ଥର | Other number words with an everyday second meaning? |
| L-mod | hi, or | `extract.py` `_NUMBER_MODIFIERS` | साढ़े, सवा, ସାଢ଼େ … (never computed, always blocked) | half, quarter | Complete? |
| UI-banner | en | consent and voice pages | "Draft translation — not reviewed by a native speaker…" | — | Keep until sign-off. |

## C. Sign-off log

| ID | Reviewer (name, qualification) | Date | Outcome |
|---|---|---|---|
| — | none yet | — | — |
