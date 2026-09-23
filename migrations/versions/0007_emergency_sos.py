"""Emergency/SOS: sentinel campus_location row and category (Phase 6).

## The gap this closes

`core.report.location_id` is `NOT NULL`, and today zero rows in
`core.campus_location` carry a verified coordinate — the emergency ("SOS")
flow being added in this phase must still succeed when a student's browser
gives no usable position and nothing can be matched to a surveyed place. The
alternative — making `location_id` nullable — would ripple through hotspot
detection, `IncidentService.destination_for()`, `v_public_safety_map`, and
every frontend assumption that an incident has a location; a single sentinel
row is the same trick this project already uses for demo fixtures
(`0005_synthetic_campus_data`), applied to a different, permanent purpose.

A second, related gap: `IncidentRepository._visible_to()` — the query behind
every responder's queue — `INNER JOIN`s `core.report_category` on
`declared_category_id` to find `routes_to_role`. A report with no category
would be invisible to every responder, including admin, which would defeat
the entire point of an emergency alert reaching someone. An SOS trigger has no
time to classify itself, so this migration also seeds one permanent category
for it to route through.

## What these rows are, and are not

**`core.campus_location`**, one row, `code = 'SYS-UNSPECIFIED'`,
`is_active = FALSE`, `coordinate_status = 'required'` (the default — no
coordinate, honestly unsurveyed). `is_active = FALSE` means
`LocationRepository.list_active()` never offers it as something a student can
pick from a dropdown or map — it is reachable only by the emergency
submission path setting `location_id` directly. Because it is never
`verified`, `LocationResolver.resolve()` already treats it exactly like any
other unsurveyed location: `UNRESOLVED`, with its existing "no verified
coordinate yet" note. No new resolution state, no new code path in the
resolver.

**`core.report_category`**, one row, `code = 'SOS_EMERGENCY'`,
`routes_to_role = 'security'` — campus security is the appropriate first
responder for the scenarios this feature exists for (a physical attack, an
abduction, being followed or trapped), and a responder can still redirect a
misrouted case with the existing `IncidentService.override_category`. Unlike
the location sentinel, this row is `is_active = TRUE`: it needs to satisfy
`CategoryRepository.get_active()`, the same lookup the normal report flow
uses, and there is no harm in a student seeing "Emergency SOS" alongside the
other categories in `GET /categories` — it is an honest description of what
happened, not a placeholder. `emergency_eligible = TRUE` and
`kind = 'incident'` together satisfy `ck_report_category_emergency_is_incident`.

Both are seeded here, in a migration, rather than by a run-once script,
because the emergency path has a hard runtime dependency on both rows
existing — an operator forgetting to run a seed script would silently break
SOS in production. `alembic upgrade head` is already a mandatory deployment
step, so this guarantees both rows exist in every migrated environment, the
same guarantee `core.system_policy`'s seeded rows already rely on.

Revision ID: 0007
Revises: 0006
"""

from __future__ import annotations

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

SENTINEL_LOCATION_CODE = "SYS-UNSPECIFIED"
SENTINEL_CATEGORY_CODE = "SOS_EMERGENCY"

INSERT_SENTINEL_LOCATION = f"""
INSERT INTO core.campus_location (code, name, zone_id, location_type, is_active)
VALUES (
    '{SENTINEL_LOCATION_CODE}',
    'Unspecified location (emergency alert — location not available)',
    NULL,
    NULL,
    FALSE
)
"""

LOCATION_COMMENT = f"""
COMMENT ON COLUMN core.campus_location.code IS
'Unique location code. The row with code = ''{SENTINEL_LOCATION_CODE}'' is a
permanent system sentinel, not a real place: it is the location_id an
emergency ("SOS") report is given when the reporter''s device supplied no
usable position and nothing could be matched to a surveyed campus location.
It is never is_active and must never be offered to a student as a selectable
location.'
"""

INSERT_SENTINEL_CATEGORY = f"""
INSERT INTO core.report_category
    (code, label, kind, routes_to_role, base_severity,
     requires_confidentiality, emergency_eligible, is_active)
VALUES (
    '{SENTINEL_CATEGORY_CODE}',
    'Emergency SOS',
    'incident',
    'security',
    5,
    FALSE,
    TRUE,
    TRUE
)
"""

CATEGORY_COMMENT = f"""
COMMENT ON COLUMN core.report_category.code IS
'Unique category code. The row with code = ''{SENTINEL_CATEGORY_CODE}'' is the
category every emergency ("SOS") report is created with, since the trigger has
no time to classify itself. Routes to security by default; a responder may
redirect a misrouted case with IncidentService.override_category the same as
any other report.'
"""


def upgrade() -> None:
    op.execute(INSERT_SENTINEL_LOCATION)
    op.execute(LOCATION_COMMENT)
    op.execute(INSERT_SENTINEL_CATEGORY)
    op.execute(CATEGORY_COMMENT)


def downgrade() -> None:
    op.execute(f"DELETE FROM core.report_category WHERE code = '{SENTINEL_CATEGORY_CODE}'")
    op.execute(f"DELETE FROM core.campus_location WHERE code = '{SENTINEL_LOCATION_CODE}'")
