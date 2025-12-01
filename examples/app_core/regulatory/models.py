from __future__ import annotations

import enum
import uuid
from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlalchemy.orm import relationship

from extensions import db
from app_core.db_types import GUID
from app_core.models import TimestampMixin, Project


class RegulatoryReportStatus(enum.Enum):
    draft = "draft"
    pending = "pending"
    validated = "validated"
    failed = "failed"
    generated = "generated"


class RegulatoryPillar(enum.Enum):
    decea = "decea"
    rni = "rni"
    servico = "servico"
    sarc = "sarc"


class RegulatoryValidationStatus(enum.Enum):
    approved = "approved"
    attention = "attention"
    blocked = "blocked"


class RegulatoryAttachmentType(enum.Enum):
    art = "art_profissional"
    decea = "decea_protocolo"
    rni = "rni_relatorio"
    homologacao = "homologacao"
    hrp_vrp = "hrp_vrp"
    laudo = "laudo_vistoria"
    custom = "custom"


class RegulatoryReport(TimestampMixin, db.Model):
    __tablename__ = "regulatory_reports"
    __table_args__ = (
        UniqueConstraint("project_id", "slug", name="uq_reg_reports_project_slug"),
    )

    id = db.Column(GUID(), primary_key=True, default=uuid.uuid4)
    project_id = db.Column(GUID(), db.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    name = db.Column(db.String(255), nullable=False)
    slug = db.Column(db.String(255), nullable=False)
    status = db.Column(
        db.Enum(RegulatoryReportStatus, name="reg_report_status", native_enum=False),
        nullable=False,
        default=RegulatoryReportStatus.draft,
        server_default=RegulatoryReportStatus.draft.value,
    )
    payload = db.Column(db.JSON, nullable=False)
    validation_summary = db.Column(db.JSON, nullable=True)
    output_pdf_path = db.Column(db.String(512), nullable=True)
    output_zip_path = db.Column(db.String(512), nullable=True)
    logs = db.Column(db.Text, nullable=True)

    project = relationship(Project, backref=db.backref("regulatory_reports", cascade="all, delete-orphan"))
    attachments = relationship("RegulatoryAttachment", back_populates="report", cascade="all, delete-orphan", passive_deletes=True)
    validations = relationship("RegulatoryValidation", back_populates="report", cascade="all, delete-orphan", passive_deletes=True)

    def mark_generated(self, pdf_path: Optional[str], zip_path: Optional[str]):
        self.status = RegulatoryReportStatus.generated
        self.output_pdf_path = pdf_path
        self.output_zip_path = zip_path


class RegulatoryAttachment(TimestampMixin, db.Model):
    __tablename__ = "regulatory_attachments"

    id = db.Column(GUID(), primary_key=True, default=uuid.uuid4)
    report_id = db.Column(GUID(), db.ForeignKey("regulatory_reports.id", ondelete="CASCADE"), nullable=False, index=True)
    type = db.Column(
        db.Enum(RegulatoryAttachmentType, name="reg_attachment_type", native_enum=False),
        nullable=False,
    )
    path = db.Column(db.String(512), nullable=False)
    description = db.Column(db.String(255), nullable=True)
    mime_type = db.Column(db.String(128), nullable=True)

    report = relationship("RegulatoryReport", back_populates="attachments")


class RegulatoryValidation(TimestampMixin, db.Model):
    __tablename__ = "regulatory_validations"

    id = db.Column(GUID(), primary_key=True, default=uuid.uuid4)
    report_id = db.Column(GUID(), db.ForeignKey("regulatory_reports.id", ondelete="CASCADE"), nullable=False, index=True)
    pillar = db.Column(
        db.Enum(RegulatoryPillar, name="reg_pillar", native_enum=False),
        nullable=False,
    )
    status = db.Column(
        db.Enum(RegulatoryValidationStatus, name="reg_validation_status", native_enum=False),
        nullable=False,
    )
    messages = db.Column(db.JSON, nullable=False)
    metrics = db.Column(db.JSON, nullable=True)

    report = relationship("RegulatoryReport", back_populates="validations")
