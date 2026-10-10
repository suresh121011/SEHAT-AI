# SEHAT AI frontend — design notes (2026-10 redesign)

Frontend-only redesign on branch `feat/sehat-ai-frontend-redesign`. No backend, API contract, rule or test changes.

## Direction

Premium clinical teal on a cool off-white page, with a dark-teal sidebar (`--shell-bg`). Chosen over a blue/neutral
direction (blue already means "info") and a dark-teal + sage direction (sage reads too close to GREEN urgency).
Typeface stays Atkinson Hyperlegible (low-vision legibility; also the UI/UX Pro Max recommendation for healthcare).

## Tokens (`src/app/globals.css`)

- **Unchanged meaning:** every urgency (`--urg-*`), escalation (`--esc-*`), AI, consent, hypothetical and focus token,
  with their documented contrast ratios. RED/YELLOW/GREEN appear only for urgency, always with a word and an icon.
- **Added:** `--page #f3f6f5`, `--surface-2`, `--border-strong`, elevation (`shadow-card`, `shadow-raised`), layout
  (`--sidebar-w` 15.5rem / `--sidebar-w-collapsed` 4.5rem, `--topbar-h`, `--content-max` 90rem), motion
  (`--dur-fast` 150ms, `--dur` 200ms; reduced motion respected globally).
- **Radii:** cards and panels `rounded-xl` (12px); controls and inner boxes `rounded-lg` (8px); badges `rounded-md`.
- **Server status** uses teal / amber, never green / red.

## Shell and components

- `components/shell/AppShell.tsx` — role-aware sidebar (collapsible icon rail ≥1024px, native `<dialog>` drawer below),
  top bar with section, server reachability (`GET /health`, reports only what it returns), role and log out, and the
  prototype footer. Mounted by `app/intake/layout.tsx` and `app/dashboard/layout.tsx`; `/login` is standalone.
- `components/shell/nav.ts` — only routes that exist and that the role may open.
- `ui.tsx` `PageHeader`, `components/MetricCard.tsx`, restyled `WorkstationBar` (now a light page header).
- New route `/dashboard/overview` (MO home; `lib/auth.ts` `homeFor`): read-only, built only from `GET /triage/queue`,
  server order kept, no actions.
- The review workstation keeps every safety section visible (no tabs): three columns only at ≥1536px, evidence stacks
  under the case below that.

## Omitted from the reference mockup (no backend support)

| Mockup element | Backend work it would need |
|---|---|
| Referral tracking (status, destination, timeline) | Referral packet + closure-tracking endpoints (docs/05 §5); no delivery may be implied until confirmed |
| Reports "Triage accuracy %" | A governed ground-truth / outcome dataset; not derivable today |
| Recent activity feed | A cross-case, facility-scoped audit/activity endpoint (audit is per case only) |
| Knowledge base | A read-only rules-and-sources endpoint (or reviewed static content from docs/10) |
| Settings / profile / offline mode | Account and preference endpoints; offline/PWA sync (docs/05 §8) |
| Patient names, photos, phone numbers | None — by design the app stores only pseudonymous tokens |
| Notifications bell | A notification channel (governance reports `notification_channel: none`) |

## Verification

`npx tsc --noEmit`, `npm run lint` (0 problems), `npm test` (88/88) and `next build` pass. Screens were checked in
headless Chrome at 390, 768, 1440 and 1600px against the running backend with synthetic data.
