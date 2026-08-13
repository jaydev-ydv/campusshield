# CampusShield — Product Completeness & User Communication (Phase 4E)

**Status:** Implemented and verified end to end against real PostgreSQL and a
real running server.
**Scope:** in-app notifications for status changes and case assignment; the
one editable account field, `display_name`, for staff; the frontend surfaces
for both (bell, inbox, account page); and the audit-and-copy fixes those
required elsewhere in the reporting flow.

This document covers the gap a full-system audit found at the start of this
phase: a professional reporting product needs a way to tell a person that
something happened to their case, and a way to answer "who am I in this
system" beyond a read-only card that could not be edited. Neither existed.
For the database design and privacy reasoning behind what was built, see
`DATABASE.md` §28. For the backend service/route shape, see
`BACKEND_ARCHITECTURE.md` §14. This document is the product and process
record: what was found, what was deliberately left alone, and why.

---

## 1. The gap, precisely

Before this phase:

- `notify.notification`, `notify.device_token`, and `notify.broadcast_alert`
  were fully specified in migration `0001` — enums, columns, indexes,
  constraints, a documented privacy design — and **never written to or read
  from by any application code.** A student whose report was triaged,
  investigated, and resolved learned about none of it except by manually
  reloading `MyReportsPage`.
- `identity.app_user.display_name` existed and was already shown to staff
  (case-assignment labels, since Phase 4D) — but there was no way to *set*
  one. Every staff account's name was whatever was typed directly into the
  database at seed time.
- There was no account-settings surface of any kind. `DashboardPage` had a
  read-only "Your account" card and nothing else; there was no `/account`
  route, no notification indicator anywhere in the navigation, and no
  endpoint a staff member could call to change anything about their own
  identity.

## 2. Research vs. inference vs. policy — how the scope was drawn

The audit distinguished four kinds of claim before any code was written:

1. **What already existed** — `notify.*`'s schema, `display_name`'s column
   and CHECK constraint, `CaseService`'s post-transition and post-assignment
   hooks (unused, but structurally ready for a side effect). Established by
   reading migration `0001`, `DATABASE.md` §16, and `case_service.py`
   directly, not assumed.
2. **Research-derived expectations of a professional incident-reporting
   product** — status notifications, a reachable account settings page, a
   profile picture as a baseline capability. These informed the audit
   question ("does CampusShield have this"), not the implementation, which
   had to be checked against #3 and #4 before proceeding.
3. **Engineering inference** — that notifications belong as a side effect of
   `CaseService`, not a parallel write path (the transition and the
   notification must not disagree about what happened); that honest
   delivery-state semantics matter more than a green "delivered" checkmark
   nobody can verify.
4. **Institutional-policy questions this phase does not have the authority to
   answer** — whether to build push notifications (needs a Firebase Cloud
   Messaging project and a decision about always-on device permissions);
   whether to allow a staff avatar upload (needs a decision about what image
   moderation, if any, applies to staff-facing uploads); whether to build
   broadcast campus alerts (needs a decision about who may issue one). All
   three are named below as deliberately not built, not silently skipped.

The profile-photo question specifically resolved against the project's own
prior decision, not a generic best practice: `identity.app_user` carries
`CHECK (role <> 'student' OR display_name IS NULL)`, a constraint whose
express purpose (per its own migration comment and `DATABASE.md` §9) is "a
field not collected cannot be breached." A student-facing photo is the same
category of field. Building one would have reversed a decision this project
had already made deliberately, not filled a gap it left open.

## 3. Feature-gap matrix (as assessed at the start of this phase)

| Feature | State | Action taken |
|---|---|---|
| Status-change notification | MISSING (schema only) | Built — §4 |
| Assignment notification | MISSING (schema only) | Built — §4 |
| Notification inbox (frontend) | MISSING | Built — §6 |
| Notification bell / unread indicator | MISSING | Built — §6 |
| Account settings page | MISSING | Built — §6 |
| Editable staff display name | MISSING | Built — §5 |
| Student profile name/photo | BLOCKED by design | Left blocked — §2 |
| Staff avatar/profile photo | MISSING, out of scope | Deliberately not built — §9 |
| Push notifications (FCM) | MISSING, external blocker | Deliberately not built — §9 |
| Broadcast campus alerts | MISSING, policy blocker | Deliberately not built — §9 |
| Anonymous-reporter notification capability | N/A by design | Confirmed still correctly absent — §4 |
| Confirmation-page copy about notifications | PARTIAL (silent on the topic) | Fixed — §7 |

## 4. Notifications — what triggers one, and what never does

Two triggers, both already named by `notification_category` in migration
`0001`:

- **`status_update`** — a case moved to a status the reporter is allowed to
  see (`visible_to_reporter = true` on the `case_status_history` row).
  `CaseService.change_status` calls `NotificationService.notify_status_change`
  after the transition is recorded, never before — a notification describes
  something that already happened in the database, not something about to.
- **`assignment`** — a responder was handed a case by someone other than
  themselves. Self-assignment writes nothing; the person doing it already
  knows.

**An anonymous report cannot produce a notification, structurally, not by a
filter.** `notify_status_change` resolves a recipient via
`ReportRepository.access_context(report).reporter_user_id`, which is `None`
for any report with no row in `identity.report_attribution` — which is every
anonymous report, by the same construction that has protected anonymity since
Phase 1. There is no code path that could accidentally notify an anonymous
reporter, because there is no `user_id` for such code to reach. A
device-token-to-report binding would restore the capability by rebuilding the
exact link anonymity exists to prevent — `DATABASE.md`'s own pre-existing
design note on `notify.notification` says exactly this, and this phase did
not build a workaround.

**A title or body never carries narrative text, evidence content, a risk
score, or ML output.** Every template is a fixed string plus a status label
and the report's `public_ref` — nothing sourced from what a student or
responder wrote. `NotificationService._STATUS_LABELS` is the one place status
wording lives for this purpose, kept intentionally independent of the
frontend's own `STATUS_LABELS` so the two may drift in phrasing but never in
which statuses exist — enforced by a test asserting the label dict's keys
equal the full `ReportStatus` enum.

**Delivery is honest.** `DeliveryState.SENT` is written at creation and means
"available through `GET /notifications` right now" — not "pushed to a
device." No Firebase Cloud Messaging credentials exist anywhere in this
deployment; `fcm_message_id` is never set; `notify.device_token` is never
read. `delivery_state` and `fcm_message_id` are both excluded from the
client-facing serializer specifically so the API response itself cannot be
read as a stronger claim than the one being made.

## 5. Account identity — the one editable field, and the boundary around it

`PATCH /auth/me` sets `display_name` for `security` / `icc` / `admin`
accounts. A student calling it is refused with 403 — checked in
`AccountService.update_display_name` before any write is attempted, so the
answer is "your role doesn't have this," not a raw 409 from the CHECK
constraint underneath. That constraint remains, and remains the backstop:
this codebase's general pattern (see also case-status-transition validation)
is to enforce a rule in the service layer for a clear error and again in the
schema so the guarantee holds even if the service is bypassed.

This is deliberately narrow. It does not touch, and nothing about it
implies:

- **Report attribution** (`identity.report_attribution`) — who filed a
  report. Unaffected; `PATCH /auth/me` has no reach into report data at all.
- **Report display to a responder** — what an incident's assignment card
  shows. Already existed (Phase 4D); this phase only made the value behind
  it settable by its owner instead of fixed at seed time.
- **Anonymity** — an anonymous report carries no identity to begin with, so
  an account's display name, whatever it is, has nothing to attach to.

## 6. Frontend surfaces

- **Notification bell** (`AppShell.tsx`, desktop and mobile nav) — a link to
  `/notifications` with an unread-count badge (capped at "9+"), backed by
  `useUnreadCount`, which polls `GET /notifications?limit=1` every 45 seconds
  while signed in and reads only `unread_count`.
- **`NotificationsPage`** (`/notifications`) — the full inbox, paginated,
  each entry showing category, title, body, timestamp, and (when present) a
  link to the related case: `/reports/:ref` for a `status_update`
  (the reporter's own authorised view), `/incidents` for an `assignment`
  (there is no per-case deep link into the responder queue yet, so this
  points at the right screen rather than a URL that would be wrong for a
  responder). An unread entry offers "Mark read"; a read one does not.
- **`AccountPage`** (`/account`) — identity (email, role, status) read-only
  for everyone. For a student, an explanation of why no name field is
  offered. For staff, the editable `display_name` form, calling `PATCH
  /auth/me` and then `refreshAccount()` so the rest of the session — case
  assignment labels included — reflects the change immediately rather than
  after the next full reload.
- **`DashboardPage`**'s existing "Your account" card gained a "Manage
  account" link to `/account`; its content is otherwise unchanged.

## 7. Confirmation-page copy

`ReportConfirmationPage`'s "what happens next" step 3 previously said only
that status would update, with no mention of *where* a person would see that.
It now states the truth for each submission mode instead of staying silent:
an anonymous report explicitly says notifications are not possible for it
("there is no account to send one to") and that the code above is the only
way to check status; an identified report says the reporter will see updates
in their CampusShield notifications, in addition to whatever direct-contact
consent language already applied.

## 8. Verification

- **Backend:** `pytest` — 466 passed (437 pre-existing baseline + 29 new: 9
  in `test_account_provisioning.py` for `PATCH /auth/me`, 20 in the new
  `tests/test_notifications.py`). No existing test was weakened or deleted;
  one pre-existing test (`test_me_exposes_only_the_callers_own_identity`) was
  updated to include the new `display_name` field in the response's expected
  key set — a deliberate contract change, not a regression fix.
- `ruff check` and `ruff format --check` clean on every file this phase
  created or modified. `mypy app` — clean, 67 source files.
- **Frontend:** `npx vitest run` — 313 passed (285 pre-existing baseline + 28
  new, across 3 new test files and 2 new tests added to
  `ReportConfirmationPage.test.tsx` for the copy change). `tsc -b --noEmit`,
  `eslint .`, `prettier --check` — all clean. `npm run build` — succeeds.
- A latent gap in the test harness (`renderWithAuth`'s placeholder
  `authInstance` had no live `currentUser`, so any test exercising
  `AuthContext.refreshAccount` — which reads `auth.currentUser` directly by
  design — saw a stale `null` and appeared signed out) was found and fixed
  while writing `AccountPage.test.tsx`, the first test in this codebase to
  call a flow that uses `refreshAccount`. Fixed by giving the placeholder a
  live getter onto the same `authState` the rest of the harness already
  used.
- **Real PostgreSQL, real HTTP, real socket:** the full backend suite already
  runs against a real PostgreSQL database with the real migrations applied
  (`tests/conftest.py`'s own stated, non-negotiable design) — not sqlite, not
  mocks. Beyond that, this phase additionally started the actual Flask dev
  server (`python run.py`) against the real `campusshield` development
  database and ran a new script, `scripts/smoke_test_phase4e.py`, matching
  the established `scripts/smoke_test.py` pattern from Phase 1: real HTTP
  over a real socket, direct `psql` verification, two throwaway accounts
  created and fully deleted afterward. 26 checks, all passed — display-name
  set/persisted/rejected-for-students, malformed and unauthenticated
  requests, an empty real notification inbox, 404-not-500 on a nonexistent
  notification, and the new routes appearing in the live endpoint listing.
  The append-only `audit.access_log` rows the throwaway account's `PATCH
  /auth/me` call wrote were deliberately left in place — `trg_access_log_
  append_only` refuses to let them be deleted, which is the design working,
  not a leftover.
- The causal path "a status transition or assignment creates the right
  notification row" was verified against real PostgreSQL through the pytest
  suite (`test_notifications.py`), not through the live-server script — no
  report can be submitted against the live dev database at all right now,
  live or in a script, because zero campus locations have verified
  coordinates (`CAMPUS_LOCATIONS.md`), and a report cannot exist without one.
  This is the same, already-documented external blocker that made Phase 1's
  own `smoke_test.py` skip report submission; this phase did not work around
  it, and did not insert a synthetic location into the shared development
  database to manufacture one.

## 9. Known limitations, and what was deliberately not built

1. **No push notifications.** `notify.device_token` remains fully unwritten.
   Standing this up needs a Firebase Cloud Messaging project, service
   credentials, and — separately — an institutional decision about prompting
   students for always-on device permissions during or after a report of
   this nature. Architecture and an honest in-app-only delivery state are in
   place; nothing was implemented that pretends push works.
2. **No broadcast campus alerts.** `notify.broadcast_alert` remains fully
   unwritten. Who may issue a campus-wide advisory, under what authority, is
   an institutional-policy question this phase has no mandate to answer.
3. **No staff avatar / profile photo.** Feasible as a well-specified future
   increment (staff already have a settable name, already shown in
   assignment UI) but deliberately kept out of this phase's scope to keep it
   coherent. A **student**-facing photo was not deferred but rejected — see
   §2.
4. **No deep link from an assignment notification into one specific
   incident.** `IncidentsPage` has no URL-addressable selected case; the
   notification links to the queue instead of inventing a wrong destination.
5. **The causal notification-creation path is unverified against the live
   development server**, for the external, pre-existing reason in §8 — it is
   verified against real PostgreSQL through the pytest suite instead.
6. **Notification pagination defaults to 20 per page in the UI** (`limit`
   accepted up to 100 by the API); there is no "load more" control yet — a
   student or responder with more than 20 notifications sees only the most
   recent 20 until this is added.
7. **No mark-all-read action.** Each notification is marked read
   individually. Judged unnecessary for the volume this phase's triggers
   produce (one notification per visible transition or handoff, not a
   stream).

---

## 10. Deliberately not built

Push notifications of any kind. Broadcast campus alerts. A staff avatar or
profile photo. A student profile photo or display name — not deferred but
rejected, reversing which would undo an existing, deliberate privacy
decision. A per-incident deep link for the responder queue. A "mark all
read" action. A notification-preferences page (frequency, mute by category).
Email delivery as an alternative channel. Any change to `notify.
broadcast_alert` or `notify.device_token`. Any automatic notification not
already named in §4 — in particular, nothing here notifies anyone based on
an ML classification, a risk score, or elapsed time, matching this
codebase's standing rule that neither may drive anything a person receives.
