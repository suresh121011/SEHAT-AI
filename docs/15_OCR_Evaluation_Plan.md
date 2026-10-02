# 15 — OCR Real-Document Evaluation Plan (not started)

> **Status: plan only.** No real document has been evaluated. The synthetic fixtures (docs/14 §9) and the
> five-image smoke test of 2026-10-02 (docs/14 §9.5) are regression checks, **not** an accuracy evaluation, and
> no accuracy figure may be quoted from them. This evaluation is required **before real patient data** is used
> (docs/09 Phase 5 status). Research prototype, not a clinically validated device; non-diagnostic.

Supersedes the outline in docs/14 §13.

---

## 1. Governance checklist (all before any document is collected)

| # | Item | Owner | Done |
|---|---|---|---|
| G1 | Written authorisation from each data-owning facility (lab chain, hospital) | project owner | [ ] |
| G2 | Ethics / IRB approval, or a documented determination that it is not required | project owner + ethics committee | [ ] |
| G3 | Lawful basis for secondary use: documented patient consent, or a basis confirmed by the facility's legal/privacy lead (DPDP-ready by design; no compliance claim) | facility privacy lead | [ ] |
| G4 | Named data steward responsible for access, retention and deletion | project owner | [ ] |
| G5 | De-identification **before** documents reach the evaluation machine where feasible (names, IDs, phone numbers, addresses masked on the image by the facility) | data steward | [ ] |
| G6 | Evaluation machine approved: encrypted disk, access-logged, no cloud sync, OCR runs offline (the pipeline already blocks network egress from its engines) | data steward | [ ] |
| G7 | Retention and deletion schedule for images and annotations; deletion log kept at the end | data steward | [ ] |
| G8 | Annotators and adjudicator named, trained on the protocol (§3), confidentiality agreements signed | clinical lead | [ ] |
| G9 | Acceptance criteria (§5) signed off by the clinical lead **before** the first document is processed | clinical lead | [ ] |
| G10 | Results reporting plan: who may see per-document errors (inside the governed environment only) | project owner | [ ] |

## 2. Stratified sampling

Strata are crossed where the sample allows; each cell's size is fixed in advance so its accuracy has a
confidence interval narrow enough to decide (for example ±5 percentage points at 95 %). Sizes are set by the
clinical lead with a statistician; none are fixed here.

| Dimension | Levels |
|---|---|
| Document type | printed lab report · handwritten prescription · discharge summary |
| Source | several lab chains / hospitals (layout families), not one template |
| Capture | flatbed scan · lab-system PDF (text layer) · phone photo, good light · phone photo, poor light / angle |
| Resolution | include pages near the text-size gate (median OCR line ≈ 12–16 px) to calibrate it (docs/14 §9.5) |
| Language / script on the page | English · Hindi (Devanagari) · Odia · mixed |
| Content | normal values · out-of-range values · comparators (`<`, `>`) · qualitative results · missing units/ranges · multi-page |

Exclude nothing silently: documents the pipeline rejects (quality, text too small) are counted as rejections,
with the reason, not dropped from the denominator.

## 3. Annotation protocol

1. **Two annotators work independently** and never see OCR output first. Each transcribes, per field: test
   name; value including sign, decimal point and comparator; unit; printed range; printed flag — and for
   prescriptions: drug, strength, dosage pattern / frequency, duration. Each field gets its page box.
2. **Adjudication:** a clinical adjudicator resolves every disagreement between annotators; the adjudicated
   value is the ground truth. Inter-annotator agreement is reported before adjudication.
3. Illegible fields are labelled `illegible` (ground truth), not guessed — the pipeline is correct to send them
   to human entry.
4. Annotation tool and files stay inside the governed environment (G6).

## 4. Metrics

Reported per document type and stratum, with 95 % confidence intervals.

| Metric | Definition | Why it matters |
|---|---|---|
| Exact-value accuracy | value text equals ground truth (after the recorded normalisation only) | headline extraction quality |
| Decimal-point errors | value differs only by decimal position (11.2 ↔ 112) | the most dangerous error; reported separately |
| Comparator / sign errors | `<`, `>`, `-` lost, added or changed | changes clinical meaning; reported separately |
| Unit accuracy | unit equals ground truth; missing stays missing (no invented unit) | wrong unit = wrong value |
| Missed and invented fields | ground-truth fields not extracted; extracted fields not on the page | completeness and hallucination (cf. the Chandra loop, docs/14 §9.5) |
| Range-parse accuracy | printed range parsed to the same bounds | drives the out-of-range display |
| Dispute sensitivity | share of true OCR errors flagged Dispute / Amber / human entry | whether the safety net catches errors |
| Dispute specificity | share of correct values not flagged | reviewer burden |
| Band calibration | error rate inside Accept, Amber and human-entry bands | whether "Accept" deserves its name |
| Highlight correctness | value region contains the true value and no neighbouring row | source-linked evidence (AGENTS rule 7) |
| RxNorm status agreement | matched / uncertain / unknown vs adjudicated drug identity | medication cross-check quality |
| Rejection appropriateness | rejected pages that were truly unreadable; accepted pages that were not | calibrates the quality and text-size gates |
| Reviewer outcomes (usability session) | correction and rejection rates, time per document, errors reaching the reviewed list in a simulated review | the end-to-end safety measure |

## 5. Acceptance criteria — PROPOSED, require clinical-lead sign-off (G9)

The values below are the project owner's proposed starting points (2026-10-02). They are **not** clinical
thresholds until the clinical lead approves or replaces them, and they must be fixed **before** the run — never
tuned afterwards.

| Criterion | Proposed value | Status |
|---|---|---|
| Decimal-point errors inside the Accept band | ≤ 2 % of Accept-band values | proposed |
| Dispute sensitivity (true OCR errors flagged Dispute / Amber / human entry) | ≥ 90 % | proposed |
| Decimal or comparator errors reaching the reviewed list in a simulated review | to be set by the clinical lead (zero is the expected target) | open |
| Highlight correctness for confirmed values | to be set | open |

If a criterion is not met for a stratum, that stratum stays experimental (as handwriting is today) and is not
used with real patients.

## 6. Failure analysis and reporting

- Every error categorised: layout, handwriting, photo quality, resolution, unit/range grammar, engine
  normalisation (e.g. Chandra "Amoxycillin" → "Amoxicillin"), engine hallucination, geometry.
- Examples stay inside the governed environment; only aggregate numbers leave it.
- Results are reported with their limits; synthetic results are never merged into them.
- Outcomes feed back into the gates and thresholds that were calibrated on few pages (text-size gate 13 px,
  Chandra loop detector, word-confidence 0.7, bands 0.5 / 0.85) — any change is re-validated, not tuned on the
  test set.
