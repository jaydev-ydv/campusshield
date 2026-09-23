from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import Base
from .enums import CoordinateStatus, UserRole, pg_enum


class CampusZone(Base):
    """``core.campus_zone`` — grouping for jurisdiction and control selection."""

    __tablename__ = "campus_zone"
    __table_args__ = {"schema": "core"}

    zone_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(String)
    responsible_role: Mapped[UserRole | None] = mapped_column(pg_enum(UserRole, "user_role"))


class CampusLocation(Base):
    """``core.campus_location`` — the controlled vocabulary.

    Every report anchors here by foreign key.  Free-text place names would make
    hotspot detection, clustering, and impact measurement impossible, because
    "Library", "library block" and "Lib" would be three different places.

    ``coordinate_status`` carries the survey backlog in the table rather than in
    a document.  A location cannot become ``is_active`` without verified
    coordinates and a recorded source, so nothing un-surveyed reaches students.
    """

    __tablename__ = "campus_location"
    __table_args__ = {"schema": "core"}

    location_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    zone_id: Mapped[int | None] = mapped_column(ForeignKey("core.campus_zone.zone_id"))
    location_type: Mapped[str | None] = mapped_column(String)
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    coordinate_status: Mapped[CoordinateStatus] = mapped_column(
        pg_enum(CoordinateStatus, "coordinate_status"),
        nullable=False,
        server_default=text("'required'"),
    )
    coordinate_source: Mapped[str | None] = mapped_column(String)
    coordinate_captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_indoor: Mapped[bool | None] = mapped_column(Boolean)
    has_lighting: Mapped[bool | None] = mapped_column(Boolean)
    has_cctv: Mapped[bool | None] = mapped_column(Boolean)
    footfall_band: Mapped[str | None] = mapped_column(String)
    dispatch_note: Mapped[str | None] = mapped_column(String)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    # TRUE only for demo/development fixtures — see scripts/seed_demo_campus_
    # locations.py. Never true for a real, surveyed location; a CHECK
    # constraint (ck_campus_location_synthetic_is_labelled) requires
    # coordinate_source to start with "DEMO FIXTURE:" whenever this is true.
    is_synthetic: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    zone: Mapped[CampusZone | None] = relationship(lazy="joined")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<CampusLocation {self.code}>"
