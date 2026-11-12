# Security policy

SEHAT AI is a **research prototype** that runs on synthetic data with shared, password-less demo accounts. It must not
hold real patient data. Security issues still matter: the same code is meant to grow into something that will.

## Reporting a vulnerability

- **Do not open a public issue** for a security problem, and never include real patient data in a report.
- Use GitHub's private vulnerability reporting for this repository (**Security** tab → **Report a vulnerability**,
  `github.com/suresh121011/SEHAT-AI`). If that option is not shown, contact the maintainers privately through GitHub
  and ask for a private channel.
- Include: what you found, the affected file or endpoint, steps to reproduce with **synthetic** data, and the impact.
- The maintainers will acknowledge the report, agree a fix and a disclosure date with you, and credit you if you wish.

## Scope

| In scope | Out of scope |
|:---|:---|
| Backend (`backend/`), frontend (`frontend/`), scripts, CI workflow, the agent harness (`.claude/`) | Third-party services (Azure, Sarvam, Google), their own infrastructure and terms |
| Authentication, sessions, OIDC, access control, facility isolation | The known demo-account limitation below |
| Consent, PII redaction, the AI gateway, audit integrity | Attacks that need physical access to the demo laptop |
| Secrets handling, dependency and supply-chain issues | Denial of service against a laptop demo |

Only the `main` branch is supported.

## Controls in place

Details, code paths and tests are in `GUARDRAILS.md` §4 and §5.

- **Identity:** server-side sessions (256-bit tokens, only hashes stored, idle and absolute timeouts, immediate
  revocation); OpenID Connect with asymmetric algorithms, nonce and PKCE; patient login bound to a record only through a
  single-use code.
- **Access:** roles, case access checked separately from consent, facility isolation that fails closed, identical 404
  for unknown and forbidden cases, denials audited by reason.
- **Abuse limits:** rate limits on patient-code entry and sign-in, with temporary blocks.
- **Integrity:** append-only clinical tables enforced by database triggers; hash-chained audit log.
- **Configuration:** the server refuses to start on unsafe settings (demo auth outside development/test, short
  `JWT_SECRET_KEY`, wildcard CORS, incomplete OIDC or cloud settings, non-loopback local model URL).
- **Browser:** no session token readable by page scripts, every call through the same-origin proxy, no WebSocket
  endpoint, no credential in a URL.
- **Models:** downloads pinned by revision and SHA-256; the local model server listens on loopback only, with an API
  key file, no web UI and no prompt logging; cloud use needs explicit switches and a synthetic-data acknowledgement.
- **Secrets:** `.env` and `.env.*` are git-ignored (only `.env.example` with placeholders is tracked); gitleaks scans
  every pull request; `backend/scripts/rotate_jwt_secret.py` writes a new `JWT_SECRET_KEY` into `.env` without
  printing it (the key now protects the rate-limit counters); coding agents are blocked from reading `.env`
  (`HARNESS.md`).
- **Process:** CI with a skip gate on the safety suites, branch protection, one approving review per change.

## Known limits

- **Demo accounts are shared and password-less.** They work only in `development` and `test`; the backend refuses to
  start with them anywhere else. They cannot attribute actions to a real person.
- **Single host:** one SQLite database; rate limits and sessions are shared only by processes on that host.
- **The audit log is tamper-evident, not immutable:** someone with access to the database file can rewrite it.
- **PII redaction is heuristic**, with documented misses; it is not anonymization.
- **Not yet in place:** a penetration test, automated dependency updates (no Dependabot configuration), a web
  application firewall, encryption at rest for the SQLite file, and a breach response procedure.

## If a secret leaks

1. Revoke or rotate it at the provider first (Azure, Sarvam, Google, GitHub), then remove it from the repository.
2. Rotating `JWT_SECRET_KEY`: run `backend/scripts/rotate_jwt_secret.py` yourself (it edits your `.env` and never
   prints the key), then restart the backend.
3. Tell the maintainers privately. Do not paste the secret into an issue, a pull request or a chat.
