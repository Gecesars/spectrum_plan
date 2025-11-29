from __future__ import annotations

from datetime import datetime
from typing import Dict, Optional
from uuid import uuid4

from argon2 import PasswordHasher, Type
from argon2.exceptions import VerifyMismatchError, VerificationError
from flask_login import UserMixin
from geoalchemy2 import Geometry
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .config import Base

_pwd_hasher = PasswordHasher(time_cost=2, memory_cost=256000, parallelism=8, type=Type.ID)


def validate_password_strength(password: str) -> tuple[bool, Optional[str]]:
    """Basic password policy: >=8 chars, digit and letter."""
    if len(password) < 8:
        return False, "Password must have at least 8 characters."
    if not any(c.isdigit() for c in password):
        return False, "Password must include a digit."
    if not any(c.isalpha() for c in password):
        return False, "Password must include a letter."
    return True, None


class User(UserMixin, Base):
    """Application user."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    full_name: Mapped[Optional[str]] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    verification_token: Mapped[Optional[str]] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    projects: Mapped[list["Project"]] = relationship(back_populates="owner", cascade="all, delete-orphan")

    def __repr__(self) -> str:  # pragma: no cover - representational
        return f"<User {self.email}>"

    def set_password(self, raw_password: str) -> None:
        ok, msg = validate_password_strength(raw_password)
        if not ok:
            raise ValueError(msg)
        self.password_hash = _pwd_hasher.hash(raw_password)

    def check_password(self, raw_password: str) -> bool:
        try:
            return _pwd_hasher.verify(self.password_hash, raw_password)
        except (VerifyMismatchError, VerificationError):
            return False


class Project(Base):
    """Logical study/network grouping stations and simulations."""

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    owner: Mapped[User] = relationship(back_populates="projects")
    stations: Mapped[list["Station"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    simulations: Mapped[list["Simulation"]] = relationship(cascade="all, delete-orphan")

    def __repr__(self) -> str:  # pragma: no cover - representational
        return f"<Project {self.name}>"


class AntennaModel(Base):
    """Reusable antenna patterns."""

    __tablename__ = "antenna_models"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    manufacturer: Mapped[Optional[str]] = mapped_column(String(255))
    gain_dbi: Mapped[Optional[float]] = mapped_column(Float)
    horizontal_pattern: Mapped[Dict] = mapped_column(MutableDict.as_mutable(JSONB), nullable=False)
    vertical_pattern: Mapped[Optional[Dict]] = mapped_column(MutableDict.as_mutable(JSONB))

    def __repr__(self) -> str:  # pragma: no cover - representational
        return f"<AntennaModel {self.name}>"


class Station(Base):
    """RF station entity."""

    __tablename__ = "stations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    station_type: Mapped[str] = mapped_column(String(20), default="FM", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="Proposed", nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    site_elevation: Mapped[float] = mapped_column(Float, default=0.0)
    frequency_mhz: Mapped[float] = mapped_column(Float, nullable=False)
    channel_number: Mapped[Optional[int]] = mapped_column(Integer)
    erp_kw: Mapped[float] = mapped_column(Float, nullable=False)
    antenna_height: Mapped[float] = mapped_column(Float, nullable=False)
    service_class: Mapped[Optional[str]] = mapped_column(String(50))
    antenna_model_id: Mapped[Optional[int]] = mapped_column(ForeignKey("antenna_models.id"))
    azimuth: Mapped[float] = mapped_column(Float, default=0.0)
    mechanical_tilt: Mapped[float] = mapped_column(Float, default=0.0)
    polarization: Mapped[str] = mapped_column(String(50), default="Circular")
    antenna_pattern: Mapped[Dict] = mapped_column(MutableDict.as_mutable(JSONB), default=dict, nullable=False)
    location: Mapped[Optional[object]] = mapped_column(
        Geometry(geometry_type="POINT", srid=4326, spatial_index=True)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    project: Mapped[Project] = relationship(back_populates="stations")
    antenna_model: Mapped[Optional[AntennaModel]] = relationship()

    def __repr__(self) -> str:  # pragma: no cover - representational
        return f"<Station {self.name} {self.frequency_mhz} MHz>"

    # Backward-compatibility aliases
    @property
    def service_type(self) -> str:
        return self.station_type

    @service_type.setter
    def service_type(self, value: str) -> None:
        self.station_type = value

    @property
    def antenna_height_m(self) -> float:
        return self.antenna_height

    @antenna_height_m.setter
    def antenna_height_m(self, value: float) -> None:
        self.antenna_height = value


class VectorLayer(Base):
    """Catalog of GIS layers."""

    __tablename__ = "vector_layers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    geom_type: Mapped[Optional[str]] = mapped_column(String(50))
    is_visible: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[Optional[str]] = mapped_column(String(255))
    description: Mapped[Optional[str]] = mapped_column(String(1024))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    features: Mapped[list["VectorFeature"]] = relationship(
        back_populates="layer", cascade="all, delete-orphan"
    )
    user: Mapped[Optional[User]] = relationship()

    def __repr__(self) -> str:  # pragma: no cover - representational
        return f"<VectorLayer {self.name}>"


class VectorFeature(Base):
    """Spatial feature storing IBGE and other vector data."""

    __tablename__ = "vector_features"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    layer_id: Mapped[int] = mapped_column(ForeignKey("vector_layers.id"), nullable=False)
    properties: Mapped[Dict] = mapped_column(MutableDict.as_mutable(JSONB), default=dict, nullable=False)
    geom: Mapped[object] = mapped_column(
        Geometry(geometry_type="MULTIPOLYGON", srid=4326, spatial_index=False),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    layer: Mapped[VectorLayer] = relationship(back_populates="features")

    __table_args__ = (
        Index("idx_vector_features_geom", "geom", postgresql_using="gist"),
    )

    def __repr__(self) -> str:  # pragma: no cover - representational
        return f"<VectorFeature {self.id} layer={self.layer_id}>"


class Simulation(Base):
    """Simulation metadata for coverage/interference jobs."""

    __tablename__ = "simulations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_id: Mapped[Optional[int]] = mapped_column(ForeignKey("projects.id"))
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"), nullable=False)
    calc_type: Mapped[Optional[str]] = mapped_column(String(50))
    resolution_m: Mapped[Optional[int]] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="PENDING", nullable=False)
    task_id: Mapped[Optional[str]] = mapped_column(String(255))
    result_path: Mapped[Optional[str]] = mapped_column(String(512))
    bbox_north: Mapped[Optional[float]] = mapped_column(Float)
    bbox_south: Mapped[Optional[float]] = mapped_column(Float)
    bbox_east: Mapped[Optional[float]] = mapped_column(Float)
    bbox_west: Mapped[Optional[float]] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    station: Mapped[Station] = relationship()
    project: Mapped[Optional[Project]] = relationship(overlaps="simulations")
    artifacts: Mapped[list["ProjectArtifact"]] = relationship(
        back_populates="simulation", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover - representational
        return f"<Simulation {self.id} status={self.status}>"


class ProjectArtifact(Base):
    """Generated artifacts (overlays, geotiffs, etc.)."""

    __tablename__ = "project_artifacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    simulation_id: Mapped[str] = mapped_column(ForeignKey("simulations.id"), nullable=False)
    artifact_type: Mapped[str] = mapped_column(String(50), nullable=False)
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    bounds: Mapped[Optional[Dict]] = mapped_column(MutableDict.as_mutable(JSONB))
    style_metadata: Mapped[Optional[Dict]] = mapped_column(MutableDict.as_mutable(JSONB))

    simulation: Mapped[Simulation] = relationship(back_populates="artifacts")

    def __repr__(self) -> str:  # pragma: no cover - representational
        return f"<Artifact {self.artifact_type} {self.file_path}>"
