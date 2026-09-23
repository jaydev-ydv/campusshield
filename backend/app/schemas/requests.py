"""Request validation.

Marshmallow schemas are the only thing that turns request JSON into arguments a
service will accept.  ``unknown = RAISE`` throughout: an unrecognised field is
rejected rather than ignored, so a client that sends ``{"anonymous_": true}`` or
``{"user_id": "..."}`` gets an error instead of a report that quietly is not what
they asked for.

Validation here is a first line, never the guarantee.  Category and location
existence are checked by the service against the database, and the anonymity
rules are enforced by database constraints underneath both.
"""

from __future__ import annotations

from marshmallow import (
    EXCLUDE,
    RAISE,
    Schema,
    ValidationError,
    fields,
    validate,
    validates,
    validates_schema,
)

from ..models.enums import ReporterRelationship, ReportKind


class _Strict(Schema):
    class Meta:
        unknown = RAISE


class CreateReportSchema(_Strict):
    # Upper bounds match the database column widths (SMALLINT / INTEGER).  An id
    # beyond them would otherwise reach PostgreSQL and come back as a driver
    # range error — a 503 for what is plainly a bad request.
    category_id = fields.Integer(required=True, validate=validate.Range(min=1, max=32_767))
    location_id = fields.Integer(required=True, validate=validate.Range(min=1, max=2_147_483_647))
    occurred_at = fields.AwareDateTime(required=True, format="iso")
    narrative = fields.String(required=True, validate=validate.Length(min=10, max=8000))

    anonymous = fields.Boolean(load_default=False)
    reporter_relationship = fields.String(
        load_default=ReporterRelationship.AFFECTED.value,
        validate=validate.OneOf(
            [r.value for r in ReporterRelationship],
            error="Must be one of: affected, witness, third_party.",
        ),
    )
    location_hint = fields.String(
        load_default=None, allow_none=True, validate=validate.Length(max=500)
    )
    is_emergency = fields.Boolean(load_default=False)
    is_ongoing = fields.Boolean(load_default=False)
    contact_consent = fields.Boolean(load_default=True)
    # Opaque capability tokens returned by POST /evidence.
    #
    # The previous contract accepted a storage path from the client. That was a
    # defect: a path the caller names is a path the caller may not own. The
    # server now generates every path and hands back a token that maps to it.
    evidence_tokens = fields.List(
        fields.String(validate=validate.Regexp(r"^[a-f0-9]{32}$")),
        load_default=list,
        validate=validate.Length(max=5),
    )

    # Fields a client must never be able to set.  They are rejected by name with
    # an explanation rather than by the generic unknown-field error, because a
    # client sending "user_id" or "submission_mode" is trying to do something
    # specific and deserves to be told why it cannot.
    _FORBIDDEN = {
        "user_id": "The reporter is taken from your authenticated session, never the request body.",
        "reporter_user_id": "The reporter is taken from your authenticated session.",
        "submission_mode": "Use `anonymous` instead; submission_mode is immutable once set.",
        "reporter_contactable": "Derived from your consent, not settable directly.",
        "public_ref": "Assigned by the server.",
        "report_id": "Assigned by the server.",
        "current_status": "Set by authorities through the case workflow.",
        "current_risk_score": "Computed by the system.",
        "current_risk_band": "Computed by the system.",
        # The path is chosen by the server. A client naming one is either
        # confused or probing, and either way deserves to be told.
        "storage_path": "Storage paths are assigned by the server, never by the client.",
        "evidence": "Send `evidence_tokens` from POST /evidence instead of file metadata.",
        # The incident anchors to a controlled `location_id`, never a raw
        # coordinate. Named explicitly, like `storage_path` above, rather
        # than left to the generic unknown-field error: a client sending a
        # latitude/longitude pair is trying to set the destination directly,
        # and the reason that path does not exist deserves to be stated.
        "latitude": "The location comes from location_id, never a client-supplied coordinate.",
        "longitude": "The location comes from location_id, never a client-supplied coordinate.",
    }

    def handle_error(self, error: ValidationError, data, **kwargs):
        raise error

    def load(self, data, *args, **kwargs):  # type: ignore[override]
        if isinstance(data, dict):
            offending = {k: v for k, v in self._FORBIDDEN.items() if k in data}
            if offending:
                raise ValidationError({k: [v] for k, v in offending.items()})
        return super().load(data, *args, **kwargs)


class TriggerEmergencySchema(_Strict):
    """Body for ``POST /reports/emergency``.

    Deliberately almost empty — an emergency must not wait on a form. Both
    coordinates are optional and, unlike ``CreateReportSchema``, permitted:
    this is the one path in the system where a raw client coordinate is
    accepted, because it is read straight from the browser at the moment of
    the alert rather than standing in for a location the reporter chose from
    the vocabulary. The service never trusts it to *be* the location — see
    ``ReportService._resolve_emergency_location``.
    """

    latitude = fields.Float(
        load_default=None, allow_none=True, validate=validate.Range(min=-90, max=90)
    )
    longitude = fields.Float(
        load_default=None, allow_none=True, validate=validate.Range(min=-180, max=180)
    )
    reporter_relationship = fields.String(
        load_default=ReporterRelationship.AFFECTED.value,
        validate=validate.OneOf(
            [r.value for r in ReporterRelationship],
            error="Must be one of: affected, witness, third_party.",
        ),
    )

    @validates_schema
    def _paired_coordinates(self, data, **kwargs):
        # Marshmallow validates fields independently, so "both or neither" has
        # to be asserted explicitly rather than falling out of two Range
        # checks. Matches the same pairing rule core.campus_location enforces
        # for its own latitude/longitude columns.
        has_lat = data.get("latitude") is not None
        has_lon = data.get("longitude") is not None
        if has_lat != has_lon:
            raise ValidationError(
                "latitude and longitude must both be present or both be omitted.",
                field_name="longitude" if has_lat else "latitude",
            )


class AttachEvidenceSchema(_Strict):
    """Body for ``POST /reports/<public_ref>/evidence`` — evidence added after
    the report already exists, most often after an emergency trigger."""

    evidence_tokens = fields.List(
        fields.String(validate=validate.Regexp(r"^[a-f0-9]{32}$")),
        required=True,
        validate=validate.Length(min=1, max=5),
    )


class ListLocationsQuerySchema(Schema):
    class Meta:
        unknown = EXCLUDE

    location_type = fields.String(load_default=None, allow_none=True)


class ListCategoriesQuerySchema(Schema):
    class Meta:
        unknown = EXCLUDE

    kind = fields.String(
        load_default=None,
        allow_none=True,
        validate=validate.OneOf([k.value for k in ReportKind]),
    )


class PaginationQuerySchema(Schema):
    class Meta:
        unknown = EXCLUDE

    limit = fields.Integer(load_default=50, validate=validate.Range(min=1, max=100))
    offset = fields.Integer(load_default=0, validate=validate.Range(min=0))


class RegisterAccountSchema(_Strict):
    """Body for ``POST /auth/register``.

    Almost empty, deliberately. Identity comes from the verified ID token; the
    only thing a client may offer is a fallback email for credentials that carry
    none, and even that loses to a verified address.

    There is no ``role`` field, and ``unknown = RAISE`` means sending one is an
    error rather than something quietly ignored — a client attempting it should
    be told, not humoured.
    """

    email = fields.Email(load_default=None, allow_none=True)


class UpdateProfileSchema(_Strict):
    """Body for ``PATCH /auth/me``.

    Just the one field this phase supports editing. Whether the caller's role
    is even allowed to have a display name is a policy question answered by
    ``AccountService``, not by this schema — the same value here is valid for
    a security officer and rejected for a student.
    """

    display_name = fields.String(required=True, validate=validate.Length(min=1, max=120))

    @validates("display_name")
    def _not_blank(self, value: str, **kwargs) -> None:
        if not value.strip():
            raise ValidationError("display_name cannot be blank.")
