# CampusShield — Phase 4B Architecture

**Evidence media · exact incident location · responder navigation**

**Status: design only.** No code, schema, migration, test, or document was
modified to produce this. Everything below is a proposal for review.

Governing principle, carried into every decision here:

> CampusShield should allow a student to safely report an incident, optionally
> provide visual evidence, associate the incident with a verified campus
> location, and enable authorized responders to understand the situation and
> reach the incident location quickly — while preserving anonymity and
> minimizing unnecessary personal data exposure.

---

## A. Current architecture assessment

### The single most important finding

**The evidence *record* layer is fully built. The evidence *media* layer does not
exist at all.**

`evidence.evidence_object` exists with retention stamping and an
anonymous-filename trigger. `ReportRepository.add_evidence()` writes rows.
`POST /api/v1/reports` accepts an `evidence[]` array and persists it. All of that
is tested.

But **no byte of any file has ever been handled by this system.** There is no
upload endpoint, no Firebase Storage, no signed URL, no MIME validation, no EXIF
handling. `grep -rl evidence frontend/src` returns nothing — the frontend does
not send evidence at all.

**This creates a security defect that Phase 4B must close.** `storage_path` is
currently **client-supplied**: `POST /reports` takes whatever path the client
sends and stores it. Today that is harmless because nothing reads those paths.
The moment a signed-URL endpoint exists, a client that can name an arbitrary path
can potentially have the server sign a URL for an object it does not own. **The
server must generate storage paths. The client must never supply one.**

### What exists

| Layer | State |
|---|---|
| `evidence.evidence_object` | Built. No uploader column (deliberate). Retention stamped by trigger. Anonymous filename refused by trigger. |
| `core.campus_location` | Built, with `coordinate_status` / `coordinate_source` / `coordinate_captured_at` / `dispatch_note`. **Zero rows.** |
| `core.report` | Built. Anchors to `location_id` + free-text `location_hint`. **No coordinate columns.** |
| `core.emergency_dispatch` | Built. Full state machine `pending → acknowledged → dispatched → on_scene → stood_down → closed`. `contact_attempted` trigger-guarded. **Never written to by any endpoint.** |
| `audit.access_log` | Built, append-only, no FKs, `detail` JSONB. |
| Retention (`core.system_policy`) | Built. `evidence_retention_days = 365` (prototype default). |
| Backend API | 9 endpoints. None touch files, coordinates, or dispatch. |
| Frontend | 7-step wizard. No evidence, no GPS, no map. |
| ML | Classification / similarity / hotspots. Offline, synthetic data, **not integrated**. No image analysis of any kind. |

### What is missing

1. Every part of file handling — upload, validation, sanitisation, storage, serving.
2. Any way to record a coordinate **for a report** (as opposed to for a location).
3. Any responder-facing endpoint or interface.
4. Any writer for `emergency_dispatch`.
5. **Verified campus coordinates.** Zero. This blocks C, D and the map entirely.

---

## B. Proposed evidence architecture

### The central decision: upload **through** Flask, not direct to Storage

The obvious pattern is a signed upload URL letting the browser write straight to
Firebase Storage. **Reject it**, for one decisive reason: EXIF must be stripped
*before* the bytes land in the bucket. With direct upload, the original — carrying
GPS, device serial, and timestamps — is in Google's storage before this system
ever sees it, and a later sanitisation pass leaves the original in object version
history or in a quarantine bucket that must then be protected forever.

Routing bytes through Flask costs throughput. At campus scale, with images
capped at 10 MB, that is an acceptable price for a bucket that has never held an
unsanitised file.

```
Student device
  │ multipart POST /api/v1/evidence  (Firebase ID token)
  ▼
Flask  ── validate: magic bytes, MIME sniff, size, dimensions
       ── extract: EXIF GPS + capture time → held for the location resolver
       ── STRIP:   all metadata, re-encode
       ── hash:    sha256(original) for integrity, sha256(sanitised) for dedup
       ── generate a random storage path (server-side, never client-supplied)
  │ Admin SDK write
  ▼
Firebase Storage   (rules: allow read, write: if false — Admin SDK only)
  │
  ▼
evidence.pending_upload   ← unattached, short TTL
  │ referenced by upload_token in POST /reports
  ▼
evidence.evidence_object  ← attached to a report, retention stamped
```

### Why a staging table

Evidence is added at wizard step 5, **before** the report exists. Two ways to
handle that:

- Make `evidence_object.report_id` nullable and add an upload state. Rejected:
  every query over evidence would then have to remember to exclude orphans, and
  an abandoned upload would inherit the 365-day evidence retention.
- **A separate `evidence.pending_upload` table.** Recommended. A file uploaded
  and never submitted is not evidence — it is an orphan with a completely
  different lifecycle (delete in hours, not months) and no report to inherit
  privacy treatment from. Keeping `evidence_object` "always attached to a report"
  preserves an invariant the whole codebase currently relies on.

On submission, `POST /reports` receives `evidence_tokens[]`, the service verifies
each token belongs to the caller's session, and rows are **moved** from
`pending_upload` to `evidence_object` inside the report transaction. Unclaimed
rows are reaped on a short TTL, deleting both the row and the Storage object.

### Images first, video deferred

**Recommend images only in the first implementation.** Video is a materially
harder problem and admitting that is better than shipping it badly:

- Metadata stripping for video requires a full container rewrite, not a field edit.
- File sizes make the through-Flask path untenable (a 200 MB clip is not a
  multipart POST to a Flask worker).
- The format and codec surface is enormous, and every decoder is attack surface.
- Transcoding costs real compute this project does not have.

`content_type` already accommodates video when the time comes; nothing in the
schema blocks it. The limit is operational honesty, not the data model.

### Validation, in order

1. **Declared size** rejected above the cap before any bytes are read.
2. **Magic bytes** — the first 12 bytes must match a permitted image format.
   `Content-Type` from the client is a claim, not a fact.
3. **MIME sniff** server-side; reject on disagreement with magic bytes.
4. **Re-decode and re-encode.** This is the real malware control: a polyglot
   file or a payload in a trailing segment does not survive decode-to-pixels and
   re-encode. It also strips every metadata segment as a side effect.
5. **Dimension caps** against decompression bombs (a 100 KB PNG can expand to
   gigabytes of pixels).
6. **Per-report and per-day quotas**, reusing the `identity.submission_quota`
   pattern — a counter, never a link.

No antivirus scanner is proposed. Re-encoding is a stronger control for image
content than signature matching, and claiming AV coverage this project does not
have would be worse than not having it.

---

## C. Proposed location architecture

### The authoritative source is the controlled campus location. Always.

Five candidate sources, assessed honestly:

| Source | What it actually measures | Reliability | Privacy cost |
|---|---|---|---|
| **A. Student-selected campus location** | Where a human says it happened | High. A person chose it deliberately. | None — it is a controlled id |
| **B. Device GPS at submission** | Where the **phone** is **now** | Low-to-moderate. 5–500 m. The student may have moved, fled, or be reporting from their room hours later. | **High** — a coordinate trace of the reporter |
| **C. Photo EXIF GPS** | Where the **camera** was when the shutter fired | Moderate. Closer to the event than B. Often absent (messaging apps strip it). **Trivially forgeable.** | **High** — plus device serial and timestamps |
| **D. Verified `campus_location` coordinate** | A surveyed point | Highest, once it exists | None |
| **E. Free-text `location_hint`** | Human precision: "near the rear stairwell" | Unmappable, but often the most *useful* field for a responder | Low |

**A must remain authoritative, and this is not a stylistic preference.**
`core.report.location_id` is a foreign key that hotspot detection, cluster
centroids, `v_public_safety_map`, and every before/after impact measurement
depend on. A coordinate that overrode it would fragment all of them. B and C
**corroborate or refine** the anchor; they never replace it.

Note also that C is a *claim from the client*. A student can set any EXIF GPS
they like. Treating it as truth is the failure mode §4 of the brief warns about.

### The resolver

```
A (required)  B (optional)  C (optional)  E (optional)
      └─────────────┴──────────────┴───────────┘
                       ▼
                 Location resolver
                       ▼
    ┌──────────────┬───────────────┬──────────────┬──────────────┐
    │  corroborated│  approximate  │  conflicting │  unresolved  │
    └──────────────┴───────────────┴──────────────┴──────────────┘
```

| Status | Condition | Precise point stored? | Shown to responder as |
|---|---|---|---|
| `corroborated` | A has verified coordinates **and** B or C falls within the location's tolerance radius | Yes | "Location confirmed by device" |
| `approximate` | A has verified coordinates; no B/C, or B/C too imprecise to judge | No — the location's own coordinate is used | "Location as selected" |
| `conflicting` | B or C is present and falls **outside** tolerance | Yes, **stored but not used for navigation** | "Selected: F Block. Device reported elsewhere." |
| `unresolved` | A has no verified coordinates | No | "No mapped coordinate for this location" |

**No numeric confidence score.** The brief warns against inventing one, and it
would be false precision: there is no calibration data to fit it against. Four
named states each carry a defensible rule.

**Conflicts are surfaced, never silently resolved.** A student who fled the scene
before reporting produces a legitimate conflict, and so does a spoofed
coordinate. The system cannot tell them apart, so it shows both facts to a human
and lets them judge. Auto-picking either would be guessing while looking
confident.

**Today every report resolves to `unresolved`,** because zero campus locations
have verified coordinates. The resolver is buildable and testable now; it simply
returns the honest answer until the survey lands.

### Precision reduction by audience

| Audience | Granularity |
|---|---|
| Public map | Location **name** only, k≥3 aggregated, week-bucketed. Never a point. Already enforced by `v_public_safety_map`. |
| Authority, non-emergency | Location name + `dispatch_note`. No point. |
| Authority, **emergency or assigned** | Exact point, if resolved. Access written to `audit.access_log`. |
| Student (own report) | Location name as they chose it. |

### EXIF: extract, then destroy

Extract GPS and capture-time server-side, feed them to the resolver, then strip
**everything** and store only the sanitised image. The extracted coordinate lives
in the location plane, not in evidence metadata where a serialiser could leak it.

Device model, serial, lens, software version, and the embedded EXIF thumbnail are
**discarded entirely, never stored**. The thumbnail deserves specific mention: it
is a separate image that does not always match the visible one, and a student who
crops something out of a photo may leave it intact in the thumbnail.

---

## D. Proposed responder architecture

### Reach the **incident**, not the reporter

This distinction has to survive every layer. `reporter_contactable` already
exists on `core.report` and is `FALSE` for every anonymous report by CHECK
constraint. The responder view must render that as a **positive statement**, not
an absence:

```
┌────────────────────────────────────────────────┐
│ CS-2026-7QK4M2            ● ONGOING            │
├────────────────────────────────────────────────┤
│ Category   Following or stalking               │
│ Where      F Block — east circulation area     │
│            "near the steel bridge landing"     │
│ Access     Enter via service gate; lift to L2  │  ← campus_location.dispatch_note
│ When       Occurred 20:14 · Reported 20:19     │
│ Evidence   2 images                            │
│                                                │
│ ⚠ REPORTER CANNOT BE CONTACTED                 │
│   This report was submitted anonymously.       │
│   Go to the location. Do not attempt to        │
│   identify or contact the reporter.            │
├────────────────────────────────────────────────┤
│ [View evidence] [Navigate] [Acknowledge]       │
│ [Mark on scene] [Close]                        │
└────────────────────────────────────────────────┘
```

The action buttons map **exactly** onto the existing `dispatch_state` enum —
`pending → acknowledged → dispatched → on_scene → stood_down → closed` — and the
existing timestamp columns. `core.emergency_dispatch` needs no change; it needs a
writer.

### What a responder must never see

| Withheld | Why |
|---|---|
| Reporter identity | The guarantee. For anonymous reports there is nothing to withhold — no row exists. |
| `reporter_relationship` as a ranking signal | It records vantage point, not credibility. Already CHECK-barred from `risk_assessment`; the same rule holds in the UI. |
| Other reports, unless routed to them | `can_view_report` already enforces this. A role is not access. |
| ML internals | Scores and factors are decision support, not case material. |
| Narrative, where `requires_confidentiality` | Already enforced by `can_view_narrative`. Admin is excluded from narratives entirely. |

**The honest caveat that must be told to the student:** for an *ongoing* anonymous
emergency, a responder physically arriving may see who called. The system cannot
prevent that, and the UI should say so at submission rather than implying a
protection it cannot deliver.

---

## E. Privacy and security architecture

### The anonymity chain, extended to evidence

```
anonymous reporter → anonymous report → evidence → responder
                                                      │
                                   must NOT lead back ✗
```

Seven distinct leak paths, each closed structurally:

| Path | Closure |
|---|---|
| **EXIF GPS + timestamp** | Extracted, then stripped. Never stored on the evidence row. |
| **Device model / serial** | Discarded entirely at ingest. Never persisted. |
| **EXIF thumbnail** | Destroyed by re-encode. May contain cropped-out content. |
| **Filename** | Already refused for anonymous reports by `trg_evidence_anonymous_no_filename`. |
| **Storage path** | Random UUID, **server-generated**. Never derived from uid, email, report ref, or timestamp. |
| **Signed URL** | Short TTL, minted per authorised request, path never returned to any client. |
| **Audit log** | Records the *accessor*, never the reporter. Anonymous reports have no identity to log. |

### Image *content* is the residual risk

Metadata can be stripped mechanically. A visible face, a reflection in a window,
an ID card on a desk, or a screenshot with the student's own name in the header
cannot. **No automated control is proposed for this**, because a reliable one does
not exist and claiming otherwise would be worse than the risk.

The mitigation is honest UX at the point of upload:

> *"Check the photo before you add it. Faces, name badges, or anything in the
> background could identify you or someone else. We remove hidden location and
> device data automatically, but we cannot see what is in the picture."*

### Storage rules

```
allow read, write: if false;
```

Total denial for every client. Only the Admin SDK, which bypasses rules, may
touch the bucket. That makes the rules trivially auditable and removes an entire
class of misconfiguration.

---

## F. Database impact

| Existing structure | Supports this? | Change required | Why |
|---|---|---|---|
| `evidence.evidence_object` | **Mostly** | **Small: 3–4 columns** | Cannot record that sanitisation happened, nor distinguish the original hash from the stored hash. Needs `original_sha256`, `metadata_stripped_at`, `width`/`height`. Everything else — retention, purge, anonymous filename — already works. |
| `core.campus_location` | **Fully** | **None** | `coordinate_status`, `coordinate_source`, `coordinate_captured_at`, `dispatch_note`, `is_indoor` are all already present and were designed for exactly this. |
| `core.report` | **No** — for a precise point | **New table** | Anchors to `location_id` only. There is deliberately nowhere to put a coordinate. |
| `core.emergency_dispatch` | **Fully** | **None** | The state machine, timestamps, `acknowledged_by`, `public_note` and `contact_attempted` guard already model responder workflow completely. It needs a *writer*, not a change. |
| `audit.access_log` | **Fully** | **None** | `action` / `object_type` / `object_id` / `detail` JSONB absorb `evidence.view`, `evidence.download`, `location.precise_view` without alteration. |
| `core.system_policy` (retention) | **Fully** | **New rows only** | `evidence_retention_days` exists. Add `pending_upload_ttl_hours` as a row — that is data, not schema. |
| `ml.*` | **N/A this phase** | **None** | Image analysis is not proposed. See §K-7. |

### Three genuine gaps

**1. `core.report_location_detail` — new table.**

A report can currently only be anchored to a controlled location. Responder
navigation needs a point.

*Why not columns on `core.report`?* Because the codebase already answered this
question. `core.report_narrative` exists as a separate table for exactly one
reason: so `SELECT` can be revoked on it independently, letting the analytics
role compute every hotspot without reading a single narrative. A precise incident
coordinate deserves the same firewall — the analytics and public layers must
never see it, and putting it on `core.report` would mean every serialiser has to
remember to exclude it. Following the established pattern is both safer and
consistent.

Shape: `report_id` PK/FK, `resolution` enum, `latitude`/`longitude`, `accuracy_m`,
`source` enum (`device_gps` | `photo_exif` | `location_default`),
`captured_at`, `conflict_note`.

**2. `evidence.pending_upload` — new table.**

Evidence is uploaded before the report exists. Justified in §B: an abandoned
upload is not evidence, and giving it a row in `evidence_object` would break the
"always attached to a report" invariant and hand it a 365-day retention it should
never have.

**3. Two new enum types** for the columns above.

**Total: 2 tables, 2 enums, ~4 columns.** No existing table is modified in a way
that changes current behaviour, and no privacy structure is touched.

---

## G. Backend API impact

### Reuse

`POST /api/v1/reports` stays the single submission endpoint. Its `evidence[]`
array is **replaced** by `evidence_tokens[]` — opaque server-issued handles rather
than client-supplied storage paths. This closes the defect identified in §A.

`GET /reports/mine`, `GET /reports/<ref>`, `/locations`, `/categories`, `/auth/*`
are unchanged.

### Genuinely new

| Endpoint | Purpose | Auth |
|---|---|---|
| `POST /api/v1/evidence` | Upload one file. Validate, sanitise, store, return an opaque token. | Authenticated |
| `DELETE /api/v1/evidence/<token>` | Remove before submission ("remove" in the wizard). | Owner of the pending upload |
| `GET /api/v1/evidence/<evidence_id>/url` | Mint a short-lived signed URL. | `can_view_report` + audit write |
| `GET /api/v1/incidents` | Responder queue, filtered by routing. | Authority, via existing policy |
| `GET /api/v1/incidents/<ref>` | Responder detail incl. precise point when authorised. | Authority + audit write |
| `POST /api/v1/incidents/<ref>/dispatch` | Advance `dispatch_state`. | Authority |

Every one of these routes through the existing `routes → services → repositories`
layering. No database access in a route.

---

## H. Frontend impact

### Wizard: one new step, not a restructure

The current seven steps are well-tested. **Insert evidence as a new step 5**,
between narrative and relationship — after the student has described what
happened (so they know what would help) and before the privacy decision (so the
anonymity warning about image content lands next to the anonymity choice).

```
1 What are you reporting?
2 Where did this happen?          ← + "Use my current location"
3 When did this happen?
4 Tell us what happened
5 Add evidence (optional)         ← NEW
6 How were you involved?
7 Privacy and urgency
8 Review your report              ← + evidence thumbnails
```

`STEPS`, `StepIndex`, and `validateStep` are already indexed arrays and a switch —
adding a step is a contained change. **Evidence stays optional**; the backend
contract does not require it and nothing should imply a report without a photo
counts for less.

### Location UX

Add "Use my current location" **alongside**, never instead of, the campus
location list. Requesting geolocation must be an explicit tap with a stated
reason, never on page load.

Failure is the normal case indoors, and must be graceful:

> *"We couldn't get your location. Choose where it happened from the list."*

**The report must never silently invent a coordinate.** If GPS fails and the
student selects a location, the report is `unresolved` or `approximate` — which is
exactly what it is.

### New components

`EvidenceUpload` (camera capture on mobile via `capture` attribute, gallery
picker, per-file progress, retry, remove, client-side preview), `EvidencePreview`,
`LocationCapture`. All follow the existing `components/ui` conventions.

### Not built this phase

No responder map. The responder interface is designed here and built in 4B-4/5,
after the coordinate survey.

---

## I. Firebase Storage architecture

**One bucket, no public path.**

```
gs://<project>.appspot.com/
└── evidence/
    ├── pending/<upload_uuid>          ← TTL-reaped, never public
    └── report/<evidence_uuid>         ← flat; NOT nested under report id
```

Paths are **flat random UUIDs**. Nesting under a report reference would make one
object's path predictable from another's, and nesting under a user id would put
identity in the path — the exact leak §E closes.

**Rules: `allow read, write: if false;`** Clients never touch the bucket in either
direction. Uploads arrive through Flask; reads go through short-lived signed URLs
minted after an authorisation check.

**Signed URLs:** ~5 minute TTL, minted per request, one object each, never cached,
never logged. Every mint writes to `audit.access_log`.

A short TTL does not stop a responder forwarding a URL within the window — that
is a people problem, mitigated by audit rather than by cryptography, and it should
be stated rather than engineered around.

**Lifecycle:** a bucket rule deletes `evidence/pending/` objects after hours. The
`evidence/report/` prefix is governed by the database, not by bucket lifecycle —
`retention_expires_at` drives a purge job that deletes the object and sets
`is_purged`. Two systems deleting on different schedules is how orphans appear.

---

## J. Threat model

| # | Threat | Risk | Mitigation |
|---|---|---|---|
| 1 | Malicious file uploaded | Server/viewer compromise | Magic-byte check, MIME sniff, **decode and re-encode** (a payload does not survive it), dimension caps, size cap |
| 2 | Another person's private image uploaded | Harm to a third party | Cannot be prevented technically. Upload-time notice; authority takedown via existing purge path; `audit.access_log` records who uploaded when |
| 3 | EXIF exposes reporter identity | **Anonymity break** | Extract then strip; store sanitised only; device fields never persisted |
| 4 | Anonymous evidence carries identifying metadata | **Anonymity break** | As 3, plus filename already refused by trigger for anonymous reports |
| 5 | Unauthorised user obtains a storage URL | Evidence disclosure | Rules deny all client access; URLs signed, ~5 min, per-object |
| 6 | Responder forwards an evidence URL | Uncontrolled spread | Short TTL bounds the window; every mint audited; a people problem, stated as such |
| 7 | Report reference guessed | Enumeration | `public_ref` is random, not sequential; 404 for both "absent" and "not yours" (already implemented) |
| 8 | Storage object path guessed | Evidence disclosure | Random UUID paths; rules deny direct access regardless |
| 9 | Fake GPS/EXIF submitted | Responder sent to the wrong place | EXIF is never authoritative. Conflicts surface as `conflicting`; the human-chosen campus location governs |
| 10 | GPS inaccurate | Wrong or unusable point | Accuracy recorded; readings outside tolerance never override the anchor; `dispatch_note` carries human access guidance |
| 11 | Photo taken earlier, uploaded later | Misleading timeline | EXIF capture time recorded **separately** from `occurred_at` and `uploaded_at`; the responder sees all three |
| 12 | Evidence deleted before retention expiry | Loss of case material | Deletion is authority-only and audited; students cannot delete after submission |
| 13 | Authority browses evidence without need | Privilege abuse | Access is per-report via `can_view_report`, not blanket; every view audited; denied attempts indexed separately |
| 14 | Anonymous emergency, no contact possible | Responder cannot call back | **By design.** `reporter_contactable = FALSE` enforced by CHECK; the UI states it positively; a contact attempt is trigger-blocked |
| 15 | Emergency location inaccurate | Responder goes to the wrong place | `dispatch_note`, `location_hint`, and evidence all shown; resolution status displayed honestly |
| 16 | The image itself contains personal information | Identity disclosure | Upload-time notice; **no automated control claimed**, because none is reliable |
| 17 | Duplicate evidence wastes storage | Cost | `sha256` of the sanitised bytes deduplicates within a report |
| 18 | Public map reveals a sensitive location | Re-identification | Already closed: `v_public_safety_map` is k≥3, week-bucketed, location-level, and **never carries a point** |

---

## K. Implementation phases

The user's suggested ordering is close but inverts one thing: **the coordinate
survey is not a step among others, it is the critical path.**

| Phase | Scope | Blocked by the survey? |
|---|---|---|
| **4B-1 Evidence upload foundation** | Storage config, `POST /evidence`, validation, sanitisation, `pending_upload`, token flow, wizard step 5, signed-URL serving | **No** — fully buildable now |
| **4B-2 Location signal capture** | `report_location_detail`, resolver, EXIF extraction, "use my location" UX | **No** — buildable and testable now; returns `unresolved` until 4B-3 |
| **4B-3 Coordinate survey** | **Not a coding task.** ~40 locations, GPS at each entrance, `dispatch_note` written, `coordinate_status → verified` | **This is the blocker** |
| **4B-4 Responder incident view** | `GET /incidents`, dispatch writer, responder UI. No map yet | Partly — usable without coordinates, far more useful with them |
| **4B-5 Map + navigation** | Leaflet, incident pins, external navigation handoff | **Yes, entirely** |
| **4B-6 Hardening** | Purge job, pending reaper, rate limits, audit review, pen-test of the upload path | No |
| **4B-7 Image intelligence** | **Not recommended.** See below | — |

**4B-1 and 4B-2 should proceed in parallel with the survey**, because neither
needs it. That is the plan's most useful property: the blocker does not idle the
work.

**On 4B-7:** no image ML is proposed. There is no validated real-world image
analysis in this project, the ML phase used synthetic text only, and the deck
marks OpenCV optional with no defined job. Adding computer vision because it
sounds impressive is exactly what §13 and §14 of the brief forbid. If it is ever
revisited, the only case with a clear safety purpose is **perceptual hashing to
detect the same image submitted across multiple reports** — a duplicate-detection
signal, not a content judgement — and even that should wait for real reports.

---

## L. Testing strategy

| Layer | What it must prove |
|---|---|
| **Unit** | Magic-byte and MIME validation; EXIF extraction and stripping; resolver produces the correct status for each source combination; path generator emits no identity-derived component |
| **Database** | Anonymous filename trigger still fires; retention stamping on the new columns; `report_location_detail` cascades with the report; `pending_upload` reaper deletes both row and object |
| **API** | Upload accept/reject matrix; token flow; a token from one session cannot be claimed by another; signed URL requires `can_view_report` |
| **Privacy** (the ones that matter most) | A sanitised file contains **zero** EXIF segments; storage path contains no uid/email/ref; no response carries `user_id`; an anonymous report's evidence yields no path back to a person; audit row written for every evidence view |
| **Authorization** | Security cannot open evidence on an ICC-routed report; admin sees metadata but not narrative; a student cannot read another student's evidence |
| **Upload failure** | Network drop mid-upload; oversized file; wrong type; corrupt image; decompression bomb; duplicate; cancel; retry idempotency |
| **Location failure** | Permission denied; timeout; wildly inaccurate reading; EXIF absent; EXIF conflicting; **spoofed EXIF does not move the anchor** |
| **Emergency** | Anonymous emergency reaches the responder view with location and **no** contact route; `contact_attempted` refused; dispatch state transitions in order |
| **Browser / mobile** | iOS Safari and Android Chrome camera capture; HEIC handling; large-image memory; upload on a poor connection |
| **End-to-end responder** | Submit anonymous emergency with evidence → appears in queue → responder views → mints URL → acknowledges → marks on scene → closes. Assert throughout that no identity is exposed and every access is audited |

The existing suites (199 backend, 163 frontend, 73 ML) must stay green.

---

## M. Exact blockers

**One blocker dominates: `core.campus_location` contains zero rows and zero
verified coordinates.**

Directly blocked until the survey completes:

1. Any map — there is nothing to plot.
2. Navigation — no destination exists.
3. `corroborated` / `approximate` resolution — nothing to corroborate against.
4. Meaningful responder location display beyond a name.
5. Hotspot detection on real data.

Not blocked, and should proceed now: evidence upload end-to-end, the resolver
itself, EXIF extraction and stripping, the responder queue and dispatch workflow,
and every privacy control.

**The survey is roughly one person, one phone, three hours**, and its protocol is
already written in [CAMPUS_LOCATIONS.md §7](CAMPUS_LOCATIONS.md). It also needs
`dispatch_note` written per location — which is the single most valuable field
for the "last hundred metres" that no map solves.

Secondary blockers: a Firebase Storage bucket must be created (Blaze plan is
required for new projects, noted back in Phase 1), and a retention decision for
evidence should be confirmed rather than left at the prototype default.

---

## N. Final recommendation

One architecture, not a menu.

1. **Upload through Flask**, never direct to Storage. EXIF must be destroyed
   before bytes reach the bucket, and this is the only ordering that guarantees
   it.
2. **Images now, video later.** Say why rather than shipping video badly.
3. **Store the sanitised image only.** Keep `sha256` of the original for
   integrity. This system explicitly does not replace legal process, so it should
   not pretend to chain of custody.
4. **The controlled campus location is authoritative.** GPS and EXIF corroborate
   or conflict; they never override. Conflicts are shown to a human.
5. **Four named resolution states, no invented confidence score.**
6. **Two new tables** — `core.report_location_detail` (mirroring the
   `report_narrative` firewall) and `evidence.pending_upload` (a different
   lifecycle) — plus ~4 columns on `evidence_object`. Nothing else changes.
7. **Server-generated random storage paths**, closing the client-supplied
   `storage_path` defect that exists today.
8. **Storage rules deny all client access.** Serving is by short-lived signed URL
   after an authorisation check, always audited.
9. **Reach the incident, not the reporter.** `reporter_contactable = false`
   renders as an instruction, not an omission.
10. **Navigation is a coordinate plus `dispatch_note` handed to the device's map
    app.** No internal path graph — surveying paths is out of scope and inventing
    them is forbidden.
11. **No image ML.** Not now, and not for demonstration.
12. **Build 4B-1 and 4B-2 in parallel with the coordinate survey**, so the
    blocker does not idle the work.

### Against the five questions in §20

| | |
|---|---|
| **Student** — report in under a minute? | Yes. Evidence is one optional step, skippable. |
| **Emergency** — can security find the place? | Only after the survey. Until then the system says so honestly rather than showing a pin it cannot justify. |
| **Security** — reach the correct place? | Coordinate plus `dispatch_note` for the last hundred metres. External navigation for the rest. |
| **Privacy** — can an anonymous student stay anonymous? | Yes, for everything the system controls. Metadata is destroyed, paths carry no identity, no attribution row exists. The one honest exception — a responder physically arriving at an ongoing incident — is stated to the student rather than hidden. |
| **University** — audit without exposing people? | Yes. Every evidence view and precise-location view is an append-only audit row naming the accessor, never the reporter. |
| **Viva** — explain it without overclaiming? | Yes, and that is why this document separates what exists (the evidence *record* layer) from what does not (every byte of file handling). |

---

**Nothing in this document has been implemented.** No file under `backend/`,
`frontend/`, `migrations/`, or `ml/` was modified, and `DATABASE.md` and
`CAMPUS_LOCATIONS.md` are untouched.
