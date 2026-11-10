# Post-remediation security hardening

> Branch `feat/post-remediation-security-hardening`, from `main` `aaebba2` (PR #14 merged). Started 2026-11-10.
> **Status: Milestones M-A to M-E implemented on this branch (uncommitted, awaiting review), 2026-11-10.**
> This is a research prototype and a non-diagnostic triage aid. Nothing here claims production readiness, clinical validation or DPDP compliance.

## 0. Baseline (inspected before any change)

| Area | Today | File |
|---|---|---|
| Login | `POST /auth/login` with `{username, role}` and **no password**. Four demo accounts, each with one role. | `app/routes/auth.py`, `app/auth.py` |
| Token | An HS256 JWT holding `sub`, `username`, `role`, `exp` (24 h default). **No `jti`, no session record, no revocation.** The frontend keeps it in an httpOnly `sehat_session` cookie and the same-origin proxy adds it as a Bearer header. "Logout" only deletes the cookie. | `app/auth.py`, `frontend/src/app/api/session/route.ts` |
| Patient identity | **Every patient shares `patient_demo`.** `cases.created_by` is a single shared `user_id`, so any patient session reads every patient-created case. | `app/consent.py` `load_case` |
| Patient registry | `patients(patient_id, patient_code, facility_code)`. A visit links to a patient through append-only `patient_link_events`, made only by an ANM who confirms a code the patient holds. There is no login identity for a patient. | `app/patient_registry.py` |
| Case access | `load_case(principal, case, mode)`: creator or handler, MO, or supervisor (metadata only), with facility scope from server config `ACCOUNT_FACILITIES` (fails closed since PR #14). **MOs can reach any case in their facility; there is no assignment.** | `app/consent.py`, `app/auth.py` |
| Lookup rate limit | None (audited since PR #14). | `app/patient_registry.py` `lookup` |
| Infrastructure | One FastAPI process with SQLite (WAL). **No Redis, no identity provider, no deployment manifests.** | — |
| Startup guard | The backend refuses to start outside `ENVIRONMENT=development` or `test`. | `app/config.py` |

## 1. Threat model

**Assets:**
- patient clinical records (cases, transcripts, documents, summaries);
- patient identity and the visit-to-patient links;
- the patient code (a locator);
- session credentials;
- the audit log.

**Actors:**
- a patient;
- staff: ANM, MO, supervisor, admin;
- another patient;
- an outsider with network access;
- an insider with database or file access (out of scope for this phase).

| # | Threat | Today | Planned mitigation (milestone) |
|---|---|---|---|
| T1 | A patient reads another patient's record through the shared account | **Possible**; documented residual F1 | Per-patient identity from an OIDC provider; patient access only through the patient's own verified identity binding (M2, M3) |
| T2 | Token forgery or replay: wrong issuer, audience or algorithm, an expired token, `alg=none` | The HS256 secret is validated and the algorithm pinned (PR #14) | For OIDC: signature checked against JWKS, plus `iss`, `aud`, `exp`, `iat`, `nbf` and an algorithm allowlist (M2) |
| T3 | A stolen session stays usable after logout or compromise | **Possible for 24 h** | Server-side sessions checked on **every** request; per-session, per-user and admin revocation (M4) |
| T4 | Demo login used where real patients exist | Blocked: demo-only environments | A separate `AUTH_MODE`; startup refuses demo plus real, or OIDC that is missing or invalid; demo identities labelled in tokens and the UI (M2) |
| T5 | Brute-force or enumeration of patient codes | 35-bit codes; lookups are behind case, consent and role gates and are audited, but **not limited** | Per-principal and per-source limits with temporary blocking; identical answers for unknown and out-of-scope codes; abuse events (M5) |
| T6 | An unauthorized identity link (a patient login bound to the wrong patient) | n/a | Linking only through an explicit authorized confirmation, never automatic or AI matching (M3) |
| T7 | Investigation gaps: who accessed what, and failed logins | Partly audited | Audit events for authentication failures, sessions, lookup abuse, links and access denials (M6) |
| T8 | Credentials leaked in logs | The code avoids it; tested for audio and transcripts | Tests asserting tokens, session ids and codes never reach logs or audit rows (M4, M6) |

**Out of scope:**
- an attacker with database or file access (encryption at rest, KMS);
- network TLS termination;
- clinical validation;
- the WIP CDS, referral and surveillance routers, which stay disabled.

## 2. Architecture decisions (adopted)

**A1. Identity: a provider-neutral OIDC adapter with a backend-for-frontend.**
- The browser never holds a provider token or a session token in script-readable storage.
- Next.js runs the OIDC authorization-code flow with PKCE server-side and receives an ID token.
- It posts the ID token once to `POST /auth/oidc/session`.
- The backend validates it against the provider's discovery document and JWKS (PyJWT `PyJWKClient`, already installed) and creates a **server session** (A2).
- No passwords and no custom cryptography: the provider handles the credentials.
- The token must carry the configured issuer and audience, an allowlisted algorithm (RS256/ES256), `exp`/`iat`/`nbf` with a small clock skew, `sub`, and a `nonce` that matches the one Next.js issued.
- Missing or invalid OIDC configuration under `AUTH_MODE=oidc` means the backend refuses to start.

**A2. Sessions: opaque, server-managed sessions instead of long-lived JWTs.**
- The session token is 256 random bits. The database stores only SHA-256(token) in a `sessions` table, which records the user, role, auth mode, created, last-seen, idle and absolute expiry, `revoked_at` and the revocation reason.
- **Every request looks the session up**, so revocation is immediate, survives restarts, and applies to every backend process that shares the database.
- No refresh tokens: one opaque session with an idle timeout plus an absolute lifetime gives the same result with less to get wrong, and no replay window.
- If refresh tokens are needed later, they rotate with family-based replay detection (recorded as a follow-up).
- The demo HS256 JWT path is replaced by the same session mechanism, so demo and real mode share one revocation path.

**A3. Patient identity: `(issuer, subject)` bound to `patients.patient_id`.**
- A new `patient_identities` table holds the binding and its confirmation event (append-only).
- A binding is created only by an explicit, authorized confirmation. The proposed flow: an ANM issues a single-use, short-lived binding code to a patient they have registered, and the logged-in patient enters it.
- There is no automatic or AI matching and no merging.
- A patient principal reaches only the cases linked to their `patient_id`, through the existing `patient_link_events`, plus cases they created themselves.
- Historical visits are shown as historical and never re-labelled as current.

**A4. Rate limiting: a store behind an interface, implemented on SQLite.**
- There is no Redis. A SQLite-backed fixed-window counter with a temporary block is shared by every process on one host that uses the same database file, and it survives restarts.
- It is **not** distributed across hosts, and the documentation will say so.
- A `RateLimitStore` interface leaves room for a Redis implementation when there is real infrastructure.
- If the store is unavailable, lookups are refused (fail closed, 503) rather than left unlimited.
- The client IP comes from `X-Forwarded-For` only when the peer is a configured trusted proxy.

**A5. Audit.**
- New typed actions:
  - `auth_login_succeeded` and `auth_login_failed` (reason enum, never the token);
  - `session_revoked` (scope: self, user or admin);
  - `patient_identity_bound` and `patient_identity_unbound`;
  - `rate_limited`;
  - `access_denied` (cross-facility or role; outcome only).
- They go into the existing hash chain. No identifiers beyond actor, case and facility context are added.

## 3. Decisions needed from the owner

| # | Decision | Options | Recommendation |
|---|---|---|---|
| H1 | Identity provider | Generic OIDC (works with Keycloak, Entra ID, Auth0, Google), or one named provider | **Generic OIDC**, tested against a local test issuer (keys generated in tests). The provider is a deployment choice. |
| H2 | Clinician access model | Keep "any MO in their facility" (as today), or add explicit case **assignment** (MO sees only assigned cases plus a facility-wide queue of metadata) | **Keep the facility-scoped MO for now**; assignment changes the reviewer workflow and needs product sign-off |
| H3 | Patient identity binding | Single-use binding code issued by an ANM (proposed), or binding only in person by staff at the facility | **Binding code** issued by an ANM, valid for 15 min, single use, audited |
| H4 | Deployment and rate-limit store | Single host with SQLite (honest, not multi-host), or add Redis now | **SQLite store with the interface**; Redis when deployment is defined |
| H5 | Session lifetimes | Idle and absolute timeouts | **Idle 30 min, absolute 12 h** for staff; **idle 15 min, absolute 2 h** for patients; configurable |

**Owner decisions (2026-11-10):**
- **H1:** generic OIDC.
- **H2:** keep facility-scoped MO access (no assignment).
- **H3:** ANM-issued single-use binding code.
- **H4:** SQLite store on a single host, documented as not multi-host.
- **H5:** the recommended defaults, configurable.

## 5. Session lifecycle (M-A, implemented)

**Create:**
- `POST /auth/login` (demo mode, synthetic data only), or `POST /auth/oidc/session` (M-B), inserts a row into `sessions` (migration step 15) inside the same transaction as its `auth_login_succeeded` audit event.
- The client receives `sehat_s1.<43 url-safe chars>`, which is 256 random bits. The database keeps only its SHA-256.

**Use:**
- Every authenticated request calls `sessions.resolve`. It returns 401 for an unknown, malformed, revoked, idle-expired or absolute-expired session.
- A live session's idle expiry slides at most once a minute, is capped by the absolute expiry, and never slides on a revoked row.

**Lifetimes (H5, configurable within bounds):**

| | Idle | Absolute |
|---|---|---|
| Staff | 30 min | 12 h |
| Patient | 15 min | 2 h |

An out-of-range value refuses to start.

**End:**

| Endpoint | Who | Effect |
|---|---|---|
| `POST /auth/logout` | the session itself | revokes this session |
| `POST /auth/sessions/{session_id}/revoke` | the owner (anyone else gets 404) | revokes one session |
| `POST /auth/sessions/revoke-all` | the owner | revokes all of the owner's sessions |
| `POST /auth/admin/users/{user_id}/sessions/revoke` | admin role only | revokes all of one user's sessions |

`GET /auth/sessions` lists the caller's live sessions. The frontend's logout now revokes the server session before it clears the cookie.

**Integrity:**
- Rows are never deleted (trigger).
- Identity, role, auth mode, `created_at` and the absolute expiry can never change, and a revocation can never be undone (trigger).
- CHECKs enforce idle ≤ absolute, that `revoked_at` and the reason come together, and that OIDC sessions carry an issuer and subject.

## 6. Revocation guarantees and limitations (M-A)

**Guaranteed and tested** (`tests/privacy/test_sessions.py`):
- The next request after a revocation gets 401.
- Revocation survives a restart, because it's a fresh app on the same database file.
- A revocation made through one app instance is refused by another on its next request.
- Other users' sessions, and the user's other sessions when one is revoked, are unaffected.
- A revoked session cannot act at all, including logging out again.
- Concurrent use and revocation never leave a live revoked row.
- Tokens and token hashes never reach logs or the audit log.

**Limitations:**
- **Instances must share one SQLite file** (one host, owner decision H4). Multi-host deployments need a shared database, which is not provided.
- A request already authorized when a revocation commits may finish; the next one fails.
- No refresh tokens exist, so there is no refresh replay to detect. If refresh tokens are ever added, they need rotation with family-based replay detection.
- `JWT_SECRET_KEY` no longer signs anything. It is still validated at startup, and removing it is a follow-up.

## 4. Authorization matrix

Facility scope applies to every staff row. An out-of-scope case is the same 404 as a missing one.

| Principal | Own cases (created) | Cases linked to their bound record | Facility cases | Clinical content | Write / triage / review |
|---|---|---|---|---|---|
| Patient (OIDC) | read, write, consent | **read only**, while bound, linked and `link_visits` consent is in effect | — | own and bound cases only | own cases only (as before) |
| Patient (demo, shared account) | as before (synthetic data only) | **never**: a demo account cannot bind | — | — | — |
| ANM | yes | — | creator or handler | yes, own and handled | own and handled cases |
| Medical officer | — | — | **all cases in their facilities** (H2: no assignment) | yes | triage and review |
| Supervisor | — | — | read | **metadata only** (PR #14 D-B) | none |
| Admin | — | — | — | — | revoke a user's sessions; remove a patient binding |

**Identity binding:**
- An ANM issues a code for a visit they created or handle, only while it is linked to a patient and has `link_visits` consent.
- Only an OIDC patient can redeem a code.
- The code is single-use and expires after 15 minutes.
- A login stays bound to one record until it is unbound (409 otherwise).
- The patient themselves or an admin can unbind it.

## 7. Patient-code rate limiting (M-D)

**Scope:** every attempt to enter a code:
- `POST /cases/{id}/patient/lookup`
- `POST /cases/{id}/patient/link`
- `POST /me/patient-record/bind`

All three share one budget, and the check runs **before** the code is looked up.

**Keys:**
- The authenticated principal, and the network source (the client IP).
- `X-Forwarded-For` is used only when the peer is in `TRUSTED_PROXIES`, and then its rightmost untrusted hop.
- Keys are stored as HMAC-SHA256 values, never as the raw id or IP.
- Request ids play no part.

**Limits** (placeholder defaults, configurable within bounds; not production thresholds):

| Key | Per minute (burst) | Per hour |
|---|---|---|
| Principal | 10 | 60 |
| Source | 30 | 300 |

Exceeding either window starts a 15-minute block for that key, answered with `429 RATE_LIMITED` and `Retry-After`. The message never mentions a patient or a code.

**Store:**
- SQLite (migration step 17), counted under `BEGIN IMMEDIATE`, so concurrent attempts are counted exactly.
- Shared by every process on **one host** and kept across restarts.
- **Not distributed across hosts** (H4). A Redis store can implement the same `Store.hit` interface.

**Failure:** if the store fails, attempts are refused with `503 RATE_LIMIT_UNAVAILABLE` (fail closed).

**Anonymous callers:** these endpoints require a session, so anonymous requests get 401 before the limiter and are never counted.

**Sign-in:**
- `POST /auth/login` and `POST /auth/oidc/session` are limited per source: 30 per minute and 300 per hour by default.
- The check runs before anything is validated or audited, so anonymous callers cannot flood the audit log with failed sign-ins. One `rate_limited` event marks the start of a block.

**Secret dependency:** the bucket keys are an HMAC with `JWT_SECRET_KEY`.
- In development, if that variable is unset, each process makes up its own secret, so processes would **not** share limits. Set it whenever more than one process runs.
- Rotating the secret resets all buckets.

**Enumeration:**
- Unknown codes, out-of-scope codes and (for binding) expired or used codes all get the same answer.
- A mistyped code (wrong check character) is refused locally as invalid. That reveals nothing, because the check-character algorithm is public.

## 8. Audit events (M-E)

New typed actions, written to the existing hash chain:
- `auth_login_succeeded`: auth mode and the public session id.
- `auth_login_failed`: auth mode and a reason enum; the actor is recorded as `anonymous`, never with the submitted username, token or claims.
- `session_revoked`: logout, own session, own all, or an admin revoking a user's sessions, with a count.
- `patient_binding_code_issued`, `patient_identity_bound` and `patient_identity_unbound`, with a result enum and ids; never the code or the provider subject.
- `patient_record_viewed`: patient self-access, with a count.
- `rate_limited`: the start of a block (scope, key kind, minutes); never the key.
- `access_denied`: a refusal for a case that **exists**, with reason `facility_scope` or `not_permitted` and the access mode.
  - It is also written when a case is started in a facility outside the account's scope: mode `create`, with no case and never the attempted facility code. The intake form now offers only the facilities the server allows. It is written after the request because the request's own transaction rolls back. An unknown id writes nothing. This **changes the earlier rule** of writing no row for these refusals; see docs/11 §3.

**Never logged or audited:** session tokens, token hashes, ID tokens, binding or patient codes, passwords, audio, raw payloads.

**Not covered:**
- Ordinary successful clinical reads by staff are not audited per request; that was already the case before this work.
- The prototype has no export feature.

## 9. Deployment configuration

See `.env.example` (Authentication section).

**AUTH_MODE=oidc requires:**
- `OIDC_ISSUER` and `OIDC_CLIENT_ID`: https only, or http on a loopback host.
- Optionally `OIDC_JWKS_URL`; without it, the keys come from the issuer's discovery document, which must name the same issuer and an https `jwks_uri`.
- `OIDC_ALLOWED_ALGS`, from asymmetric algorithms only.
- The role and facility claim names.

**The frontend (Next.js, server side only) needs:**
- `OIDC_ISSUER`, `OIDC_CLIENT_ID` and `OIDC_REDIRECT_URI`;
- `OIDC_CLIENT_SECRET` for a confidential client.

Partial OIDC configuration, OIDC mixed with `ACCOUNT_FACILITIES` or `ALLOW_UNSCOPED_DEMO`, an unknown `AUTH_MODE`, an out-of-range session or rate setting, or a malformed `TRUSTED_PROXIES` each **refuse to start**.

**Environment:** the backend still runs only with `ENVIRONMENT=development` or `test`, in both modes. Lifting that requires the production prerequisites in §15.

## 10. Secrets management

**Secrets:**
- `JWT_SECRET_KEY`: at least 32 bytes. It keys the rate-limit HMAC and no longer signs sessions.
- `OIDC_CLIENT_SECRET`: frontend only.
- The provider's signing keys are never held here; only the public JWKS is fetched.

**Handling:**
- Secrets come from the process environment or `.env` and never from the repository.
- `repr(Settings)` hides them (PR #14).
- `.env.example` holds placeholders only.
- Rotating `JWT_SECRET_KEY` (`scripts/rotate_jwt_secret.py`) resets the rate-limit buckets. It no longer logs anyone out; to do that, revoke sessions.

## 11. Demo versus real-patient mode

| | Demo (`AUTH_MODE=demo`) | Real (`AUTH_MODE=oidc`) |
|---|---|---|
| Login | four shared synthetic accounts, no password | identity provider (code + PKCE), one login per person |
| Patient identity | shared, so **synthetic data only** | per person, bound explicitly to one patient record |
| Facility scope | `ACCOUNT_FACILITIES` (fails closed, or `ALLOW_UNSCOPED_DEMO=true`) | the provider's facility claim |
| Sessions | server sessions | server sessions |
| Fallback | — | **none**: demo login is 404 and demo sessions never authorize |

The login page asks `GET /auth/mode` and offers exactly one method.

## 12. Migrations

New steps:
- **15:** `sessions`, with an immutability trigger and no deletes.
- **16:** `patient_binding_codes` (use-once trigger) and `patient_identity_events` (append-only).
- **17:** `rate_limit_counters` and `rate_limit_blocks`.

**Properties:**
- All three steps are additive: no existing table or row is changed.
- The PR #14 schema fingerprint validator covers them automatically.
- A database from newer code is still refused.

**Recovery:**
- An older build refuses a step-17 database rather than write into it.
- Development data is synthetic, so delete the file (manually, never automatically) or use matching code.

**Upgrade impact:** existing demo JWTs stop working after the upgrade, because sessions are now server-side. Everyone signs in again.

## 13. Test results (2026-11-10, local)

**Backend:** 1572 passed, 14 skipped (all allowlisted), 5 xfailed.

New tests:
- `test_sessions.py`: 16
- `test_oidc.py`: 42
- `test_patient_identity.py`: 13
- `test_rate_limit.py`: 14
- `test_authz.py`: denial auditing

The PR #14 regression suites all pass unchanged, except where an intended behaviour change is recorded:
- the schema version pins moved from 14 to 17;
- the facility test's JWT-claim widening case now expects 401 (tokens carry no claims);
- the audit-count tests now account for audited logins;
- the denial test now expects one `access_denied` row.

**Frontend:** 128/128 tests (6 new OIDC helper tests); `tsc` clean; ESLint 0 errors (3 old warnings).

**CI-equivalent run** (fresh Python 3.11 with CI's exact install): migrations 32/32; full suite 1527 passed, 15 skipped (all allowlisted), 5 xfailed. The skip gate passes. Its minimum was raised from 1390 to 1520, and the 5 new security test modules are required.

**Secret scan:** gitleaks runs in CI on a PR. Locally, a pattern search of the change set found no keys, tokens or secrets.

**GitHub CI:** not run yet, because nothing is pushed.

## 14. Known limitations

- **Not multi-host.** Sessions and rate limits share one SQLite file, so the guarantees hold across processes on one host only.
- **No refresh tokens.** Sessions expire, and users sign in again.
- **The OIDC flow has been tested only against a local test issuer.** No real provider has been integrated or penetration-tested. The frontend callback has unit-tested helpers, but no end-to-end browser test.
- **MOs see all cases in their facility** (H2: no assignment).
- **Patient self-access is read-only and shows visit metadata** (date and scenario). Clinical detail stays behind the existing case endpoints.
- **The patient-code and binding-code thresholds are placeholders.**
- **35-bit codes rely on rate limits and expiry.**
- **Successful staff reads are not audited per request.**
- **No export feature exists.**
- **Demo mode still has the shared patient account.** It is synthetic-data-only by policy and startup guard.
- **Production deployment is still blocked** by `ENVIRONMENT`.

## 15. Production prerequisites (not met)

These are needed before any real patient data:
- a real identity-provider tenant, with MFA for staff and a reviewed patient sign-in method;
- a shared session and rate-limit store, if more than one host;
- TLS, secret management (KMS) and encryption at rest;
- a penetration test of the OIDC flow;
- retention for sessions, rate-limit rows and audit;
- a DPDP review;
- clinical review of the summary wording (`docs/clinical/summary-review-checklist.md`);
- lifting the `ENVIRONMENT` guard, a deliberate change with its own review.


Pending sections:
- 4 Authorization matrix
- 5 Session lifecycle
- 6 Revocation guarantees and limitations
- 7 Rate limiting
- 8 Audit events
- 9 Deployment configuration
- 10 Secrets management
- 11 Demo versus real-patient mode
- 12 Migrations
- 13 Test results
- 14 Known limitations
- 15 Production prerequisites
