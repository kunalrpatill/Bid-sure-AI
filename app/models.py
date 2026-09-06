"""
SQLAlchemy ORM models for all Phase 1–N tables.
All tables are created now to avoid future migrations.

IMPORTANT INVARIANTS:
- officer_decisions: insert-only. Never UPDATE or DELETE.
- audit_log: insert-only. Never UPDATE or DELETE.
- Every write to requirements.approved_at MUST insert a row in audit_log
  with a SHA-256 hash chain (see routers/requirements.py).
"""
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Tender(Base):
    __tablename__ = "tenders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    tender_number: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    category: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    # relationships
    requirements: Mapped[list["Requirement"]] = relationship(
        "Requirement", back_populates="tender", cascade="all, delete-orphan"
    )
    bidders: Mapped[list["Bidder"]] = relationship(
        "Bidder", back_populates="tender", cascade="all, delete-orphan"
    )


class Requirement(Base):
    __tablename__ = "requirements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    tender_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tenders.id"), nullable=False, index=True
    )
    label: Mapped[str] = mapped_column(String(500), nullable=False)
    clause_ref: Mapped[str] = mapped_column(String(100), nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # relationships
    tender: Mapped["Tender"] = relationship("Tender", back_populates="requirements")
    evidence: Mapped[list["Evidence"]] = relationship(
        "Evidence", back_populates="requirement", cascade="all, delete-orphan"
    )


class Rule(Base):
    """
    Verification rules engine table — populated in a later phase.
    Defined here to avoid future schema migrations.
    """
    __tablename__ = "rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    requirement_type: Mapped[str] = mapped_column(String(200), nullable=False)
    field: Mapped[str] = mapped_column(String(200), nullable=False)
    operator: Mapped[str] = mapped_column(String(50), nullable=False)
    threshold: Mapped[str | None] = mapped_column(String(500), nullable=True)
    effective_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    source_clause: Mapped[str | None] = mapped_column(String(200), nullable=True)


class Bidder(Base):
    """
    Bidder table — populated in a later phase.
    Defined here to avoid future schema migrations.
    """
    __tablename__ = "bidders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    tender_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tenders.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(500), nullable=False)

    # relationships
    tender: Mapped["Tender"] = relationship("Tender", back_populates="bidders")
    evidence: Mapped[list["Evidence"]] = relationship(
        "Evidence", back_populates="bidder", cascade="all, delete-orphan"
    )


class Evidence(Base):
    """
    Evidence table — populated in a later phase.
    Defined here to avoid future schema migrations.
    """
    __tablename__ = "evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    bidder_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("bidders.id"), nullable=False, index=True
    )
    requirement_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("requirements.id"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    extracted_value: Mapped[str | None] = mapped_column(String(500), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_doc: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extracted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # relationships
    bidder: Mapped["Bidder"] = relationship("Bidder", back_populates="evidence")
    requirement: Mapped["Requirement"] = relationship(
        "Requirement", back_populates="evidence"
    )
    officer_decisions: Mapped[list["OfficerDecision"]] = relationship(
        "OfficerDecision", back_populates="evidence"
    )


class Document(Base):
    """
    Document tracking table for uploaded PDFs.
    """
    __tablename__ = "documents"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    document_type: Mapped[str] = mapped_column(String(50), nullable=False) # "tender" or "bidder"
    tender_id: Mapped[int] = mapped_column(Integer, ForeignKey("tenders.id"), nullable=False)
    bidder_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("bidders.id"), nullable=True)
    file_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    
    
class OfficerDecision(Base):
    """
    Officer review decisions — INSERT ONLY. Never UPDATE or DELETE.
    Populated in a later phase.
    """
    __tablename__ = "officer_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    evidence_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("evidence.id"), nullable=False, index=True
    )
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    officer_name: Mapped[str] = mapped_column(String(200), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    # relationships
    evidence: Mapped["Evidence"] = relationship(
        "Evidence", back_populates="officer_decisions"
    )


class AuditLog(Base):
    """
    Append-only, hash-chained audit log — INSERT ONLY. Never UPDATE or DELETE.

    hash = sha256(prev_hash + json_payload_of_this_event)
    First row uses prev_hash = "0" * 64.
    """
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
