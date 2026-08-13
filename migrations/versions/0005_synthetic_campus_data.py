"""Synthetic (demo) campus location data, distinguished from verified (Phase 5B).

One additive column and one CHECK. Nothing else.

## The gap this closes

`coordinate_status` already distinguishes `required` (nothing surveyed),
`provisional` (a guess, treated as unsurveyed everywhere — see
`location_service.py`), and `verified` (surveyed, sourced, may become
`is_active`). It does not distinguish *why* a row is verified: a real field
survey and a synthetic demo fixture both satisfy `coordinate_status =
'verified'` identically, because both need to behave identically for the
map, corroboration, and navigation to actually work end to end. That
identical behaviour is the whole point of a demo environment — and it is
also exactly the property that could let a synthetic row be mistaken for a
real one if nothing else distinguished them.

`is_synthetic` is that distinction, orthogonal to `coordinate_status`. A row
can be `verified` *and* `is_synthetic` — fully functional for demonstration
purposes, and unambiguously flagged everywhere it is displayed or queried.

## The CHECK does the enforcing, not a convention

`ck_campus_location_synthetic_is_labelled` requires `coordinate_source` to
start with the literal string `'DEMO FIXTURE:'` whenever `is_synthetic` is
true. This is deliberately redundant with `scripts/
seed_demo_campus_locations.py` always writing exactly that prefix: the
schema is what makes it impossible to insert a `is_synthetic = true` row
that does not self-identify in its own `coordinate_source`, not merely a
script that happens to behave — the same reasoning `DATABASE_SETUP.md`
already gives for `coordinate_status`'s other CHECK constraints.

Revision ID: 0005
Revises: 0004
"""

from __future__ import annotations

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

ADD_COLUMN = """
ALTER TABLE core.campus_location
    ADD COLUMN is_synthetic BOOLEAN NOT NULL DEFAULT FALSE
"""

ADD_CHECK = """
ALTER TABLE core.campus_location
    ADD CONSTRAINT ck_campus_location_synthetic_is_labelled
    CHECK (NOT is_synthetic OR coordinate_source LIKE 'DEMO FIXTURE:%')
"""

ADD_INDEX = (
    "CREATE INDEX ix_campus_location_synthetic ON core.campus_location "
    "(is_synthetic) WHERE is_synthetic"
)

COMMENT = (
    "COMMENT ON COLUMN core.campus_location.is_synthetic IS "
    "'TRUE only for demo/development fixtures seeded by "
    "scripts/seed_demo_campus_locations.py. Never true for a real, surveyed "
    "location. The CHECK constraint ck_campus_location_synthetic_is_labelled "
    "requires coordinate_source to start with the literal string "
    "\"DEMO FIXTURE:\" whenever this is true, so a synthetic row cannot be "
    "mistaken for a verified production one even by someone reading raw SQL.'"
)


def upgrade() -> None:
    op.execute(ADD_COLUMN)
    op.execute(ADD_CHECK)
    op.execute(ADD_INDEX)
    op.execute(COMMENT)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS core.ix_campus_location_synthetic")
    op.execute(
        "ALTER TABLE core.campus_location "
        "DROP CONSTRAINT IF EXISTS ck_campus_location_synthetic_is_labelled"
    )
    op.execute("ALTER TABLE core.campus_location DROP COLUMN IF EXISTS is_synthetic")
