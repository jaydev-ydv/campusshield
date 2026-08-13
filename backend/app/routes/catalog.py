"""Campus location and report category endpoints.

Authenticated but not role-restricted: a student cannot fill in the report form
without them, and neither list contains anything about any report.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from ..models.enums import ReportKind
from ..schemas.requests import ListCategoriesQuerySchema, ListLocationsQuerySchema
from ..schemas.responses import serialize_category, serialize_location
from ..security.context import authenticated
from .dependencies import catalog_service

bp = Blueprint("catalog", __name__)


@bp.get("/locations")
@authenticated
def list_locations():
    """Active campus locations.

    An empty list is a valid, expected answer right now: a location is only
    active once its coordinates are verified and sourced, and per
    CAMPUS_LOCATIONS.md the field survey has not happened yet. Clients should
    render "no locations configured" rather than treating this as an error.
    """
    query = ListLocationsQuerySchema().load(request.args.to_dict())
    locations = catalog_service().list_locations(location_type=query["location_type"])
    return jsonify({"items": [serialize_location(loc) for loc in locations]}), 200


@bp.get("/categories")
@authenticated
def list_categories():
    """Report categories, optionally filtered to incidents or concerns."""
    query = ListCategoriesQuerySchema().load(request.args.to_dict())
    kind = ReportKind(query["kind"]) if query["kind"] else None
    categories = catalog_service().list_categories(kind=kind)
    return jsonify({"items": [serialize_category(c) for c in categories]}), 200
