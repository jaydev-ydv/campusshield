# CampusShield — Demo vs. Verified Campus Data (Phase 5B)

**Status:** Implemented and verified end to end against real PostgreSQL, a
real running server, and a genuinely disposable scratch database.
**Scope:** one additive schema column (`core.campus_location.is_synthetic`)
and its CHECK constraint, a demo-data seeding script, and visible "DEMO"
UI treatment everywhere a location or destination is shown — student map,
responder map, incident detail, navigation.

This document is a direct continuation of `PHASE_5_CAMPUS_MAP.md`,
triggered by an explicit follow-on instruction: the map architecture was
real and tested, but nothing populated it, and the instruction was to make
the map experience *functional now* through clearly-labelled demo data,
never through an invented real coordinate. Everything in Phase 5's own
report stands; this document covers only what changed on top of it.

---

## 1. The instruction, and what it did and did not ask for

Explicitly forbidden throughout: inventing a real Presidency University
coordinate, or letting demo data become indistinguishable from verified
production data. Explicitly requested: a clean separation between
verified and synthetic/demo data, implemented as a small additive schema
mechanism *if genuinely necessary* — not duplicated schema capability.

Inspection (repeating Phase 5's own finding, still true) confirmed the
map/location/navigation architecture was already complete and correct.
The one genuine gap was that `coordinate_status = 'verified'` could not
distinguish a real field-survey point from a development fixture — both
had to satisfy it identically to work at all. That is the "small additive
mechanism" this phase builds: one column, not a parallel table, not a
second location concept.

## 2. `core.campus_location.is_synthetic`

Migration `0005`. One `BOOLEAN NOT NULL DEFAULT FALSE` column, one CHECK
constraint, one partial index:

```sql
CONSTRAINT ck_campus_location_synthetic_is_labelled
    CHECK (NOT is_synthetic OR coordinate_source LIKE 'DEMO FIXTURE:%')
```

The CHECK is what makes this a schema guarantee rather than a convention:
a row cannot claim `is_synthetic = true` without its own
`coordinate_source` saying so, checked by PostgreSQL on every write,
regardless of what wrote it. Verified directly — a mislabelled insert is
rejected (`scripts/verify_schema_guarantees.sql` §17,
`tests/test_import_campus_locations.py`, and the E2E script all exercise
this from a different angle).

Full design rationale: `DATABASE.md` §30.

## 3. Demo data is functional, not a placeholder

A `is_synthetic = true` row can be `coordinate_status = 'verified'` and
`is_active = true` simultaneously. It is mapped, navigable, and
corroboration-eligible exactly like a real location — that identical
behaviour is the entire point: a development environment needs a map that
actually works, not a static screenshot standing in for one. What changes
is visibility, not function.

## 4. `scripts/seed_demo_campus_locations.py`

Five fixed, hand-authored locations — library, hostel, cafeteria, gate,
sports ground — clustered a few hundred metres apart near `(0, 0)`, Null
Island, the same "obviously not Presidency University" convention
`tests/conftest.py` already established. Every name carries a `DEMO —`
prefix and a `(NOT A REAL LOCATION)` suffix in addition to the schema-level
flag, so even a plain-text rendering (a `<Select>` option, the report
review step) self-describes as non-real. Refuses a production-looking
database name; insert-only and idempotent.

Distinct from both existing scripts, deliberately:

- `scripts/seed_dev_data.py` (accounts only) still creates zero campus
  locations of any kind, real or synthetic — unchanged, its own docstring
  already explained why at length.
- `scripts/import_campus_locations.py` (Phase 5, real survey data) has no
  code path capable of setting `is_synthetic` — its CSV format does not
  include the column. Verified structurally
  (`test_the_real_data_importer_never_mentions_is_synthetic`) and
  behaviourally (a row it writes is confirmed `is_synthetic = false` by
  the column's own default).

## 5. Where the flag is exposed

`serialize_location` (`GET /locations`), `serialize_destination`
(`GET /incidents/<ref>`), and the incident queue's nested `location`
object (`GET /incidents`) all carry `is_synthetic`. Deliberately
redundant across all three surfaces — a demo location must never be
identifiable in one response and silently ordinary in another.

## 6. Frontend: visible, not just present in the payload

- **`LocationMap.tsx`** (student, step 2 of the report wizard) — a demo
  marker renders dashed and amber instead of the ordinary blue/grey,
  its tooltip gets an appended "(DEMO)", and a banner
  (`data-testid="demo-data-notice"`) appears above the map whenever any
  visible location is synthetic.
- **`IncidentMap.tsx`** (responder queue) — the same dashed-outline
  treatment, layered independently of the pre-existing emergency/selected
  colour scheme (a marker can be both emergency-coloured and
  demo-dashed), an appended "(DEMO)" tooltip, and its own banner.
- **`IncidentDetail.tsx`** — an explicit "DEMO MODE" notice in the "Where"
  section, and "(DEMO)" appended to the navigate link's label. The label
  itself still comes from `NavigationProvider`, untouched — the suffix is
  added at the display layer only, so the navigation abstraction carries
  no awareness of data provenance, matching the instruction to preserve
  it exactly.

## 7. A bug fix carried over from Phase 5, re-confirmed here

Phase 5 found and fixed a truthiness bug in `IncidentService.
destination_for` that silently reported a location at exactly `(0, 0)` as
unmapped. Phase 5B's demo fixtures sit near `(0, 0)` by design (§4), so
this phase's own test suite and E2E script exercise that exact fix again,
from a different angle, and it continues to hold.

## 8. Backend tests

- `tests/test_catalog.py` (+2) — `is_synthetic` is `false` for a real-style
  fixture and `true` for a demo one, through `GET /locations`.
- `tests/test_incidents.py` (+2) — the same distinction through
  `GET /incidents` and `GET /incidents/<ref>`, plus confirmation that a
  demo destination remains fully mapped/navigable.
- `tests/test_demo_seed_integrity.py` (new, 8 tests) — behavioural (the
  seeder creates exactly five rows, every one flagged synthetic and
  correctly sourced, active and mapped, idempotent, refuses a
  production-looking name) and structural (the real-data importer's
  source never mentions `is_synthetic`; a row it writes defaults to
  `false`).
- `scripts/verify_schema_guarantees.sql` (+1 section, 3 checks) — the
  CHECK constraint rejects a mislabelled synthetic row, accepts a
  correctly labelled one, and an ordinary verified row defaults to not
  synthetic.
- `scripts/e2e_campus_map_scenario.py` (extended) — a second, demo-flagged
  location alongside the original fixture; a report filed against it;
  confirmation the destination and queue summary both carry
  `is_synthetic: true` while remaining mapped and navigable; and a direct
  SQL attempt to insert a mislabelled synthetic row, confirmed rejected
  and absent afterward.

**513 backend tests passed** (Phase 5 ended at 501; this phase adds 12).

## 9. Frontend tests

- `report/LocationMap.test.tsx` (+2) — no demo notice when all locations
  are real; a demo notice when one is synthetic.
- `pages/ReportPage.test.tsx` (+2) — the same distinction through the full
  wizard, plus a check that the demo option's own name text is
  self-describing.
- `pages/IncidentsPage.test.tsx` (+5) — no demo notice/label for a
  real-style incident or destination; a demo notice on the map; a "DEMO
  MODE" notice in the incident detail; "(DEMO)" appended to the
  navigate-link's accessible name.

**330 frontend tests passed** (Phase 5 ended at 321; this phase adds 9).

## 10. Verification summary

- Backend: `pytest` — 513 passed. `ruff check`, `ruff format --check`,
  `mypy` — clean on every file this phase touched (the same two
  pre-existing, untouched findings from Phase 5 remain: `test_case_
  lifecycle.py` formatting, `test_exif_location.py` mypy).
- Frontend: `npx vitest run` — 330 passed. `tsc -b --noEmit`, `eslint .`,
  `prettier --check` — clean. `npm run build` — succeeds.
- Migration: `0005`, tested upgrade → downgrade → upgrade against a
  disposable scratch database before being applied anywhere else, then
  applied to the development database.
- `scripts/verify_schema_guarantees.sql` — 46/46 checks passed (43
  pre-existing + 3 new), rolled back, no data retained.
- `scripts/e2e_campus_map_scenario.py` — all checks passed against a
  fresh scratch database, dropped afterward; the shared development
  database confirmed untouched by the run (0 reports, unchanged location
  count) before and after.
- The demo seeder was run against the real development database (the
  intended, real use of this script — not a scratch/throwaway run): 5
  locations created, confirmed idempotent on re-run, confirmed served
  correctly through a live `GET /locations` call with `is_synthetic: true`
  on every row.
- **NOT VERIFIED:** a live, authenticated browser walkthrough of the
  rendered map/badges. This environment has no Firebase web configuration
  (`frontend/.env` does not exist, and none was created — inventing one
  would mean fabricating credentials), so `AuthProvider` reports
  `unavailable` and no authenticated page is reachable through a real
  browser session here. This is the same pre-existing, external
  credential gap every earlier phase has documented, not something new to
  this one. UI behaviour is instead verified through 330 component tests
  (including real Leaflet DOM rendering and a real dispatched marker
  click, in the same manner already established and trusted for
  `IncidentMap.tsx` before this phase) and through direct, unmocked API
  responses confirmed via `curl` against the real backend and real
  development database.

## 11. Real vs. synthetic, restated

Nothing about what counts as "real" changed. Verified coordinates for the
actual campus remain zero, and this phase added none. What Phase 5B adds
is a second, permanently separate, schema-enforced category — demo
data — that exists solely so a development or demonstration environment
has a working, honestly-labelled map before the field survey happens. The
real-data path (`import_campus_locations.py`) cannot produce a demo row;
the demo path (`seed_demo_campus_locations.py`) cannot produce anything
the schema would accept as verified-without-being-labelled. Confirmed
structurally and behaviourally, not merely asserted.

## 12. Known limitations

1. **Demo data is fixed and hand-authored**, not configurable — five
   locations, not a template CSV. Judged sufficient: the point is to
   demonstrate the map works, not to support arbitrary demo scenarios.
2. **No UI affordance to hide/show demo data separately from real data**
   once both exist in the same database. Not needed today (the two never
   coexist in the current development database), and would be premature
   to build against a scenario that has not arisen.
3. Every Phase 5 limitation not touched by this phase still applies
   unchanged — most importantly, zero verified *real* coordinates.

## 13. Deliberately not built

An update/promote mode for either script. A UI toggle for showing or
hiding demo data. A second, parallel `campus_location`-like table for
demo rows (the whole point was one additive column, not a duplicated
schema). Any change to `NavigationProvider`'s own interface. Any relaxing
of an existing privacy or authorization guarantee.
