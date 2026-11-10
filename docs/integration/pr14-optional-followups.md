# PR #14 optional follow-ups (2026-11-10)

Related: [PR #14](https://github.com/suresh121011/SEHAT-AI/pull/14) (draft, **not merged**) and `docs/integration/final-security-remediation-plan.md`. Research prototype, non-diagnostic. Nothing here claims clinical validation, production readiness or DPDP compliance.

## 1. PR #13 (`ci/basic-workflow` → main)

| Check | Result |
|---|---|
| Contents | One commit, `8aaefd0`, changing only `.github/workflows/ci.yml` |
| Reviews or comments | None |
| Compared with PR #14 | Same jobs (backend tests, frontend tests/types/lint, secret scan) and the same triggers. Every line found only in #13 is one #14 replaced with a stricter version: unpinned `@v4`/`@v5`/`@v2` action refs (SHA-pinned in #14), the install without Silero VAD (#14 adds it so the 149 consent and summary safety tests run), and a pytest run with no JUnit output or skip gate (#14 adds both) |
| Unique changes not in #14 or main | **None** |
| Redundant? | **Yes** |
| Action taken | **Closed without merging**, with a comment pointing to #14. The branch `ci/basic-workflow` (`8aaefd0`) and its history are kept; no branch was deleted |

## 2. Browser verification

**Not performed.** The Claude-in-Chrome extension's tools are not connected in this session. A tool search found no browser-control tools. Nothing was installed and no browser settings were changed. **No result below comes from a browser.**

What the automated suites already cover. These are unit and API tests, not browser tests; CI on #14 passed: backend 1441 passed with the skip gate ok, frontend 122/122.
- **On-device voice:** only `localService` voices are selected (`deviceVoice.test.ts`).
- **Consent before upload:** consent is checked before the audio body is read, a same-key retry after withdrawal returns 403, and no transcript text is logged (`tests/voice/test_consent_precheck.py`).
- **Denied or uncertain symptoms:** a denied symptom is written only as denied, and uncertain ones are withheld (`tests/ai/test_encounter_summary_semantics.py`).
- **Supervisor metadata only:** case review and body map (`tests/review/test_supervisor_metadata_only.py`).
- **Facility isolation:** every case route answers another facility's case exactly like a missing one (`tests/review/test_facility.py`).
- **WIP routers:** the CDS, referral and surveillance routers are not registered (remediation guard tests).

### Manual checklist (run in a browser with synthetic data only)

**Setup:**
```
ENVIRONMENT=development ACCOUNT_FACILITIES='anm_demo=PHC-KHURDA-01;mo_demo=PHC-KHURDA-01;supervisor_demo=PHC-KHURDA-01;patient_demo=PHC-KHURDA-01' \
  DATABASE_PATH=<scratch>.db AI_PROVIDER=fake VOICE_ENABLED=1 VOICE_CLOUD_STT_ENABLED=0
```
Then start the frontend with `npm run dev`. Keep DevTools open on Console and Network.

**Voice intake** (`/intake/voice`, as `anm_demo`):
1. The page loads with no console errors.
2. The microphone permission prompt appears only after you press Record, never on page load.
3. Without triage consent, the page shows "Triage consent is not in effect" and nothing can be recorded.
4. Grant triage consent only, without `voice_cloud`. The online (Sarvam) engine is not selectable or not the default, and the "On-device" label is shown.
5. Withdraw consent in a second tab, then upload from the first tab. Expect "Consent for this is not in effect. The recording was not sent.", and the Network tab shows **no** `POST …/voice/transcriptions` request.
6. Record, stop, and retry after an error. The controls recover, and an error message never claims a result.
7. Use **Listen** in Chrome on a machine with only "Google …" voices for the language. Expect "Spoken read-back is unavailable…", never playback in a cloud voice labelled "this device's voice".
8. Switch between English and Hindi. Only the selected language's prompts and voices are used.
9. Read-back candidates stay unconfirmed until a reviewer decides them.
10. After a reload, no audio blob is kept (Application tab: no stored audio), and the transcript list disappears after triage consent is withdrawn.
11. In the encounter summary, type "Cough, no fever." Expect "Denied at intake: fever" only. A symptom with conflicting negation appears under "withheld", not in the text.
12. No screen shows a diagnosis, prescription or treatment advice.

**Supervisor** (`/dashboard`, as `supervisor_demo`):
1. A case shows the **"Supervisor view: metadata only"** notice. Urgency, escalation and audit metadata are visible. The recorded input, body-map regions, free-text reasons and image findings are not.
2. There are no reviewer action buttons (sign-off, override, correct).
3. Map `supervisor_demo` to a different facility, restart, and open a known case URL. Expect "not found", identical to a random case id.
4. Probe `/api/backend/cases/<id>/body-map` directly. Expect `regions: null, metadata_only: true`.
5. The verification queue (as `mo_demo`) orders "match document" before "could change urgency", then abnormal labs. Unmatched reports and abnormal labs stay listed until a person acts. As a supervisor, the queue is refused.
6. `/api/backend/cds…`, `/referrals…` and `/surveillance…` return 404.
7. The UI and the server logs show no patient names or codes beyond the synthetic patient token.

Record each step as pass or fail, with a screenshot and the console and network output, under `docs/integration/evidence/` (synthetic data only).

## 3. Findings

| Severity | Finding | Reproduction | Expected | Actual | Impact | Recommended fix |
|---|---|---|---|---|---|---|
| Informational | Browser verification not performed | Claude-in-Chrome tools are absent in the session | A real browser pass over the voice and supervisor screens | Only unit, API and CI tests | UI regressions (wording, control state, the console) are unverified | Run the checklist above manually, or connect the extension and re-run |
| Low | 3 existing ESLint warnings in files untouched by #14 (`AppShell.tsx` unused `sectionTitle` and a raw `<img>`; `nav.ts` unused `role`) | `cd frontend && npx eslint .` | 0 warnings | 3 warnings, 0 errors | None functional | Clean up in a separate PR |

No Critical, High or Medium findings came from this follow-up. Browser-level behaviour stays **unverified** until the checklist has been run.
