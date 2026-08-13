"""Case lifecycle: a controlled resolution vocabulary (Phase 4D).

One enum and one nullable column, plus its CHECK. Nothing else.

**No new tables.** `core.case_assignment` and `core.case_status_history` were
designed for exactly this in `0001` — one active-assignment-per-report unique
index, a role-not-student trigger on assignment, an append-only trigger on
history, and a trigger that already syncs `core.report.current_status` and
`closed_at` from every row inserted into `case_status_history`
(`trg_sync_report_status`). Phase 4D writes to both for the first time; it does
not change their shape, because their shape was already correct.

The one gap: `case_status_history.remark` is free text, and the brief for this
phase asks for a controlled vocabulary "where appropriate" for *why* a case
closed, distinct from the free-text account of *what happened*. `remark` stays
exactly what it always was — an operational account, visible to the reporter or
not per `visible_to_reporter`, exactly as designed. `resolution_reason` is new,
and answers a narrower, structured question: which of a fixed set of outcomes
applies. It is required exactly when `to_status` is terminal and forbidden
otherwise, so a transition cannot claim an outcome it has not reached and a
terminal transition cannot omit one.

Revision ID: 0004
Revises: 0003
"""

from __future__ import annotations

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


# A fixed set of outcomes rather than free text, so "why was this closed" is
# answerable by a query rather than by reading every remark ever written.  Each
# names an outcome, not a judgement about the report's merit — "no_action_
# warranted" is neutral about whether something happened, only that the process
# concluded without formal action, which is the honest, legally-safe framing for
# a system that must never claim to have determined guilt or innocence.
RESOLUTION_REASON_VALUES = [
    "action_taken",
    "no_action_warranted",
    "insufficient_information",
    "referred_elsewhere",
    "duplicate_of_existing_case",
    "withdrawn_by_reporter",
    "other",
]

TERMINAL_STATUSES = ("resolved", "closed_no_action", "duplicate", "withdrawn")

ADD_ENUM = "CREATE TYPE public.report_resolution_reason AS ENUM ({})".format(
    ", ".join(f"'{value}'" for value in RESOLUTION_REASON_VALUES)
)

ADD_COLUMN = """
ALTER TABLE core.case_status_history
    ADD COLUMN resolution_reason public.report_resolution_reason
"""

# Present if and only if the transition lands on a terminal status. A
# transition into `under_review` cannot carry a resolution — there is none yet
# — and a transition into `resolved` cannot omit one: closing a case without
# saying why is exactly the gap this migration exists to close.
ADD_CHECK = f"""
ALTER TABLE core.case_status_history
    ADD CONSTRAINT ck_case_status_resolution_reason_terminal
    CHECK (
        (to_status IN {TERMINAL_STATUSES} AND resolution_reason IS NOT NULL)
        OR
        (to_status NOT IN {TERMINAL_STATUSES} AND resolution_reason IS NULL)
    )
"""

COMMENT = (
    "COMMENT ON COLUMN core.case_status_history.resolution_reason IS "
    "'Controlled outcome vocabulary. NULL for every non-terminal transition; "
    "required for resolved/closed_no_action/duplicate/withdrawn. Distinct from "
    "remark, which is the free-text account of what happened.'"
)


def upgrade() -> None:
    op.execute(ADD_ENUM)
    op.execute(ADD_COLUMN)
    op.execute(ADD_CHECK)
    op.execute(COMMENT)


def downgrade() -> None:
    op.execute(
        "ALTER TABLE core.case_status_history "
        "DROP CONSTRAINT IF EXISTS ck_case_status_resolution_reason_terminal"
    )
    op.execute("ALTER TABLE core.case_status_history DROP COLUMN IF EXISTS resolution_reason")
    op.execute("DROP TYPE IF EXISTS public.report_resolution_reason")
