"""The campus location and report category vocabularies."""

from __future__ import annotations

from ..models import CampusLocation, ReportCategory
from ..models.enums import ReportKind
from ..repositories.catalog_repository import CategoryRepository, LocationRepository


class CatalogService:
    def __init__(self, *, locations: LocationRepository, categories: CategoryRepository) -> None:
        self._locations = locations
        self._categories = categories

    def list_locations(self, *, location_type: str | None = None) -> list[CampusLocation]:
        """Active locations only.

        A location becomes active only once its coordinates are verified and
        sourced, so an un-surveyed place can never be offered as a choice.  Until
        the field survey happens this list is legitimately empty — see
        CAMPUS_LOCATIONS.md, where zero coordinates are verified — and an empty
        vocabulary is the correct answer rather than a bug to work around.
        """
        return self._locations.list_active(location_type=location_type)

    def list_categories(self, *, kind: ReportKind | None = None) -> list[ReportCategory]:
        return self._categories.list_active(kind=kind)
