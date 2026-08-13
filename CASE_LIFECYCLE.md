# CampusShield — Case Lifecycle (Phase 4D)

**Status:** Implemented and verified end to end against real PostgreSQL.
**Scope:** case status transitions, case assignment, the reporter's own status
timeline, and the responder queue actually reflecting all of it.

This document covers the gap the previous phase's inspection found: the system
could receive, triage-suggest, corroborate location, and dispatch an emergency
response — but nothing ever moved a report's institutional status past
`submitted`, so a responder queue never emptied and a student's own status page
read "Received" forever. For evidence and location see `RESPONDER_ARCHITECTURE.md`
and `DATABASE.md` §24-25; for the ML layer this phase deliberately does not touch,
see `ML_INTEGRATION.md`.

---

## 1. The gap, precisely

Before this phase:

- `core.report.current_status` defaulted to `submitted` and never changed.
- `core.case_status_history` received exactly one row, at submission
  (`ReportRepository.record_initial_status`), and no endpoint added a second.
- `core.case_assignment` existed, fully specified since migration `0001`
  (partial unique index, role-check trigger, indexes for "my queue"), and was
  never written.
- `IncidentRepository`'s queue query already excluded `resolved`,
  `closed_no_action`, `duplicate`, `withdrawn` — the filter was correct from the
  start. It simply had nothing to filter, because nothing ever produced those
  statuses.

**No migration was required for the core lifecycle.** The schema was already
correct — the gap was a missing writer, the same shape of gap 4B-1 and 4B-2
closed for evidence and dispatch. One additive column was needed
(`resolution_reason`, see §3) because the brief asked for a controlled outcome
vocabulary distinct from the free-text `remark` that already existed.

---

## 2. Case status vs. dispatch status — two state machines

**Related. Not the same. Never coupled.**

| | Case status | Dispatch status |
|---|---|---|
| Column | `core.report.current_status` | `core.emergency_dispatch.state` |
| States | `submitted → triaged → under_review → action_taken → resolved`, plus `closed_no_action` / `duplicate` / `withdrawn` | `pending → acknowledged → dispatched → on_scene → closed`, plus `stood_down` |
| Question it answers | Has this report been looked at, investigated, and concluded? | Did a responder physically go, and what happened when they did? |
| Exists for | Every report | Only reports raised as an emergency |
| Owner | `CaseService` | `IncidentService` |

Neither reads or writes the other. A case can reach `resolved` with no dispatch
ever raised — most reports are not emergencies. A dispatch can be `closed`
while the case is still `under_review` — arriving and helping someone is not
the same act as concluding the institutional process, which may involve
follow-up the responder on scene has no part in. The brief was explicit that
closing one must not be allowed to silently imply closing the other, and
nothing in this implementation does: there is no code path from
`advance_dispatch` into `CaseService`, or back.

---

## 3. The state machine

```
submitted ──▶ triaged ──▶ under_review ──┬──▶ action_taken ──▶ resolved
    │             │             │        └──────────────────────▲
    ├─▶ withdrawn ┤             ├─▶ resolved
    ├─▶ duplicate ┤             ├─▶ closed_no_action
    └─▶ closed_no_action        ├─▶ withdrawn
                                 └─▶ duplicate
```

Enforced once, server-side, in `CaseService.CASE_TRANSITIONS`. The frontend
holds a courtesy copy (`CASE_TRANSITIONS` in `lib/api.ts`) to decide which
buttons to draw; it is never the authority, and every one of the checks below
runs again on the server regardless of what the frontend sent.

| Rule | Detail |
|---|---|
| **Assignment gate** | `under_review`, `action_taken`, `resolved` each require an *active* `core.case_assignment` row at the moment of the transition. A first acknowledgement (`submitted → triaged`) and the early exits (`closed_no_action` / `duplicate` / `withdrawn`, reachable directly from `submitted` or `triaged`) do not — a case can be dismissed or merged before anyone has taken it on. |
| **Remark gate** | Every transition beyond the first acknowledgement requires a `remark`. Only `submitted → triaged` may carry nothing. |
| **Resolution reason gate** | Every terminal transition requires a `resolution_reason` from a seven-value controlled vocabulary; every non-terminal transition forbids one. `RESOLUTION_REASONS_BY_STATUS` further restricts which of the seven fit which terminal status — `duplicate` cannot close with reason `withdrawn_by_reporter`. |
| **Terminal is terminal** | `resolved`, `closed_no_action`, `duplicate`, `withdrawn` have no legal successor. Reopening is not a feature this phase built; it would need its own authorisation story. |

### The resolution vocabulary

`action_taken`, `no_action_warranted`, `insufficient_information`,
`referred_elsewhere`, `duplicate_of_existing_case`, `withdrawn_by_reporter`,
`other`. Each names an *outcome*, deliberately not a judgement.
`no_action_warranted` says a process concluded without formal action — it does
not assert that nothing happened, only that this system's process did not
result in one. A reporting tool that appeared to rule on the truth of an
allegation would overstep in a way that damages trust in both directions, and
the vocabulary was chosen to avoid that.

### Releasing an assignment does not roll a case backward

A case unassigned mid-investigation stays exactly where it is — `under_review`,
say — and simply becomes ineligible for further forward transitions until
claimed again. That is a handover, not an error, and the queue reflects it: the
case reappears filterable as unassigned rather than disappearing or resetting.

---

## 4. Assignment

`core.case_assignment`, first written in this phase. `CaseService.assign`:

- **No body → self-assign.** The common action in a shared queue: a responder
  claims the case in front of them. This is the only assignment action the
  frontend currently exposes a control for.
- **`assignee_user_id` → assign a named colleague.** The backend supports this
  fully — validated against the report's routing role before it can reach the
  database's own assignment-target-role trigger — but the frontend does not yet
  offer a colleague picker, because there is no responder-directory endpoint to
  populate one from. See §7.
- **Reassignment** releases the active row and creates a new one inside the
  same request. The partial unique index (`WHERE is_active`) guarantees at most
  one active owner regardless; the service does not rely on the constraint to
  catch what it can prevent directly.
- **Unassignment** releases the active row. The case's status is untouched.

Every identity surfaced by assignment — assignee, who assigned them, who
changed a status — is a role plus `identity.app_user.display_name`, never a
bare `user_id`. This is a deliberate departure from `serialize_dispatch`'s
`acknowledged_by`, which stays hidden outright: a case's ownership is exactly
the coordination mechanism a small team needs visible, where dispatch's "who
answered the phone" was judged unnecessary to expose at all.

---

## 5. Authorization: `can_manage_case`

Narrower than `can_view_report`, in `app/security/authorization.py`:

```python
def can_manage_case(principal, ctx) -> bool:
    if principal is None: return False
    if principal.role is UserRole.ADMIN: return False
    if ctx.assigned_to_user_id == principal.user_id: return True
    return principal.role is ctx.routes_to_role
```

Three exclusions worth naming:

- **An anonymous reporter's own access token is not enough.** Holding the
  token proves "this is my report" (and admits `GET /reports/<ref>`); it does
  not admit any responder-plane write. Verified end to end (§8, check F1).
- **Administration is excluded**, for the same reason it is excluded from
  `can_view_narrative`: oversight of patterns is not the same authority as
  operating on one case.
- **A student is never admitted**, implicitly — the two remaining conditions
  (current assignee, routed role) can never be true for a student: assignment
  to a student is trigger-refused at the database level regardless of what the
  service does, and no category ever routes to `student`.

Every refusal — illegal transition, wrong role, no assignment where required,
malformed resolution reason — returns a stated reason (`ValidationError` 400 or
`ConflictError` 409) rather than a silent no-op, and every attempt is audited
via the same `_audited` context manager the responder plane has used since
4B-2, extended with three new action names: `case.status_change`,
`case.assign`, `case.unassign`.

---

## 6. What a responder sees; what a reporter sees

**Responder** (`GET /incidents/<ref>`, extended): the full, unfiltered
`case_status_history` and the current `assignment`. Bundled into the existing
detail response rather than a new endpoint — `IncidentService.detail` had
already run authorisation once, and `CaseService`'s read methods trust that
rather than repeating it.

**Reporter** (`GET /reports/<ref>`, unchanged endpoint, now actually
exercised): `status_history` filtered to `visible_to_reporter = true` — the
same filter `ReportRepository.visible_status_history` has applied since Phase
1. `resolution_reason` rides along on the same terms as `remark` already did.
A responder writing a transition chooses per-transition whether it is
reporter-visible (default: yes), which is how an ICC officer keeps an internal
handling note ("escalating to police liaison, awaiting availability")
separate from what the student sees ("Your report is under investigation"),
without a second table.

**`ReportDetailPage.tsx`** (new): the first thing in the frontend to actually
read `status_history`. `MyReportsPage` stays a lightweight scan — current
status, submitted date, via the existing `STATUS_LABELS` mapping, which was
already complete for all eight statuses and simply had nothing but
`submitted` to render before this phase. Each row now links to the detail
page for the full timeline.

---

## 7. Queue behaviour

`IncidentRepository`'s exclusion of closed statuses (`CLOSED_STATUSES`) needed
no change — it was correct from `4B-2` onward and simply had nothing to filter
until this phase gave reports somewhere else to go. Verified directly: a case
taken to any of the four terminal statuses disappears from `GET /incidents` on
the very next read.

**New:** an optional `?status=` filter, narrowing within the open set (never
past it — requesting a closed status explicitly returns nothing). The frontend
renders it as filter chips for the four *reachable-while-open* statuses
(`submitted`, `triaged`, `under_review`, `action_taken`); the four terminal
ones are not offered as chips because they can never appear in an open queue.

**Unchanged, re-verified:** ordering is emergency, then ongoing, then recency —
never anything about the reporter, and never a function of case status either.
An `is_assigned` boolean rides on every queue row (cheap `EXISTS` check,
mirroring `ReportRepository._active_assignee`'s existing pattern) so a
responder can see ownership at a glance without opening each incident; the
assignee's actual identity is a detail-view question.

---

## 8. Verification

### End to end, scratch database, real HTTP — 21/21 checks

Scenario A (identified report, full path: acknowledged → assigned →
investigating → action taken → resolved) confirmed `core.report.current_status`
and `closed_at` were set by `trg_sync_report_status` — the database trigger,
never application code — and that five history rows exist (the initial
`submitted` plus four transitions). Scenario B (`closed_no_action` directly
from `triaged`), C (`duplicate` directly from `submitted`), and D (`withdrawn`
directly from `submitted`) each confirmed the early-exit paths need no
assignment.

Scenario E: an **anonymous, ongoing emergency** taken through the full
responder workflow — acknowledged, assigned, investigated, dispatched, and
resolved — with **zero** `identity.report_attribution` rows at every step, the
reporter's own token-based status view showing the resolution with no identity
present, and the responder's own case view carrying no bare user id for the
assignee either.

Scenario F: an unauthorised student attempting status change, assign, unassign,
and reading the queue, plus a wrong-role responder attempting a status change —
**five for five rejected** (403/404) — with **three denied attempts
independently confirmed in `audit.access_log`**.

Database invariants checked directly rather than assumed: at most one active
assignment per report across every case created in the run; zero stored risk
factors mentioning reporter credibility or relationship; the development
database untouched (0 reports, 0 case assignments) after the scratch database
was dropped.

### Suites

- Backend: **437 tests** (was 377; +60 new — `test_case_lifecycle.py`), real
  PostgreSQL, real migrations
- Frontend: **285 tests** (was 240; +45 new — `CaseLifecyclePanel.test.tsx`,
  `ReportDetailPage.test.tsx`, plus extensions to `IncidentsPage.test.tsx`)
- Migration `0004`: upgrade → downgrade → upgrade → base, zero residue
- `ruff`, `ruff format`, `mypy` (63 files), `eslint`, `prettier`, `tsc`,
  `vite build` — all clean

---

## 9. Known limitations

1. **No colleague picker in the frontend.** `CaseService.assign` fully
   supports assigning to a named `assignee_user_id`, validated against the
   report's routing role, and is tested at the API level. The UI exposes only
   "Assign to me" and "Release assignment," because there is no
   responder-directory endpoint to populate a picker from, and building one
   was judged out of scope for a case-management phase — it is an
   organisational-directory feature, not a lifecycle one. The realistic
   small-team flow (release, then the next responder claims it) is fully
   supported.

2. **No reopening.** A terminal case cannot be moved again through this
   system. Reopening needs its own authorisation story — who may reopen, under
   what circumstances, with what record left behind — which this phase was not
   asked to design.

3. **A responder's operational notes ride on status transitions.**
   There is no free-standing "add a note without changing status" action.
   `case_status_history.remark`, gated by `visible_to_reporter`, is what the
   schema already provided for this — its own design note says "without a
   second table" — and this phase followed that rather than inventing a
   parallel notes table. A responder logging incremental investigative
   progress does so at the next genuine transition, not between them.

4. **Resolution reasons are outcome categories, not narratives.** They
   deliberately cannot capture case-specific nuance — that is what `remark`
   is for, on the same transition.

5. **No email or push notification accompanies a status change.**
   `notify.*` remains unwired, as it has been since Phase 1 — a student learns
   of a status change only by returning to `MyReportsPage` /
   `ReportDetailPage`.

6. **The quota system was exercised, not bypassed, during verification.**
   `identity.submission_quota`'s 5-reports-per-day limit is enforced correctly
   and the end-to-end script accounts for it (two synthetic student accounts)
   rather than disabling it — worth stating because it is exactly the kind of
   thing a less careful verification would have quietly worked around.

---

## 10. Deliberately not built

Reopening a closed case. A free-standing case-notes table. A
responder-directory endpoint. Automatic status transitions from any signal —
dispatch closure, ML classification, risk score, or elapsed time. Any coupling
between case status and dispatch status. Reporter credibility or risk scoring
of any kind (unaffected by this phase; `core.risk_assessment`'s
`ck_risk_factors_no_credibility_terms` and `CaseService`'s own signature — no
principal reaches the state machine's rules — both hold exactly as before).
Bulk case operations. SLA timers or overdue-case alerting. A public-facing case
status page beyond what `GET /reports/<ref>` already served.
