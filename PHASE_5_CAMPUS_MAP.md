# CampusShield — Campus Map, Location & Navigation (Phase 5)

**Status:** Implemented and verified end to end against real PostgreSQL, a
real running server, and a genuinely disposable scratch database.
**Scope:** the student-facing interactive campus map, a validated real-data
import mechanism, one destination-serialisation correctness fix this
phase's own testing found, and a scratch-database end-to-end scenario
exercising the full report → evidence → corroboration → responder map →
navigation → case-response story over real HTTP.

This document covers what a full-system inspection found before any code
was written (§1), what was actually built (§2–§4), what was deliberately
left as it already was (§5), and how all of it was verified (§6–§8). For the
underlying schema and privacy design, unchanged by this phase, see
`DATABASE.md` §7, §16, §25, and the new §29. For the responder-side map and
navigation design, also unchanged, see `RESPONDER_ARCHITECTURE.md`.

---

## 1. What inspection found, before any code was written

The instruction that opened this phase said, correctly, that the campus map
is a core MVP requirement and must not be deferred. Inspecting the actual
code — not assuming the prior phase's documentation was current — found
that **most of the map, location, and navigation architecture already
existed, was already tested, and was already correct**:

| Layer | State found |
|---|---|
| `core.campus_location` / `core.campus_zone` schema, coordinate verification invariants | Built (migration `0001`), unchanged since Phase 1 design |
| `core.report_location_detail`, the four-state corroboration model | Built (migration `0003`, Phase 4B-2) |
| EXIF extraction, extract-then-destroy ordering | Built and tested (`app/utils/exif_location.py`) |
| `LocationResolver` (corroborated/approximate/conflicting/unresolved) | Built and tested (`app/services/location_service.py`) |
| `ReportService.submit` wiring location_id + EXIF signal + resolver in one transaction | Built and tested |
| `IncidentService.destination_for`, the responder's map/navigation target | Built — **and, as this phase's own testing found, carrying one real bug (§3)** |
| Responder incident map (`IncidentMap.tsx`, Leaflet) | Built and tested, honest empty state |
| `NavigationProvider` abstraction (`geo:` mobile, OSM desktop, text fallback) | Built and tested |
| **Student-facing map for choosing a location** | **Missing.** `StepLocation` was a plain `<Select>`. |
| **A mechanism to load real survey data** | **Missing.** Only one-row-at-a-time SQL, documented in `DATABASE_SETUP.md` §8. |

The two missing items above are exactly what this phase built. Nothing
else in the table needed rebuilding, and nothing was redesigned for
stylistic reasons — the existing corroboration model, navigation
abstraction, and privacy boundaries are sound and were left as they were.

## 2. The student campus map

`frontend/src/report/LocationMap.tsx` — a Leaflet map for step 2 of the
reporting wizard ("where did this happen?"), alongside the `<Select>` that
was already there. Deliberately not shared code with
`responder/IncidentMap.tsx`: same library, same imperative approach, for
the same reasons the responder map uses them, but a different component,
because a multi-marker read-heavy responder view and a single-choice
picker a student taps once are different things that should be free to
change independently.

**The list is not a fallback for the map.** `<Select>` remains fully
functional on its own — a student can complete the entire form without
touching the map — because it is what a screen reader reaches, what works
on the slowest phone, and what keeps working if a tile server does not
answer. The map is the second way in, not a replacement for the first.
Clicking or keyboard-activating a marker and choosing from the dropdown
both write to the same `draft.locationId`, so the two are always in sync
by construction, not by one observing the other.

No coordinate is fabricated at any point in this path. `GET /locations`
returns only `is_active` locations, and `is_active` cannot be `true`
without `coordinate_status = 'verified'` — a schema invariant, not a
frontend assumption. `LocationMap` additionally filters defensively for
any location missing a coordinate, so even a violation of that invariant
upstream would degrade to "marker not drawn," never a fabricated pin.

Details: `frontend/README.md`, "The campus map on step 2 (Phase 5)".

## 3. A real bug this phase's testing found and fixed

`IncidentService.destination_for` computed the responder's map/navigation
target as:

```python
latitude=float(location.latitude) if mapped and location.latitude else None,
```

`mapped` already established `location.latitude is not None`. The second
`and location.latitude` tested *truthiness*, and a coordinate of exactly
`0.0` — the equator, a legitimate value, and also the value this project's
own synthetic test fixtures use throughout (`tests/conftest.py`'s
`locations["active"]`, and the fixture this phase's own scratch-database
scenario seeds) — is falsy in Python. The bug silently reported a
genuinely `verified`, `is_active` location as unmapped: a response
carrying `is_mapped: true` and `latitude: null` in the same object.

No existing test caught it, because none asserted `destination.latitude`
for a verified fixture at exactly `(0, 0)`. This phase's own scratch-
database E2E script (§7) hit it on its first run — a real, previously
unverified interaction, exactly the kind of thing an actual end-to-end
pass over real HTTP finds and a unit test in isolation does not.

Fixed to `is not None`. Regression test added:
`tests/test_incidents.py::test_a_verified_location_at_exactly_zero_is_still_mapped`.
Full account: `DATABASE.md` §29.1.

## 4. Real campus-data import mechanism

`backend/scripts/import_campus_locations.py` — a validated, repeatable,
insert-only CSV loader for `core.campus_zone` and `core.campus_location`.

**What it validates**, each rule mirroring an existing database CHECK
constraint so a bad file fails with a row number before any write, not an
opaque `IntegrityError` partway through:

- required fields (`code`, `name`) present and non-blank
- `code` unique within the file (a code already in the database is
  skipped and reported, not an error — see "repeatable" below)
- `location_type` / `footfall_band` restricted to the same fixed
  vocabularies the schema enforces
- `zone_code` resolves to a real zone or the run is rejected
- latitude/longitude both present or both absent, each numerically in
  range
- `coordinate_status = required` carries no coordinate;
  `coordinate_status = verified` carries a source and a capture time with
  an explicit UTC offset (no guessed timezone)
- `is_active = true` only alongside `coordinate_status = verified`
- two rows with the identical coordinate, or the identical name, within
  one file are rejected as ambiguous duplicates — the second check exists
  because `CAMPUS_LOCATIONS.md` §1 already documents exactly this failure
  mode (`CAFE-MAIN` vs `CANTEEN-MAIN`, possibly the same place twice)

**Insert-only, and never silently promotes.** A code already in the
database is skipped, never updated — this script bulk-*loads*; promoting
one location from staged to verified stays the deliberate, one-row SQL
`UPDATE` already documented in `DATABASE_SETUP.md` §8, because that is a
"someone stood here and confirmed it" act performed once per place, not a
batch operation. Re-running the same file is therefore safe (idempotent by
skipping, not by magic) — verified directly (§8).

**Dry run by default.** Without `--commit`, the script validates and
reports what it would do and writes nothing.

Template and full field-by-field documentation, including exactly what
real survey data this project still needs and why none of it can be
filled in from a desk: `backend/scripts/templates/README.md`. Both
example rows in the shipped template are explicitly, unambiguously
synthetic — one staged placeholder, one verified row at Null Island
(`0, 0`) with `coordinate_source = 'SYNTHETIC EXAMPLE — not a real field
survey'`.

## 5. Deliberately left as it was

Per the phase's own hard scope boundary, and because inspection found
these already correct:

- The responder incident map, `NavigationProvider`, and the operational-
  map-vs-hotspot-analytics separation (`RESPONDER_ARCHITECTURE.md` §4) —
  unchanged.
- The four-state corroboration model and its "never resolve a conflict
  automatically" principle (`location_service.py`) — unchanged.
- The extract-then-destroy EXIF pipeline — unchanged.
- The `core.report.location_id` trust model (student selection is
  authoritative; nothing overrides it) — unchanged, and re-verified by
  this phase's own E2E scenario rather than merely re-asserted.
- No new endpoint. No new field on any existing endpoint. Both the
  student map and the importer consume/populate the existing schema and
  API surface exactly as they already were.

## 6. Backend test additions

- `tests/test_incidents.py`:
  `test_a_nearby_signal_corroborates_the_selected_location` — the
  resolver's four states are named in `location_service.py`, but before
  this phase only `approximate` (no signal) and `conflicting` (a signal
  thousands of kilometres away) had integration coverage.
  `corroborated` — a signal that genuinely agrees — did not.
  `test_a_verified_location_at_exactly_zero_is_still_mapped` — the
  regression test for §3.
- `tests/test_import_campus_locations.py` (new, 33 tests) — every
  validation rule in §4, plus real-database write tests (a commit lands
  the exact row given, a dry run writes nothing, a second run skips what
  is already there, an unresolvable zone reference aborts before any
  write, a zoned location resolves its zone correctly).

## 7. Frontend test additions

- `src/report/LocationMap.test.tsx` (new, 5 tests) — renders nothing when
  no location has a coordinate; renders the map when one does; ignores an
  unmapped location alongside mapped ones without failing; calls
  `onSelect` when a marker is clicked (a real DOM `click` dispatched at
  Leaflet's own rendered SVG path, not a mock); cleans up on unmount.
- `src/pages/ReportPage.test.tsx` (3 new tests) — the map appears
  alongside the list once locations carry a coordinate and the list stays
  usable; no map renders when no location has one, and the list still
  works; no map when the campus has no published locations at all.

## 8. The scratch-database end-to-end scenario

`backend/scripts/e2e_campus_map_scenario.py` — a new, self-contained
script distinct in kind from the pytest suite. It creates a throwaway
database (`campusshield_e2e_map_scratch`), migrates it to head, seeds
exactly one synthetic verified zone/location/category and four accounts,
starts a real Flask server against it on its own port, drives the entire
story over real HTTP, verifies with direct SQL, then stops the server and
drops the database — every step, whether the run passed or failed.

What it exercises, in one continuous run: an identified report with a
nearby (corroborating) photo; the served evidence image confirmed to
carry zero EXIF/GPS tags where the original had a real GPS IFD; the
resolution confirmed `corroborated` via SQL; a second report with a
deliberately distant photo, confirmed `conflicting`; both reports'
`core.report.location_id` confirmed unchanged by either photo; a
responder opening the real incident queue and incident detail; the
destination confirmed mapped, navigable, and equal to the surveyed
coordinate — explicitly **not** equal to either photo's EXIF coordinate;
the same evidence opened again in the responder's authorised context; a
case triaged from the incident view; a student refused the responder
queue and incident detail; a wrong-role responder refused a specific
incident; an unauthenticated caller refused; an anonymous report
confirmed to carry no attribution row and to be unreachable by the
submitting account's own identity; and audit rows confirmed written for
the sensitive actions above with no coordinate or narrative text leaked
into them.

**Result: 39/39 checks passed.** It caught the bug in §3 on its first run.
The scratch database was confirmed dropped afterward; the shared
development database was confirmed untouched (`0` reports, `0` campus
locations, before and after).

Distinct from `backend/scripts/smoke_test.py` (Phase 1) and
`smoke_test_phase4e.py` (Phase 4E), which both point at the *shared*
development database and skip whatever depends on a location that does
not exist there yet. This script needs no such skip, because it seeds and
then destroys its own.

## 9. Real vs. synthetic, stated plainly

**Real, unchanged by this phase:** the schema, every privacy guarantee,
the corroboration model's rules, the navigation abstraction, the
responder map. **Synthetic, and labelled as such everywhere it appears:**
every coordinate this phase's own tests, fixtures, template CSV, and E2E
script use — `0.0, 0.0` throughout, matching the convention
`tests/conftest.py` already established, never a value that could be
mistaken for a real campus location. **Zero real campus coordinates were
added anywhere** — not to the development database, not to any seed
script, not to any template. `core.campus_location` in the development
database remains, and must remain, empty until `CAMPUS_LOCATIONS.md`'s
field survey happens.

## 10. Verification summary

- Backend: `pytest` — **501 passed** (Phase 4E ended at 466; this phase adds
  35 — 33 in the new `test_import_campus_locations.py` plus 2 in
  `test_incidents.py`, §6). `ruff check`, `ruff format --check`, `mypy`
  (69 source files) — clean on every file this phase touched. One
  pre-existing, unrelated `mypy` finding in `tests/test_exif_location.py`
  (a file this phase did not modify) was left as found.
- Frontend: `npx vitest run` — **321 passed** across 20 files. `tsc -b
  --noEmit`, `eslint .`, `prettier --check` — clean. `npm run build` —
  succeeds.
- Migration state: **unchanged.** Still at `0004` (head). No migration was
  created — every table and column this phase uses already existed.
  There is therefore no upgrade/downgrade cycle to report for this phase.
- Scratch-database E2E: 39/39 checks passed (§8).
- Importer: exercised against both the real development database
  (dry-run only, confirmed zero writes) and a separate, disposable
  scratch database (`--commit`, confirmed correct writes, confirmed
  idempotent on re-run, confirmed dropped afterward). Eleven distinct
  invalid-CSV scenarios each produced the correct rejection with no
  partial write.

## 11. Known limitations

1. **Zero verified campus coordinates**, unchanged and unchangeable by
   this phase — see `CAMPUS_LOCATIONS.md`. The student map, the responder
   map, and the importer are all real and all tested; none of them has
   real data to show yet, and none of them fabricates any.
2. **The importer has no update mode.** Promoting a staged location to
   verified is a deliberate one-row `UPDATE`
   (`DATABASE_SETUP.md` §8), not a bulk operation — see §4's reasoning.
   If the eventual survey workflow turns out to need bulk promotion too,
   that is a follow-up decision, not an oversight here.
3. **The map's "centre on the incident" behaviour is unchanged from
   Phase 4B-2**: `IncidentMap` fits bounds to every visible incident and
   emphasises the selected one, rather than hard-centring on a single
   incident. This was judged adequate and was not touched, per the scope
   boundary against redesigning completed architecture.
4. **No indoor wayfinding.** Never proposed; `NavigationProvider` hands
   off to an external map application or presents text directions, both
   pre-existing and both unchanged.
5. **Firebase Storage remains unverified against a real bucket** — unrelated
   to this phase. Phase 4F (after this one) fixed a real wiring defect and
   exercised the provider against a real, uncredentialed Firebase Admin SDK;
   no real Google-hosted bucket has been reached in any environment this
   project has run in, for lack of credentials — see
   `PHASE_4F_FIREBASE_STORAGE.md`.

## 12. Deliberately not built

A public incident map. Predictive policing or any location-based risk
scoring (`core.risk_assessment`'s existing `ck_risk_factors_no_
credibility_terms` and this phase's own additions carry no such thing).
Facial recognition or any person-identification feature. Automatic
dispatch from a location or corroboration signal. Video, audio, or PDF
evidence. Turn-by-turn indoor navigation. An importer update/promote
mode. A public map layer change of any kind (`v_public_safety_map` was
not touched). Any redesign of the corroboration model, the navigation
abstraction, or the responder map beyond the one correctness fix in §3.
