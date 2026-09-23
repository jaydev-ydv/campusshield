# CampusShield — Emergency ("SOS") Reporting (Phase 6)

**Status:** Implemented, tested (backend + frontend), and verified against a
running instance of the real application.
**Scope:** one migration (one sentinel campus location, one sentinel report
category), two new endpoints, a refactor of `ReportService.submit()` into a
shared creation core, a new location-matching utility, a press-and-hold SOS
control reachable from every page, and an emergency-attention banner on the
responder queue.

This document is a companion to `BACKEND_ARCHITECTURE.md` §16 and
`RESPONDER_ARCHITECTURE.md` §3's "Emergency ("SOS") reports" subsection,
which carry the full technical detail. This document is the narrative:
what was inspected, what was reused, what was built, and why.

---

## 1. The instruction, in one line

Add a second way into the reporting system — an "EMERGENCY — GET HELP NOW"
path requiring no category, description, photo, or manual location — that
creates a persistent, server-side, admin-visible emergency event immediately,
without ever depending on the normal questionnaire, AI classification, or
completing before it can be acted on. Do this by inspecting and reusing the
existing architecture first, not by inventing a parallel system.

## 2. What inspection found already existed, unmodified

- **The entire emergency lifecycle.** `core.emergency_dispatch` and the
  `dispatch_state` enum (`pending → acknowledged → dispatched → on_scene →
  closed`, with `stood_down` reachable from any pre-arrival state) were
  built, tested, and working since migrations `0001`/`0003`. This phase
  reuses them exactly as they are — an SOS trigger creates the *report*, not
  a dispatch; a responder still consciously calls the pre-existing
  `raise_dispatch`, matching that service's own stated principle: "Always a
  human action. Nothing in this system dispatches automatically."
- **The responder queue's emergency-first ordering.** Already sorted
  `is_emergency DESC, is_ongoing DESC, submitted_at DESC`. An SOS report
  needed no new code to appear at the top of it.
- **The location-confidence vocabulary.** `LocationResolver.resolve()` and
  its four states (`corroborated`/`approximate`/`conflicting`/`unresolved`)
  already existed, and `LocationSignalSource.DEVICE_GPS` was already defined
  end to end — Python enum, Postgres type, frontend TypeScript union — since
  migration `0003`, dormant. Its own migration comment: "the value exists so
  adding it later is not a migration." This phase is that "later."
- **The evidence pipeline.** `EvidenceService.attach_all()` already accepted
  an arbitrary `report_id`, not only one being created in the same request —
  attaching evidence to an *existing* report needed a route and an
  authorization rule, not new storage plumbing.
- **The privacy boundary.** No column on `core.report` names a reporter;
  `reporter_contactable` is derived by trigger, never set directly; nothing
  about this feature touches that boundary.

## 3. The one real gap: nothing routes a categoryless report anywhere

The single finding that changed the design mid-implementation:
`IncidentRepository._visible_to()` — the query behind every responder's
queue — inner-joins `core.report_category` to find `routes_to_role`. A
report with `declared_category_id = NULL` would be invisible to every
responder, including admin. An emergency nobody can see is not an emergency
alert; it is a silently dropped one. The fix was not a new column or a
weakened join — it was accepting that an SOS trigger needs *some* category
to route through, and giving it one that says exactly what happened:
`SOS_EMERGENCY`, routed to security, seeded permanently by migration `0007`
alongside the location sentinel. See `DATABASE.md`'s Phase 6 subsection for
why each sentinel is shaped the way it is.

## 4. The confirmation-interaction decision (required before building it)

This codebase has no modal or dialog pattern anywhere. `CaseLifecyclePanel`
confirms in place, not in an overlay. A generic "Are you sure? [Cancel]
[Confirm]" dialog would have been the wrong default for two independent
reasons: it introduces a component type this codebase has never needed, and
it adds a second decision point and a small target at a moment that may have
neither time nor attention to spare for either.

**Press-and-hold** (1.5s, `SosButton.tsx`) was chosen instead: one continuous
gesture, immune to an accidental tap or a bag brushing the screen, needing no
reading (the fill and the countdown are the confirmation), and identical
across mouse, touch, and keyboard without separate code paths for each. The
codebase's own established "emergency" colour — amber, the incident queue's
existing badge — carries the button; `Button.tsx`'s reserved `danger`
variant is explicitly documented as *not* for this, because raising an
emergency is framed throughout this app as a calm, deliberate request for
help, not something to colour as alarming (`AppShell.tsx`'s wordmark
docstring makes the same argument for the whole product).

## 5. What was built

**Database** — migration `0007`: the `SYS-UNSPECIFIED` campus location and
the `SOS_EMERGENCY` report category, both permanent, both seeded so
`alembic upgrade head` guarantees their existence in every environment
rather than depending on a run-once script an operator might forget.

**Backend** — `ReportService.submit()` split into a public entry (unchanged
strictness: still requires an *active* client-supplied location) and a
shared `_create()` core; `submit_sos()` resolves its own location (a
nearest-neighbour match against verified locations via a new
`nearest_verified_location()`, or the sentinel) and calls the same core;
`category_id` widened to optional with a service-layer backstop; quota
skipped for `is_emergency=true` (fixing the same latent gap in the
pre-existing emergency checkbox on the normal form, not only the new
endpoint); a two-minute duplicate-press guard keyed on the reporter's own
most recent report. Two new routes: `POST /reports/emergency` (10/hour,
matching `/auth/register`) and `POST /reports/<ref>/evidence`
(reporter-only, narrower than `can_view_report` — no staff, no token
holder).

**Frontend** — `SosButton.tsx` (press-and-hold, mounted in `AppShell` for
every signed-in account), `lib/geolocation.ts` (`getEmergencyPosition()`,
never rejects, started the moment the hold begins so a fast fix costs no
extra latency), `EmergencyConfirmationPage.tsx` (reference, an honest
statement of whether a location resolved, optional evidence attach reusing
the existing `EvidenceUpload` component), and an attention banner on
`IncidentsPage` for unacknowledged emergencies. `ReportDetailPage.tsx` gained
the same evidence-attach capability during Phase 5 real-flow verification
below, once manual testing showed the confirmation page's one-time appearance
was not actually a durable way back to it — a reporter revisiting any of
their own open, identified reports (not only an emergency one) from "My
reports" can now add photos there too, gated on
`submission_mode !== 'anonymous'` (a token proves read access, not write —
matching `can_attach_evidence`'s own restriction) and on the case being open.

## 6. What was deliberately not built

- **Anonymous SOS.** Always identified, always contactable — the primary
  scenario (the reporter's own danger) makes reachability important in a way
  normal anonymous reporting's threat model does not share, and an anonymous
  variant needs its own answer to rate-limiting an unlinkable submitter that
  this phase did not have time to design well. Tracked as future scope.
- **A distinct emergency lifecycle or state machine.** `core.emergency_
  dispatch` reused unmodified.
- **Offline delivery.** No service worker or offline queue exists anywhere
  in this frontend. `SosButton` fails honestly and offers retry; it does not
  claim to have queued anything.
- **Contacting any external emergency service.** The alert reaches campus
  security through this application's own responder queue and nothing else.
  `EmergencyConfirmationPage`'s copy says so.
- **A second "add narrative later" endpoint.** Evidence-after-creation was
  built because the acceptance criteria required it; narrative-after-creation
  was not, and is noted as near-term future work rather than built here.

## 7. Testing

- Backend: 24 new tests (`tests/test_emergency_reports.py`) covering minimal
  creation, both location-resolution branches, duplicate-press handling,
  quota bypass, dispatch-lifecycle reuse, queue ordering, evidence-attach
  authorization (reporter/other-student/staff/anonymous-token, each
  correctly refused or allowed), and the existing anonymity/authorization
  guarantees applied to this new path — plus one new rate-limit test. Seven
  pre-existing seed-integrity tests updated to account for the two new
  permanent rows (a schema change with an intended, expected effect, not a
  regression). **Full suite: 579/579 passing.**
- Frontend: 8 new tests for `SosButton` (visibility, quick-tap
  no-op, pointer-leave cancel, full-hold success and navigation, failure
  handling, signed-out hiding, keyboard operability both ways), 6 for
  `EmergencyConfirmationPage` (fallback state, reference display, honest
  unresolved-location copy, resolved-location copy, no access-token leakage,
  evidence attach), 3 for the queue's attention banner, and 4 for
  `ReportDetailPage`'s evidence-attach addition (offered/withheld correctly
  by submission mode and case status, upload-and-attach). **353 of the
  suite's 354 tests pass; one pre-existing, unrelated failure** in
  `AuthContext.test.tsx` — caused by `frontend/.env`'s real Firebase config
  (added in an earlier, unrelated task this session) being picked up by
  Vite's test env loading, not by anything in this phase. Full detail in the
  final report's testing section.
- `ruff`, `mypy` (whole backend), `eslint`, `tsc`, `vite build` — all clean.

## 8. Real end-to-end verification

Run against the actually-running backend (real local PostgreSQL, real
Firebase Storage, real Firebase Auth — `AUTH_PROVIDER=firebase`) and
frontend, with two freshly-registered real accounts (a student and a
security responder), not against test doubles:

1. Signed in as a real student, triggered SOS via a real press-and-hold
   gesture. Confirmed in PostgreSQL: `is_emergency=true`,
   `submission_mode=identified`, `reporter_contactable=true`,
   category `SOS_EMERGENCY`, location `SYS-UNSPECIFIED` (geolocation
   permission was unavailable in this environment — the intended, honest
   fallback), `report_location_detail.resolution=unresolved`.
2. Promoted a second account to `security`, opened `/incidents` — the
   attention banner and both unacknowledged emergencies appeared, sorted
   first, exactly as designed.
3. Opened the SOS report's detail: category, location, system narrative,
   contactability, and the "Raise dispatch" action all rendered correctly.
   Raised the dispatch, then acknowledged it — the banner's count dropped
   from 2 to 1 live, and the existing dispatch UI (`Acknowledge`/`Stand
   down`/`On the way`) offered exactly the transitions the pre-existing
   state machine allows.
4. Attached a real photo to the SOS report after the fact, first via
   `EmergencyConfirmationPage`'s "Add photos" and then — after noticing the
   confirmation page is not a durable way back to that capability — via
   `ReportDetailPage` (a gap found and fixed during this same verification
   pass, see §5). The evidence row landed with `storage_backend =
   firebase_storage`: a real upload to the real bucket, not a stub.
5. Confirmed the normal reporting flow, the responder dispatch lifecycle,
   and the reporter/staff/anonymous-token evidence-attach authorization
   boundaries all continue to work via the full regression suite (§7).

One artifact from this manual pass is worth recording rather than quietly
fixing: a raw `pointerdown` dispatched via developer tools during testing
landed under the browser's already-signed-in real account rather than the
freshly-registered test account, creating one genuine (if harmless, local-
dev-only) `SOS_EMERGENCY` report against a real identity. Reported to the
user for their own cleanup decision rather than deleted unilaterally.
