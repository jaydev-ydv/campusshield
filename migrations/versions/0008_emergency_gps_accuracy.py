"""Emergency GPS accuracy support (Phase 7 - Real GPS Emergency Navigation).

Adds signal_accuracy_m to core.report_location_detail to capture device GPS
reported accuracy in metres with a non-negative constraint.

Revision ID: 0008
Revises: 0007
"""

from __future__ import annotations

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

ADD_COLUMN = """
ALTER TABLE core.report_location_detail
    ADD COLUMN signal_accuracy_m NUMERIC(8, 2);
"""

ADD_CONSTRAINT = """
ALTER TABLE core.report_location_detail
    ADD CONSTRAINT ck_location_detail_accuracy_non_negative
    CHECK (signal_accuracy_m IS NULL OR signal_accuracy_m >= 0);
"""

COLUMN_COMMENT = """
COMMENT ON COLUMN core.report_location_detail.signal_accuracy_m IS
'Reported accuracy of the device GPS or location signal in metres (95% confidence).
NULL when accuracy was not supplied or unavailable.';
"""

DROP_CONSTRAINT = """
ALTER TABLE core.report_location_detail
    DROP CONSTRAINT IF EXISTS ck_location_detail_accuracy_non_negative;
"""

DROP_COLUMN = """
ALTER TABLE core.report_location_detail
    DROP COLUMN IF EXISTS signal_accuracy_m;
"""


def upgrade() -> None:
    op.execute(ADD_COLUMN)
    op.execute(ADD_CONSTRAINT)
    op.execute(COLUMN_COMMENT)


def downgrade() -> None:
    op.execute(DROP_CONSTRAINT)
    op.execute(DROP_COLUMN)
