# CampusShield — Frontend

**Phases 3A–3B, extended through 4D–4E.** React + Vite + TypeScript + Tailwind
+ Firebase Web SDK. Application shell, authentication, the student report
submission workflow, the responder incident queue and case lifecycle (4D),
and in-app notifications with account display-name editing (4E).

Backend design is [BACKEND_ARCHITECTURE.md](../BACKEND_ARCHITECTURE.md).

---

## Quick start

```bash
cd frontend && npm install
cp .env.example .env      # fill in your Firebase web configuration
npm run dev               # http://localhost:5173
```

The backend must be running on port 5000 and must list `http://localhost:5173` in
its `CORS_ORIGINS`. That is the default, so in practice:

```bash
cd backend && python run.py
```

---

## What Phase 3A contains

| Route | Access | Purpose |
|---|---|---|
| `/login` | signed-out only | Email and password sign-in |
| `/register` | signed-out only | Create a Firebase credential, then an application account |
| `/dashboard` | authenticated | Account, role, and campus data counts |
| `/report` | authenticated | The eight-step reporting form |
| `/report/submitted` | authenticated | Confirmation, shown once |
| `/reports` | authenticated | The caller's own identified reports |
| `/reports/:ref` | authenticated | One report in full — status timeline included (Phase 4D) |
| `/incidents` | authenticated | Responder incident map, queue, evidence, dispatch, and case status (Phase 4D). Students get an explanation; every endpoint behind it refuses them server-side |
| `/notifications` | authenticated | The caller's own notification inbox (Phase 4E) |
| `/account` | authenticated | Read-only identity for everyone; editable display name for staff (Phase 4E) |
| `/` | — | Redirects to `/dashboard` |
| `*` | — | Not found |

Deliberately absent: push notifications (in-app notifications are built — see
below), the aggregated public safety map, and anything ML.

## Case status (Phase 4D)

`STATUS_LABELS` in `lib/api.ts` has covered all eight `core.report.
current_status` values since Phase 3B; only `submitted` was ever reachable
until the backend gained a writer for the others. See `../CASE_LIFECYCLE.md`.

Two places read it now:

- **`MyReportsPage`** — the current status, as a badge, same as before. Each
  row now links to `/reports/:ref`.
- **`ReportDetailPage`** (new) — the full timeline from `GET /reports/<ref>`'s
  `status_history`, which the frontend had never consumed before this phase
  despite the endpoint returning it since Phase 1. Already filtered
  server-side to `visible_to_reporter` rows; the page renders exactly what it
  receives and asks for nothing more.

On the responder side, `CaseLifecyclePanel.tsx` (inside `IncidentsPage`) is
**deliberately a separate component from the dispatch controls** next to it —
case status and dispatch status are different state machines, and the UI keeps
them visually and structurally distinct rather than merging them into one
"incident status" concept. `CASE_TRANSITIONS` in `lib/api.ts` is a courtesy
copy of the server's state graph, used only to decide which buttons to offer;
the server re-validates every transition regardless.

## Notifications and account (Phase 4E)

**Polling, not push.** `useUnreadCount` (`hooks/useNotifications.ts`) calls
`GET /notifications?limit=1` every 45 seconds while signed in and reads
`unread_count` from the response — there is no Firebase Cloud Messaging
integration anywhere in this codebase, and the bell does not pretend
otherwise. The bell (`NotificationBell` inside `AppShell.tsx`) links to
`/notifications`; `NotificationsPage` lists the full inbox and marks entries
read individually.

A notification's `related_public_ref` links to `/reports/:ref` for a
`status_update` (the reporter's own view, which the reporter is always
authorised to reach) and to `/incidents` for an `assignment` — a responder
works cases from the queue, and `IncidentsPage` has no deep link to one
specific case yet, so pointing at the reporter-scoped report view would be
the wrong screen for them, not merely a less convenient one.

**`AccountPage`** (`/account`) is the read-only identity card every account
already had inline on `DashboardPage`, now with a real destination and, for
`security` / `icc` / `admin` accounts only, an editable `display_name` field
that calls `PATCH /auth/me`. For a student it explains why the field is
absent rather than showing a form that could only ever fail — the constraint
is `identity.app_user`'s own (`ck_app_user_student_has_no_name`), not a
frontend rule invented to match it. There is no profile picture: see
`BACKEND_ARCHITECTURE.md` §14.4 for why that is a scope boundary, not a gap.

## The reporting workflow

Eight steps rather than one long form: a student may be filling it in on a phone,
possibly upset, possibly in a hurry, and one question at a time is easier to
answer than twelve at once.

`src/report/draft.ts` holds the draft shape and every rule, as pure functions
with no React and no network. `src/report/steps.tsx` renders them.
`ReportPage.tsx` decides when a step may be left. Every rule mirrors one the
backend already enforces — **the backend remains authoritative and nothing in the
browser is a security control.**

Three details worth knowing:

- **`buildPayload` is the only place a request body is constructed**, and a test
  asserts its exact key set against `CreateReportSchema`. No user id, email,
  Firebase UID, role, or `submission_mode` — the reporter comes from the verified
  token, and `anonymous` is the only lever a client has over identity.
- **`contact_consent` is omitted entirely on an anonymous report.** There is no
  attribution row for consent to attach to, so sending it would describe a
  contact channel that cannot exist.
- **The access token is passed through router state and never persisted.** A
  refresh losing it is correct: only its SHA-256 reaches the database, so any
  recoverable copy would defeat the mechanism.

## The campus map on step 2 (Phase 5)

`StepLocation` (step 2, "where did this happen?") now shows an interactive
map alongside the `<Select>` it always had — `src/report/LocationMap.tsx`,
a student-facing sibling of `src/responder/IncidentMap.tsx`. Same library
(Leaflet, directly, no React wrapper), same reasoning, deliberately not the
same component: one is a read-heavy, multi-marker responder view; this one
is a single-choice picker a student taps once, and coupling them would
couple two things that change for different reasons.

**The list is not a fallback for the map — the map is the second way in.**
`GET /locations` only ever returns locations whose coordinates are
verified, so in principle every location this component receives can be
drawn — but the `<Select>` is what a screen reader uses, what works on the
slowest phone, and what keeps working if a tile server is unreachable. A
student can complete the whole form without ever touching the map. When
`locations` is empty (still the real system's actual state — see
`CAMPUS_LOCATIONS.md`), `StepLocation` shows the same honest "not available
yet" message it always has, and no map renders at all.

No new endpoint, no new field: the map consumes exactly what `useCatalog`
already fetched. Clicking or keyboard-activating a marker calls the same
`update({ locationId })` the `<Select>`'s `onChange` calls — the two stay in
sync because they write to the same piece of state, not because either
observes the other.

## Emergency ("SOS") trigger (Phase 6)

`components/SosButton.tsx` renders a persistent, floating control in
`AppShell` for every signed-in account — reachable from any page, not just
the report form. See its own module docstring for the full reasoning; the
short version:

- **Press-and-hold, not a confirmation dialog.** No modal pattern exists
  anywhere in this codebase, and a copied generic "Are you sure?" dialog adds
  exactly the wrong thing at a moment that may have neither the time nor the
  attention for a second decision and a small target. Holding for 1.5s
  (`SOS_HOLD_MS`) needs sustained, deliberate contact instead — immune to a
  stray tap, needs no reading, and behaves identically across mouse, touch,
  and keyboard (`pointerdown`/`pointerup` and `keydown`/`keyup` over the same
  timer).
- **Amber, not red.** `Button.tsx`'s `danger` variant is explicitly reserved
  and explicitly *not* for this — raising an emergency is a calm, deliberate
  request for help, the same reasoning that keeps the app's own wordmark a
  quiet shield rather than a siren (`AppShell.tsx`).
- **Geolocation is opportunistic, never blocking.** `lib/geolocation.ts`'s
  `getEmergencyPosition()` starts the moment the hold begins — so a fast fix
  usually finishes before the hold does, at zero extra latency — and
  resolves to `null` rather than rejecting on denial, unavailability, or
  timeout. The alert is sent the instant the hold completes regardless of
  whether a position arrived.
- **`POST /reports/emergency`** needs no category, location, or narrative —
  the backend fills all three in. On success the app navigates to
  `EmergencyConfirmationPage.tsx`, which shows the reference, states plainly
  whether a location was resolved, and offers an `EvidenceUpload` (the same
  component `ReportPage` uses) to attach photos afterward via
  `POST /reports/<ref>/evidence` — never required, and the reporter can also
  reach this later from their reports list.
- **The queue side** — the responder-facing half of this feature — lives in
  `IncidentsPage.tsx`'s attention banner, documented in
  `RESPONDER_ARCHITECTURE.md` §3.

## Demo campus data, visibly marked (Phase 5B)

`CampusLocation.is_synthetic` / `Destination.is_synthetic` (see
`DATABASE.md` §30) mark a development/demo fixture — never a real,
surveyed location. Both maps render one visibly differently from the
other:

- **`LocationMap.tsx`** (student) draws a demo marker dashed and amber
  instead of the ordinary blue/grey, appends "(DEMO)" to its tooltip, and
  shows a banner above the map — `data-testid="demo-data-notice"` — when
  any visible location is synthetic.
- **`IncidentMap.tsx`** (responder) does the equivalent: a dashed outline
  independent of the existing emergency/selected colours, "(DEMO)" in the
  tooltip, and its own banner.
- **`IncidentDetail.tsx`** shows an explicit "DEMO MODE" notice in the
  "Where" section and appends "(DEMO)" to the navigate link's label —
  added at the display layer only, so `NavigationProvider` itself (see
  `responder/navigation.ts`) carries no awareness of data provenance.

This is deliberately *visible*, not merely a difference the developer
console could reveal: `scripts/seed_demo_campus_locations.py` also names
every fixture with a `DEMO —` prefix and a `(NOT A REAL LOCATION)` suffix,
so even a screen not showing the badge — a raw `<Select>` option, the
report review step — still self-describes as non-real, in plain text.

---

## Structure

```
src/
├── config/firebase.ts     public web config; lazy init so a missing .env
│                          shows a message instead of a blank screen
├── lib/
│   ├── apiClient.ts       the single door to the API — token injection,
│   │                      one error shape
│   ├── api.ts             typed endpoint definitions
│   └── validation.ts      pure form validators
├── auth/
│   ├── context.ts         the context object and its types
│   ├── AuthContext.tsx    the provider — Firebase user vs application account
│   ├── useAuth.ts
│   └── firebaseErrors.ts  SDK codes translated for people
├── components/
│   ├── ui/                Button, Input, Card, Alert, Spinner, ErrorState
│   ├── layout/AppShell    Navigation (incl. the notification bell), AuthLayout, SiteFooter
│   └── ProtectedRoute.tsx route guards
├── pages/                 Login, Register, Dashboard, Notifications, Account,
│                          MyReports, ReportDetail, ReportConfirmation, Incidents
├── report/                the reporting wizard
│   ├── draft.ts           draft shape + every validation rule, pure functions
│   ├── steps.tsx          the eight step components, presentational only
│   ├── LocationMap.tsx    the step-2 campus map (Phase 5)
│   └── EvidenceUpload.tsx photo picker, staged-upload lifecycle
├── responder/             the incident queue and detail
│   ├── IncidentMap.tsx    the responder-facing campus map (Phase 4B-2)
│   ├── IncidentDetail.tsx where/when/evidence/case-lifecycle/dispatch, assembled
│   ├── CaseLifecyclePanel.tsx, TriagePanel.tsx, EvidenceViewer.tsx
│   └── navigation.ts      NavigationProvider — geo: on mobile, OSM in a browser tab
├── hooks/
│   ├── useCatalog.ts      locations + categories
│   └── useNotifications.ts unread count, polled — no push infra exists
└── test/                  Firebase module mock, fetch stub, render harness
```

### The dependency rule

```
pages → components → hooks → lib/api → lib/apiClient → Flask
   ↓                              ↑
 auth ──────────────────────────────
```

`lib/` knows nothing about React. `auth/` knows nothing about pages. Components
never call `fetch`.

---

## Two identities, and why they are separate

The single most important idea in this codebase:

- the **Firebase user** proves someone signed in
- the **application account** (`identity.app_user`) says who they are here and
  what role they hold

They are not the same thing. A user can be perfectly signed in to Firebase and
have no account here at all — the state between creating a credential and
provisioning a row, and where anyone lands if registration is interrupted. That
is why `AuthStatus` has a `needs-provisioning` value rather than being a boolean,
and why `ProtectedRoute` offers to finish setup instead of redirecting a
signed-in user back to a sign-in page they would immediately bounce off.

**The role always comes from `GET /auth/me`.** It is never inferred, never read
from a token claim, never cached across sign-ins. The backend re-checks every
request regardless, so nothing in the browser is a security boundary —
`ProtectedRoute` decides what to *render*, not what is *permitted*.

---

## What is safe to put in `.env`

Everything with a `VITE_` prefix is inlined into the bundle and visible to anyone
who opens developer tools. That is fine for Firebase's **public web
configuration**: those values identify the project, they do not authorise
anything. `VITE_FIREBASE_API_KEY` is an identifier despite its name.

**Never** put a service-account key, `FIREBASE_CREDENTIALS_*`, a database URL, or
any server pepper under `frontend/`. A service-account key can mint a token for
any user in the project, and anything here is published to every visitor. Those
belong in `backend/.env`.

---

## Commands

```bash
npm run dev           # dev server on :5173
npm run typecheck     # tsc --noEmit
npm run lint          # eslint
npm run format        # prettier --write
npm run test          # vitest
npm run build         # tsc -b && vite build
npm run verify        # all of the above
```

---

## Testing

**330 tests across 20 files** (`npx vitest run`, actually executed — this
count is not aspirational). Firebase is replaced at the module boundary
(`src/test/firebaseMock.ts`) and the network by a route-table fetch stub.
Nothing this application owns is stubbed — the auth context, route guards,
forms, and API client all run for real against those two seams.

The table below covers the files kept in sync with this document; it predates
full reconciliation with every file added in Phase 4B/4C/4D (`IncidentsPage`,
`ReportDetailPage`, `responder/*` other than the map, `report/EvidenceUpload`
among them) and is not a complete index — `npx vitest run` is the source of
truth for the total.

| File | Tests | Scope |
|---|---|---|
| `lib/apiClient.test.ts` | 11 | Token injection, error normalisation |
| `auth/AuthContext.test.tsx` | 9 | State machine, role source, logout |
| `components/ProtectedRoute.test.tsx` | 10 | Guards, provisioning gate, redirects |
| `pages/LoginPage.test.tsx` | 19 | Validation, Firebase errors, keyboard, a11y |
| `pages/RegisterPage.test.tsx` | 16 | Validation, two-step sign-up, no role sent |
| `pages/DashboardPage.test.tsx` | 14 | Account display, catalogue counts, empty state |
| `report/draft.test.ts` | 27 | Step rules, payload shape, no identity fields |
| `report/LocationMap.test.tsx` | 7 | Marker render/click, empty state, unmount safety, demo-data notice (Phase 5/5B) |
| `pages/ReportPage.test.tsx` | 37 | Wizard navigation, all four report shapes, errors, campus map, demo-data notice (Phase 5/5B) |
| `pages/ReportConfirmationPage.test.tsx` | 17 | Reference, one-time token, honest promises, notification expectations (Phase 4E) |
| `pages/MyReportsPage.test.tsx` | 10 | List, empty state, anonymous absence |
| `components/layout/AppShell.test.tsx` | 5 | Notification bell: link, polling, badge, cap at "9+" (Phase 4E) |
| `pages/NotificationsPage.test.tsx` | 10 | Inbox list, mark-read, category-aware linking, scoping (Phase 4E) |
| `pages/AccountPage.test.tsx` | 11 | Identity display, staff display-name edit, student refusal (Phase 4E) |

A few assertions are worth knowing about because they encode product decisions
rather than mechanics:

- **Sign-in never reveals whether an account exists.** `auth/user-not-found` and
  `auth/wrong-password` produce the same message, so the form cannot be used to
  discover registered addresses.
- **The password never appears in an API request.** Asserted across every call
  the registration flow makes.
- **No role is ever sent to the server.** The registration body is checked for it.
- **An empty location list is a state, not an error.** `GET /locations`
  legitimately returns nothing until campus coordinates are verified.
- **The interface does not shout.** No exclamation marks, no shouted capitals, no
  siren emoji, and red is reserved for genuine errors.

---

## Design

Calm, and deliberately so. This is a tool people reach for when something has
gone wrong, and an interface that shouts makes that worse — alarm colours and
warning triangles raise the cost of opening the app at exactly the moment someone
is deciding whether to bother.

- **Teal**, institutional and steady without being clinical. Red exists only for
  genuine errors, so that when it appears it means something.
- **Mobile-first.** 44px minimum touch targets, 16px minimum input text (below
  that iOS Safari zooms on focus and throws the layout).
- **Accessible by default.** Real `<label>` elements rather than placeholders,
  errors linked with `aria-describedby` and announced with `role="alert"`, a
  visible focus ring everywhere, a skip link on every page, and
  `prefers-reduced-motion` respected.
- No frightening imagery. The shield mark is a quiet outline; emergency reporting
  will be a clearly-labelled choice, not a red panic button.
