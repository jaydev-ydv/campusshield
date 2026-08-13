"""Read access to the campus location and report category vocabularies."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import CampusLocation, ReportCategory, SystemPolicy
from ..models.enums import ReportKind


class LocationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_active(self, *, location_type: str | None = None) -> list[CampusLocation]:
        """Active locations only.

        A location is active only once its coordinates are verified and sourced,
        so this can never hand a client a place with no position on the map.
        """
        stmt = select(CampusLocation).where(CampusLocation.is_active.is_(True))
        if location_type:
            stmt = stmt.where(CampusLocation.location_type == location_type)
        return list(self._session.scalars(stmt.order_by(CampusLocation.name)))

    def get_active(self, location_id: int) -> CampusLocation | None:
        return self._session.scalar(
            select(CampusLocation).where(
                CampusLocation.location_id == location_id,
                CampusLocation.is_active.is_(True),
            )
        )

    def count_all(self) -> int:
        return len(list(self._session.scalars(select(CampusLocation.location_id))))


class CategoryRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_active(self, *, kind: ReportKind | None = None) -> list[ReportCategory]:
        stmt = select(ReportCategory).where(ReportCategory.is_active.is_(True))
        if kind is not None:
            stmt = stmt.where(ReportCategory.kind == kind)
        return list(self._session.scalars(stmt.order_by(ReportCategory.kind, ReportCategory.label)))

    def get_active(self, category_id: int) -> ReportCategory | None:
        return self._session.scalar(
            select(ReportCategory).where(
                ReportCategory.category_id == category_id,
                ReportCategory.is_active.is_(True),
            )
        )


class PolicyRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_int(self, key: str, default: int) -> int:
        row = self._session.get(SystemPolicy, key)
        if row is None:
            return default
        try:
            return int(row.policy_value)
        except ValueError:
            return default
