# CampusShield — Responder Architecture (Phase 4B-2)

**Status:** Implemented and verified end to end against real PostgreSQL.
**Scope:** incident location signal, responder incident map, evidence viewing,
navigation destination, and dispatch.

This document covers the half of the product that turns a report into something a
human acts on. For the schema see `DATABASE.md`; for the evidence pipeline see
`DATABASE.md` §24 and `BACKEND_ARCHITECTURE.md`. For **case status** — whether a
report has been triaged, investigated, and concluded, as distinct from the
dispatch workflow this document covers — see `CASE_LIFECYCLE.md` (Phase 4D).
For the **student-facing** counterpart to the map described in §4 below, and
for the real-campus-data importer, see `DATABASE.md` §29 and
`PHASE_5_CAMPUS_MAP.md` (Phase 5) — this document's own map/navigation
design was not changed, one destination-serialisation bug in it was.

---

## 1. The product line, end to end

```
  STUDENT                    SYSTEM                        RESPONDER
  ───────                    ──────                        ─────────
  photographs         →  validate, extract GPS,       →
  what happened          strip ALL metadata,
                         store sanitised bytes

  selects a campus    →  anchor the report at         →  sees the incident on
  location               core.report.location_id         the map, at that place

                      →  compare the photo's GPS      →  reads corroborated /
                         against that location            conflicting, and judges

  submits             →  report + evidence +          →  opens it, reads the
                         location detail, one            account, views the photo
                         transaction

                                                      →  navigates to the campus
                                                         location

                                                      →  dispatch → acknowledge
                                                         → en route → on scene
                                                         → close
```

Each arrow is a real code path with tests behind it. §7 records the end-to-end run.

---

## 2. The location trust model

### The rule

**`core.report.location_id` — the campus location the student selected — is the
operational truth. Nothing overrides it.**

Not EXIF GPS. Not browser geolocation. Not a reverse-geocoded address. Not an
estimate. Those are *corroborating signals*: they are compared against the
selection, the comparison is recorded, and a human reads the result.

This is not conservatism. `location_id` is a foreign key that hotspot detection,
cluster centroids, `v_public_safety_map` and every before/after impact
measurement depend on. A coordinate that silently overrode it would fragment all
of them — on the strength of a number a student can set to anything.

### The four states

Implemented in `backend/app/services/location_service.py`.

| State | Rule | Today |
|---|---|---|
| `corroborated` | The location is surveyed, a signal exists, and they agree within `location_corroboration_radius_m` (default 150 m). | unreachable — no location is surveyed |
| `approximate` | The location is surveyed; no signal, or none precise enough. **The ordinary case.** | unreachable |
| `conflicting` | The location is surveyed, a signal exists, and they disagree. | unreachable |
| `unresolved` | Not enough information — almost always because the location has no verified coordinate. | **every report** |

**No numeric confidence score.** There is no calibration dataset behind this
system. A number like "0.82 confident" would be invented precision presented as
measurement. Four named states each carry a rule a human can check.

**A `provisional` coordinate counts as unsurveyed.** A provisional point is one
nobody has stood at, and judging a student's photograph against a guess would
manufacture conflicts out of the survey backlog.

### Conflicts are surfaced, never resolved

A student who fled the scene and reported from their room half an hour later
produces exactly the same reading as a forged coordinate: a photo whose GPS is
some distance from the selected place. **No available signal separates them.**

So the system states both facts and stops. The note a responder sees names the
innocent explanations explicitly, and the tests assert that it never contains
"fake", "spoof", "false", or "lying". Picking an interpretation would be guessing
while looking authoritative, and the wrong guess in one direction disbelieves
someone telling the truth about being attacked.

### What EXIF GPS actually is

A number a camera wrote into a file. It is **often absent** (messaging apps strip
it — absence is normal and never suspicious), **stale** (where the camera was
when the shutter fired), **imprecise** (5–50 m outdoors, far worse indoors), and
**trivially forgeable**.

**No forensic claim is made anywhere.** `corroborated` means "the number in the
file is consistent with what the student selected". That is weak evidence of
honesty and no evidence at all of authenticity.

### Extract, then destroy

```
received bytes ──> extract GPS + capture time ──> core.report_location_detail
               │                                   (firewalled, responder-only)
               └─> sanitise (rebuild from raw     ──> storage
                   pixels: no EXIF, no XMP,
                   no PNG text, no thumbnail)
```

The coordinate lives in the *location plane*, never on the evidence row. It
stages briefly on `evidence.pending_upload` — which has no user column and is
deleted within hours by attachment or by the reaper — and moves to
`core.report_location_detail` in the same transaction that creates the evidence.

---

## 3. Responder flow

`GET /incidents` → `GET /incidents/<ref>` → view evidence → navigate →
`POST /incidents/<ref>/dispatch` → `POST .../dispatch/state`.

As of Phase 4D, `GET /incidents/<ref>` also carries the case's own status
history and assignment, and three more endpoints
(`POST .../status`, `.../assign`, `.../unassign`) manage them. That is a
**separate state machine from dispatch** — see `CASE_LIFECYCLE.md` — and
nothing below in this section changed to accommodate it.

### The queue

Ordered by **emergency, then ongoing, then most recent**. That ordering comes
entirely from the incident. It is deliberately blind to `reporter_relationship`
(vantage point, not credibility), to who reported it, and to how many reports
they have filed before. A queue that sorted on the reporter would be a
credibility score without a name.

Scoped by routing: an ICC member sees reports routed to the ICC, security sees
reports routed to security. **A role is not access** — the same rule
`can_view_report` applies to a single report, expressed as a query so the queue
cannot show something the detail endpoint would then refuse.

### Emergency ("SOS") reports (Phase 6)

A report a student raised through the SOS trigger (`POST /reports/emergency`)
is not a different kind of thing in this queue — it is `is_emergency = true`
under the fixed `SOS_EMERGENCY` category, which routes to **security**, so it
sorts first by the existing rule above with no new code in this file's own
query. What is new:

- **The attention banner.** `IncidentsPage` shows a calm, factual banner —
  "N emergency reports have not been acknowledged yet" — when the queue holds
  one or more emergencies with no dispatch yet, or a dispatch still in
  `pending`. It disappears the moment a responder raises and acknowledges the
  dispatch; an emergency already being handled does not re-alarm the page.
  Deliberately not styled in alarm colours — see `SosButton.tsx`'s own
  reasoning for why this app treats an emergency as a calm, deliberate
  request for help rather than something to colour as risky.
- **Category may say "Emergency SOS."** Where every other report's category
  reflects the student's own classification, an SOS report's category is a
  system default assigned because a responder queue has to route *somewhere*
  and the trigger had no time to classify itself. A responder can correct it
  with the existing `override_category` the same as any other report.
- **Location may say "Unspecified location."** When the reporter's browser
  gave no usable position and nothing could be matched to a verified place,
  the report anchors to a permanent sentinel location
  (`core.campus_location.code = 'SYS-UNSPECIFIED'`), which is unmapped by
  construction — `destination.is_mapped` is `false` for it, exactly like any
  other unsurveyed location, so no fake pin ever appears. `IncidentDetailPanel`
  already renders this correctly with no new frontend logic: "This location
  has no verified coordinate yet."

### Dispatch

`core.emergency_dispatch` from migration `0001`, unchanged. It needed a writer,
not a migration. The states are the existing `dispatch_state` enum:

```
pending ──> acknowledged ──> dispatched ──> on_scene ──> closed
   │             │                │
   └─────────────┴────────────────┴────> stood_down
```

`stood_down` is terminal and is **not** a step towards `closed`: a dispatch that
was stood down never reached a scene, and merging the two would lose that.

Each state owns exactly one timestamp column, written server-side, so the record
of when a responder arrived cannot disagree with the state saying they did.

Two rules the database already enforced and the service now surfaces as stated
reasons rather than constraint violations:

- **Only an emergency report can be dispatched** (`trg_dispatch_requires_emergency`).
- **A contact attempt cannot be recorded against an uncontactable reporter**
  (`trg_dispatch_contact_honours_flag`).

**Nothing dispatches automatically.** Not on an emergency flag, not on a
category, not on any score. A human decides to go.

---

## 4. Map and navigation

### The map is not the interface — the list is

The incident list carries every field the map does. It works on a phone, works
with a screen reader, works for a location nobody has surveyed, and works today.
The map is a second view of the same data.

With zero verified coordinates the map renders an honest empty state:

> Campus locations have not yet been verified. Incident mapping will become
> available after the campus location survey is completed.

**No invented pins, no campus centroid, no fake routing, no fake hotspots.** A
map with plausible-looking pins would be worse than no map, because a responder
would trust it.

### Navigation

`NavigationProvider` (`frontend/src/responder/navigation.ts`) is a two-method
interface with two implementations — `geo:` for a phone, OpenStreetMap for a
control-room desktop. A university with its own indoor wayfinding system replaces
one class.

**The destination is always the selected campus location.** Never a photograph's
GPS, never the reporter's device position — navigating to one would send a
responder to wherever a picture happened to be taken.

When there is no verified coordinate the navigation action is disabled and the
responder gets text instead: location name, zone, indoor flag, `dispatch_note`,
and the reporter's own `location_hint`. **The last hundred metres are a door, not
a pin** — "enter via the service gate, lift to level 2" beats a coordinate on a
building outline.

### Operational map vs. hotspot analytics

Kept separate, deliberately. The incident map answers *"what is happening and
where should I go now?"*. Hotspot analytics answers *"what pattern is emerging
over time?"*. No risk score was invented to make the map look intelligent.

---

## 5. Privacy model

| Guarantee | How |
|---|---|
| Anonymous reports have no attribution | No row in `identity.report_attribution`. Verified in the database, not the response body. |
| No responder query can reach a reporter | `IncidentRepository` never joins `report_attribution` — there is no join to forget to remove. |
| Reporter identity never serialised | Every responder field is named explicitly in `responses.py`. No `dump()` anywhere. |
| `acknowledged_by` recorded, never returned | Which colleague took a call is internal; emitting it would put a user id in a response. |
| The reporter's coordinate is never shown | The responder gets a resolution, a distance and a note — not the point. |
| No storage path, filename, or URL | Evidence is an opaque id resolving only through an authorised stream. |
| Evidence stays sanitised | The responder path serves the same stored object; re-verified end to end. |
| Analytics cannot read precise location | `core.report_location_detail` is a separate table with its own grant, exactly as `report_narrative` is. |
| Denied access is audited | Responder routes write a `denied` audit row and re-raise. A probe is the access most worth recording. |

### The limitation that is stated, not hidden

An anonymous report protects the reporter's identity **in this system**. A
responder physically arriving may see who is present. The system cannot prevent
that, and the responder interface says so rather than implying a protection it
cannot deliver.

---

## 6. Student experience

Unchanged by this phase. A student sees their reference, their selected location,
and confirmation that evidence was received. They never see storage paths,
Firebase, EXIF, database ids, dispatch ids, or internal scores — and are not told
whether a responder has viewed the report, because no existing product policy
supports that.

**Phase 4D** gave this its first real content: `STATUS_LABELS` covered all
eight statuses from the start, but nothing except `submitted` was ever
reachable until case status transitions existed to write the others. A
student's report now moves through a visible timeline — see
`CASE_LIFECYCLE.md` §6.

---

## 7. Verification

### End to end, scratch database, real HTTP — 22/22 checks

Report created with a GPS-bearing photograph → evidence stored → responder queue
→ incident opened → **served image had 0 EXIF tags and no GPS where the original
had 5 tags and a GPS IFD** → destination is the controlled location with its
dispatch note → dispatch raised, acknowledged, en route, on scene, closed →
backwards transition refused → every unauthorised path refused (student queue
403, student detail 403, student dispatch 403, unrelated student evidence 404,
wrong-role responder 404, unauthenticated 401/404) → audit rows for every
sensitive action, with no narrative, coordinate, or path in them → **development
database untouched: 0 reports, 0 campus locations**.

A nearby photo GPS resolved `corroborated` (22 m) and a far-away one
`conflicting` (8 051 km). **Neither moved the incident** — both destinations
remained the selected campus location.

The scratch database was dropped afterwards.

### Suites

- Backend: **326 tests**, real PostgreSQL, real migrations, real Pillow
- Frontend: **221 tests**
- Migration `0003`: upgrade → downgrade → upgrade → base, zero residue
- `ruff`, `ruff format`, `mypy` (55 files), `eslint`, `prettier`, `tsc`, `vite build` — all clean

---

## 8. Known limitations

1. **Zero verified *real* campus coordinates.** Every report against an
   un-surveyed location resolves `unresolved`, and the map shows its empty
   state until either a real location is loaded or a demo one is. `CAMPUS_
   LOCATIONS.md` records the survey as outstanding. `scripts/seed_dev_
   data.py` (accounts) still creates **0 campus locations**, real or
   synthetic, and always will — see its own docstring. Phase 5 built the
   importer that will load the real survey once it exists (`DATABASE.md`
   §29); Phase 5B separately built an *opt-in* demo seeder
   (`scripts/seed_demo_campus_locations.py`, §9 below) so the map has
   something real-behaving, clearly-flagged, and non-real to show before
   that survey happens.

2. **Firebase Storage is still unverified against a real bucket.** Phase 4F
   fixed a genuine defect (`_build_storage_provider` never reused the
   Firebase Admin app auth had already initialised, which would have made
   the first real upload fail) and exercised the real `FirebaseStorageProvider`
   class and a real, uncredentialed `firebase_admin` SDK — see
   `PHASE_4F_FIREBASE_STORAGE.md`. What remains true: no Firebase credentials
   have existed in any environment this project has run in, so no byte has
   ever actually reached a real Google-hosted bucket.

3. **EXIF cannot establish photographic authenticity.** It is forgeable, often
   absent, and frequently stale. `corroborated` is weak evidence of honesty and
   none of authenticity.

4. **Visible content is not anonymised.** Faces, name badges, number plates,
   documents and reflections survive sanitisation, because they are pixels. No
   automated control is proposed; a reliable one does not exist.

5. **Navigation precision depends on survey quality.** A coordinate on a building
   outline plus a good `dispatch_note` beats a precise coordinate with none.
   Corroboration radius (150 m) is a prototype default, not calibrated.

6. **A responder arriving may identify an anonymous reporter.** Physical, not
   digital, and outside what software can prevent.

7. **Reaping and purging still have no scheduler.** Phase 4F gave both a real
   entry point — `backend/scripts/reap_and_purge_evidence.py`, calling
   `reap_expired()` (abandoned uploads) and the new `purge_expired()`
   (attached evidence past retention) — but nothing invokes that script
   periodically. A production deployment needs an external trigger (cron,
   a platform's own scheduled-job feature); see `PRODUCTION_READINESS.md`.

8. **Leaflet tiles come from the public OSM server.** Fine for a prototype; a
   deployment should use its own tile source or a licensed provider.

9. **(Fixed in Phase 5) A destination at exactly `(0, 0)` used to serialise
   as unmapped.** `IncidentService.destination_for` tested
   `location.latitude` for truthiness after already confirming it was not
   `None`; `0.0` is falsy in Python. Every verified coordinate this project's
   own tests have used until now happened to be non-zero, so nothing caught
   it — `DATABASE.md` §29.1 has the full account. Listed here because it was
   a real defect in the architecture this document otherwise describes as
   complete, not swept into a changelog elsewhere.

10. **SOS while offline is not guaranteed, and this project does not claim
    otherwise.** No service worker, manifest, or offline-queueing exists
    anywhere in the frontend (Phase 6 confirmed this by grep before building
    anything). If the device has no connectivity at the moment the hold
    completes, `POST /reports/emergency` fails like any other request and
    `SosButton` shows an inline retry — it does not pretend to have queued
    the alert for later delivery.

11. **Anonymous SOS does not exist.** `ReportService.submit_sos()` always
    creates an identified, contactable report. Building it safely needs a
    real answer to rate-limiting an unlinkable submitter that this phase did
    not have time to design well — see the method's own docstring and
    `BACKEND_ARCHITECTURE.md` §16.7.

12. **No external emergency service is contacted.** An SOS trigger alerts
    campus security through this application's own responder queue — the
    infrastructure that genuinely exists — and nothing else. It does not call
    police, an ambulance, or a parent, and the frontend copy on
    `EmergencyConfirmationPage` says so explicitly rather than implying
    otherwise.

---

## 9. Demo/development campus data (Phase 5B)

`core.campus_location.is_synthetic` (`DATABASE.md` §30) distinguishes a
demo/development fixture from real, surveyed data. A synthetic row can be
`coordinate_status = 'verified'` and `is_active = true` at the same time —
it works exactly like a real one for map rendering, navigation, and
corroboration — so the map genuinely functions in a development
environment before the field survey exists. What changes is presentation,
not behaviour:

- **The responder incident map** (`IncidentMap.tsx`) draws a demo
  incident's marker with a dashed outline — layered independently of the
  existing emergency/selected colour scheme, so a marker can be both — and
  appends "(DEMO)" to its tooltip. A banner appears above the map whenever
  any visible incident is anchored to a demo location.
- **The incident detail view** (`IncidentDetail.tsx`) shows an explicit
  "DEMO MODE" notice in the "Where" section for a synthetic destination,
  and appends "(DEMO)" to the navigation link's own label — the label
  `NavigationProvider` computes is left untouched; the suffix is added at
  the display layer only, so the navigation abstraction itself carries no
  knowledge of data provenance.
- **The student location map** (`frontend/src/report/LocationMap.tsx`) —
  see `frontend/README.md` — does the equivalent on the reporting side.

Seeded by `scripts/seed_demo_campus_locations.py`, which refuses to run
against a database whose name suggests production and is idempotent
(re-running skips codes already present). Nothing here relaxes the rule in
§2: `core.report.location_id` remains the operational truth regardless of
whether the location behind it is real or a demo fixture.
