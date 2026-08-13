"""Response serializers.

**No ORM object is ever returned to a client.**  Every response is built by a
function here that names each field explicitly, so adding a column to a model can
never widen an API response by accident — which is exactly how identity leaks
happen: someone adds a field, a ``dump`` picks it up, and nobody notices until it
is in production.

The rule that follows from that: there is no ``user_id`` anywhere in this file,
in any shape, for any role.  Nor is there ``storage_path``, which is an internal
bucket location, or a raw evidence pointer.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..models import CampusLocation, Report, ReportCategory
from ..models.enums import CoordinateStatus
from ..services.case_service import AssignmentView
from ..services.health_service import DatabaseHealth
from ..services.incident_service import Destination
from ..services.report_service import ReportDetail, SubmissionResult


def _decimal(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def serialize_principal(principal, *, created: bool | None = None) -> dict[str, Any]:
    """The caller's own identity.

    ``user_id`` appears here and nowhere else in this module. It is the caller's
    own id, returned only to the caller, and the client needs a stable handle for
    itself. It still never appears on a report, in a list, or in anything another
    user can fetch.
    """
    body: dict[str, Any] = {
        "user_id": str(principal.user_id),
        "email": principal.email,
        "role": principal.role.value,
        "is_active": principal.is_active,
        # Always null for a student — `ck_app_user_student_has_no_name` means
        # there is nothing here to leak. Present and editable for authority
        # roles via `PATCH /auth/me`.
        "display_name": principal.display_name,
    }
    if created is not None:
        body["created"] = created
    return body


def serialize_location(location: CampusLocation) -> dict[str, Any]:
    return {
        "location_id": location.location_id,
        "code": location.code,
        "name": location.name,
        "location_type": location.location_type,
        "zone": (
            {
                "zone_id": location.zone.zone_id,
                "code": location.zone.code,
                "name": location.zone.name,
            }
            if location.zone
            else None
        ),
        "latitude": _decimal(location.latitude),
        "longitude": _decimal(location.longitude),
        "is_indoor": location.is_indoor,
        "dispatch_note": location.dispatch_note,
        # Demo/development fixture, never a real surveyed location — see
        # scripts/seed_demo_campus_locations.py. Exposed deliberately, to
        # every caller, so a demo location can never quietly look real.
        "is_synthetic": location.is_synthetic,
        # has_lighting / has_cctv are risk-scoring inputs and stay server-side.
        # Publishing "no camera here, unlit after dark" as an open endpoint would
        # be a map of where not to be seen.
    }


def serialize_category(category: ReportCategory) -> dict[str, Any]:
    return {
        "category_id": category.category_id,
        "code": category.code,
        "label": category.label,
        "kind": category.kind.value,
        "emergency_eligible": category.emergency_eligible,
        "requires_confidentiality": category.requires_confidentiality,
        # routes_to_role and base_severity are internal routing and scoring
        # inputs. A student choosing a category should not be choosing an
        # audience, or be able to infer how seriously each option is weighted.
    }


def serialize_report_summary(report: Report) -> dict[str, Any]:
    """Report metadata, safe for the reporter and for authority list views."""
    return {
        "public_ref": report.public_ref,
        "report_kind": report.report_kind.value,
        "submission_mode": report.submission_mode.value,
        "reporter_relationship": report.reporter_relationship.value,
        "category": (
            {
                "category_id": report.category.category_id,
                "code": report.category.code,
                "label": report.category.label,
            }
            if report.category
            else None
        ),
        "location": {
            "location_id": report.location.location_id,
            "code": report.location.code,
            "name": report.location.name,
        },
        "location_hint": report.location_hint,
        "occurred_at": _iso(report.occurred_at),
        "submitted_at": _iso(report.submitted_at),
        "is_emergency": report.is_emergency,
        "is_ongoing": report.is_ongoing,
        "reporter_contactable": report.reporter_contactable,
        "status": report.current_status.value,
    }


def serialize_report_detail(detail: ReportDetail) -> dict[str, Any]:
    body = serialize_report_summary(detail.report)
    body["narrative"] = detail.narrative
    body["narrative_available"] = detail.narrative_available
    if detail.narrative_withheld_reason:
        # Said plainly rather than by omitting the key.  "purged" in particular
        # is a fact the reporter is entitled to: their account reached its
        # retention limit and no longer exists.
        body["narrative_withheld_reason"] = detail.narrative_withheld_reason
    body["evidence_count"] = detail.evidence_count
    body["status_history"] = [
        {
            "status": row.to_status.value,
            "changed_at": _iso(row.changed_at),
            "remark": row.remark,
            # A controlled vocabulary, not free text — safe to disclose on the
            # same terms as `remark` already is (this list is pre-filtered to
            # `visible_to_reporter` rows in `ReportRepository.visible_status_history`).
            "resolution_reason": row.resolution_reason.value if row.resolution_reason else None,
        }
        for row in detail.status_history
    ]
    return body


def serialize_submission(result: SubmissionResult) -> dict[str, Any]:
    body = serialize_report_summary(result.report)
    if result.access_token:
        body["access_token"] = result.access_token
        body["access_token_notice"] = (
            "Save this code now. It is the only way to check this report's status, "
            "it is shown once, and it cannot be recovered — the report is not linked "
            "to your account. You will not receive notifications about it."
        )
    return body


def serialize_database_health(health: DatabaseHealth) -> dict[str, Any]:
    body: dict[str, Any] = {
        "connected": health.connected,
        "ready": health.ready,
    }
    if health.connected:
        body["server_version"] = health.server_version
        body["migration_revision"] = health.migration_revision
        body["schemas_present"] = health.schemas_present
        if health.schemas_missing:
            body["schemas_missing"] = health.schemas_missing
    if health.error:
        body["error"] = health.error
    return body


def paginated(
    items: list[dict[str, Any]], *, total: int, limit: int, offset: int
) -> dict[str, Any]:
    return {
        "items": items,
        "pagination": {
            "total": total,
            "limit": limit,
            "offset": offset,
            "returned": len(items),
        },
    }


# ---------------------------------------------------------------------------
# Responder plane
#
# Everything below is read by security and ICC staff. The same rule applies as
# above and matters more here: every field is named explicitly, and the fields
# that are absent are absent on purpose.
#
# Never serialised, in any responder response: reporter identity in any form,
# `storage_path`, `original_filename`, raw EXIF, or the reporter's device
# position. Evidence appears as an opaque id that resolves only through
# `GET /evidence/<id>`, which re-authorises on every request.
# ---------------------------------------------------------------------------


def serialize_destination(destination: Destination) -> dict[str, Any]:
    """Where a responder is being sent.

    `latitude`/`longitude` are null for every location until the campus survey
    lands. `is_mapped` says so explicitly rather than leaving a client to infer
    it from two nulls, and `dispatch_note` carries the access instructions that
    make the destination usable in the meantime.
    """
    return {
        "location_id": destination.location_id,
        "code": destination.code,
        "name": destination.name,
        "latitude": destination.latitude,
        "longitude": destination.longitude,
        "is_mapped": destination.is_mapped,
        "navigable": destination.navigable,
        "location_type": destination.location_type,
        "is_indoor": destination.is_indoor,
        "dispatch_note": destination.dispatch_note,
        "zone_name": destination.zone_name,
        "location_hint": destination.location_hint,
        # Demo/development fixture, never a real surveyed destination.
        "is_synthetic": destination.is_synthetic,
    }


def serialize_location_signal(detail) -> dict[str, Any] | None:
    """How a corroborating signal compared with the selected campus location.

    The coordinate the signal carried is **not** included. A responder needs to
    know that a photograph's location data disagrees and by roughly how far —
    which the note and the distance give them — not the coordinate itself, which
    is the reporter's position and has no operational use.
    """
    if detail is None:
        return None
    return {
        "resolution": detail.resolution.value,
        "source": detail.source.value,
        "distance_m": detail.distance_m,
        "signal_captured_at": _iso(detail.signal_captured_at),
        "note": detail.conflict_note,
    }


def serialize_dispatch(dispatch) -> dict[str, Any] | None:
    """The responder engagement.

    `acknowledged_by` is deliberately absent: which colleague took a call is
    internal, and the id would be a user id in a response — the one thing this
    module has no business emitting.
    """
    if dispatch is None:
        return None
    return {
        "dispatch_id": str(dispatch.dispatch_id),
        "state": dispatch.state.value,
        "raised_at": _iso(dispatch.raised_at),
        "acknowledged_at": _iso(dispatch.acknowledged_at),
        "dispatched_at": _iso(dispatch.dispatched_at),
        "on_scene_at": _iso(dispatch.on_scene_at),
        "closed_at": _iso(dispatch.closed_at),
        "responder_note": dispatch.responder_note,
    }


def serialize_case_status_entry(entry) -> dict[str, Any]:
    """One row of case history, for a responder.

    `changed_by` never appears as a bare id — `_responder_label` resolves it to
    a role and, where set, a display name, the same treatment given to
    assignment. Unlike `serialize_dispatch`'s deliberately hidden
    `acknowledged_by`, a case's history is a team record: colleagues
    coordinating a shared queue need to see who moved a case and when, not just
    that it moved.

    `resolution_reason` is `None` on every non-terminal row — the controlled
    vocabulary only ever applies to how a case ended.
    """
    return {
        "history_id": entry.history_id,
        "from_status": entry.from_status.value if entry.from_status else None,
        "to_status": entry.to_status.value,
        "changed_at": _iso(entry.changed_at),
        "remark": entry.remark,
        "visible_to_reporter": entry.visible_to_reporter,
        "resolution_reason": entry.resolution_reason.value if entry.resolution_reason else None,
    }


def serialize_assignment(view: AssignmentView | None) -> dict[str, Any] | None:
    """Who currently owns a case.

    `assignee`/`assigned_by` carry a role and a display-name-or-role label —
    never a `user_id`. That is enough for one teammate to recognise another on
    a shared queue, and it is the same ceiling every other responder-facing
    serialiser in this file holds to.
    """
    if view is None:
        return None
    assignment = view.assignment
    return {
        "assignment_id": assignment.assignment_id,
        "assigned_at": _iso(assignment.assigned_at),
        "assignee": {"role": view.assignee.role.value, "label": view.assignee.label},
        "assigned_by": (
            {"role": view.assigned_by.role.value, "label": view.assigned_by.label}
            if view.assigned_by
            else None
        ),
        "note": assignment.assignment_note,
    }


def serialize_incident_summary(row) -> dict[str, Any]:
    """One incident in the responder queue.

    `reporter_contactable` is carried as a positive statement rather than left
    to be inferred. For an anonymous report it is false by CHECK constraint, and
    a responder needs to know before they set off that there is nobody to call —
    not discover it when a phone number turns out to be missing.
    """
    report = row.report
    return {
        "public_ref": report.public_ref,
        "report_kind": report.report_kind.value,
        "category": (
            {
                "category_id": row.category.category_id,
                "code": row.category.code,
                "label": row.category.label,
            }
            if row.category
            else None
        ),
        "location": {
            "location_id": row.location.location_id,
            "code": row.location.code,
            "name": row.location.name,
            "latitude": (
                float(row.location.latitude)
                if row.location.coordinate_status is CoordinateStatus.VERIFIED
                and row.location.latitude is not None
                else None
            ),
            "longitude": (
                float(row.location.longitude)
                if row.location.coordinate_status is CoordinateStatus.VERIFIED
                and row.location.longitude is not None
                else None
            ),
            "is_mapped": (
                row.location.coordinate_status is CoordinateStatus.VERIFIED
                and row.location.latitude is not None
            ),
            # Demo/development fixture, never a real surveyed location.
            "is_synthetic": row.location.is_synthetic,
        },
        "location_hint": report.location_hint,
        "occurred_at": _iso(report.occurred_at),
        "submitted_at": _iso(report.submitted_at),
        "is_emergency": report.is_emergency,
        "is_ongoing": report.is_ongoing,
        "status": report.current_status.value,
        "reporter_contactable": report.reporter_contactable,
        "submission_mode": report.submission_mode.value,
        "evidence_count": row.evidence_count,
        "location_signal": serialize_location_signal(row.location_detail),
        "dispatch": serialize_dispatch(row.dispatch),
        # A bare boolean at queue scale — enough to filter and to render "no
        # owner yet" without a name. `GET /incidents/<ref>` carries who.
        "is_assigned": row.is_assigned,
    }


def serialize_incident_detail(detail) -> dict[str, Any]:
    body = serialize_incident_summary(detail.row)
    body["destination"] = serialize_destination(detail.destination)
    body["narrative"] = detail.narrative
    body["narrative_available"] = detail.narrative_available
    if detail.narrative_withheld_reason:
        body["narrative_withheld_reason"] = detail.narrative_withheld_reason
    # Opaque ids. Each resolves only through GET /evidence/<id>, which streams
    # the bytes after re-checking authorisation — there is no URL here, signed
    # or otherwise, and no path.
    body["evidence"] = [{"evidence_id": evidence_id} for evidence_id in detail.evidence_ids]
    body["triage"] = serialize_triage(detail.triage)
    # Case lifecycle — distinct from `dispatch` above. `case_status_history` is
    # the full, unfiltered responder view; the reporter's own view of the same
    # report (`GET /reports/<ref>`) is filtered to `visible_to_reporter` by
    # `serialize_report_detail`, and the two are deliberately different sizes.
    body["case_status_history"] = [
        serialize_case_status_entry(entry) for entry in detail.status_history
    ]
    body["assignment"] = serialize_assignment(detail.assignment)
    body["reporter_contact_guidance"] = (
        "This report was submitted anonymously. There is no reporter to contact, "
        "and no identity is held in this system. Go to the location; do not attempt "
        "to work out who reported it."
        if detail.row.report.submission_mode.value == "anonymous"
        else "The reporter consented to contact about this report."
        if detail.row.report.reporter_contactable
        else "The reporter did not consent to being contacted about this report."
    )
    return body


def serialize_triage(triage) -> dict[str, Any] | None:
    """Machine assistance, framed as what it is.

    Three deliberate choices about what this does and does not carry.

    `model_trained_on_real_data` is always emitted, never inferred from silence.
    Every model this project can currently produce is trained on synthetic text,
    and a suggestion from one must not reach a responder looking like a finding
    from a system trained on real reports.

    `label_scores` — the full probability distribution — is **not** serialised.
    A responder deciding whether to trust a suggestion needs the confidence and
    the alternative, not sixteen numbers that invite reading tea leaves. The
    distribution stays in the database for evaluation.

    `risk_factors` **is** serialised, in full. A number that orders a queue must
    be answerable for, and "why is this 65?" should have an answer on the screen
    rather than in a database someone would have to query.
    """
    if triage is None:
        return None
    return {
        "suggested_category": (
            {
                "category_id": triage.suggested_category.category_id,
                "code": triage.suggested_category.code,
                "label": triage.suggested_category.label,
            }
            if triage.suggested_category
            else None
        ),
        "confidence": triage.suggested_confidence,
        "agrees_with_reporter": triage.declared_matches_suggestion,
        "overridden_category": (
            {
                "category_id": triage.overridden_category.category_id,
                "code": triage.overridden_category.code,
                "label": triage.overridden_category.label,
            }
            if triage.overridden_category
            else None
        ),
        "model": triage.model_name,
        "model_trained_on_real_data": triage.model_is_real_world_trained,
        "risk_score": triage.risk_score,
        "risk_band": triage.risk_band,
        "risk_factors": triage.risk_factors,
        "related": [
            {
                "link_id": link.link_id,
                "public_ref": link.public_ref,
                "link_type": link.link_type.value,
                "similarity": link.similarity,
                "review_state": link.review_state,
                "occurred_at": _iso(link.occurred_at),
                "location_name": link.location_name,
                "category_label": link.category_label,
            }
            for link in triage.related
        ],
    }


def serialize_notification(row) -> dict[str, Any]:
    """One notification, as its recipient may see it.

    `related_report_id` never appears — only `related_public_ref`, resolved
    by the repository. `delivery_state` and `fcm_message_id` are internal
    bookkeeping about a delivery mechanism (push) this deployment does not
    have configured, and neither is emitted: showing a client
    `delivery_state: "sent"` invites reading it as "this reached your phone,"
    which would not be true.
    """
    notification = row.notification
    return {
        "notification_id": str(notification.notification_id),
        "category": notification.category.value,
        "title": notification.title,
        "body": notification.body,
        "related_public_ref": row.related_public_ref,
        "created_at": _iso(notification.created_at),
        "read_at": _iso(notification.read_at),
    }
