"""
Pydantic v2 request/response schemas for Phase 1 + Phase 2 endpoints.
"""
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ---------------------------------------------------------------------------
# Tender schemas
# ---------------------------------------------------------------------------

class TenderCreate(BaseModel):
    tender_number: str = Field(..., max_length=100, examples=["GEM/2026/IT-HW/00214"])
    title: str = Field(..., max_length=500, examples=["Procurement of Desktop Computers"])
    category: str = Field(..., max_length=200, examples=["IT Hardware"])


class TenderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tender_number: str
    title: str
    category: str
    created_at: datetime


# ---------------------------------------------------------------------------
# Requirement schemas
# ---------------------------------------------------------------------------

class RequirementCreate(BaseModel):
    label: str = Field(..., max_length=500, examples=["PAN / GSTIN Presence"])
    clause_ref: str = Field(..., max_length=100, examples=["Clause 3.1"])


class RequirementRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tender_id: int
    label: str
    clause_ref: str
    approved_by: str | None
    approved_at: datetime | None


class BulkRequirementsCreate(BaseModel):
    requirements: list[RequirementCreate] = Field(
        ..., min_length=1, description="At least one requirement must be provided."
    )


# ---------------------------------------------------------------------------
# Approval schemas
# ---------------------------------------------------------------------------

class ApproveRequest(BaseModel):
    officer_name: str = Field(..., min_length=1, max_length=200)


class ApproveResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    requirement: RequirementRead
    audit_log_id: int


# ---------------------------------------------------------------------------
# Audit log schemas
# ---------------------------------------------------------------------------

class AuditLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_type: str
    payload_json: str
    prev_hash: str
    hash: str
    created_at: datetime


# ---------------------------------------------------------------------------
# Bidder schemas  (Phase 2)
# ---------------------------------------------------------------------------

class BidderCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=500, examples=["Alpha Technologies Pvt Ltd"])


class BidderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tender_id: int
    name: str


# ---------------------------------------------------------------------------
# Evidence schemas  (Phase 2)
# ---------------------------------------------------------------------------

class EvidenceStatus(str, Enum):
    evidenced       = "evidenced"
    low_confidence  = "low_confidence"
    not_detected    = "not_detected"
    mismatch        = "mismatch"


class EvidenceCreate(BaseModel):
    """
    A single evidence row.

    Validation rules (enforced by model_validator):
    - status == "not_detected"  → confidence MUST be null
    - status != "not_detected"  → confidence MUST be a number in [0, 100]
    """
    requirement_id: int
    status: EvidenceStatus
    confidence: float | None = Field(
        default=None,
        ge=0,
        le=100,
        description="0–100 for all statuses except not_detected (must be null).",
    )
    extracted_value: str | None = Field(default=None, max_length=500)
    note: str | None = Field(default=None, max_length=1000)
    source_doc: str | None = Field(default=None, max_length=500)
    source_page: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_confidence_vs_status(self) -> "EvidenceCreate":
        if self.status == EvidenceStatus.not_detected:
            if self.confidence is not None:
                raise ValueError(
                    "confidence must be null when status is 'not_detected'."
                )
        else:
            if self.confidence is None:
                raise ValueError(
                    f"confidence is required (0–100) when status is '{self.status.value}'."
                )
        return self


class BulkEvidenceCreate(BaseModel):
    """
    Body for POST /bidders/{id}/evidence.
    """
    evidence: list[EvidenceCreate] = Field(
        ..., min_length=1, description="One entry per requirement on the tender."
    )


class EvidenceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    bidder_id: int
    requirement_id: int
    requirement_label: str
    requirement_clause_ref: str
    status: str
    confidence: float | None
    extracted_value: str | None
    note: str | None
    source_doc: str | None
    source_page: int | None
    extracted_at: datetime | None


class EvidenceIngestResponse(BaseModel):
    evidence: list[EvidenceRead]
    audit_log_id: int


# ---------------------------------------------------------------------------
# Evaluation schemas (Phase 3)
# ---------------------------------------------------------------------------

class RequirementResult(BaseModel):
    requirement_id: int
    requirement_label: str
    clause_ref: str
    rule_id: int | None
    rule_version: str | None
    status: str
    confidence: float | None
    extracted_value: str | None
    threshold: str | None
    operator: str | None
    explanation: str
    source_doc: str | None
    source_page: int | None
    evidence_id: int | None


class EvaluationResponse(BaseModel):
    """
    Response for POST /bidders/{id}/evaluate and GET /bidders/{id}/evaluation.
    This entire object is also stored in the audit_log payload.
    """
    bidder_id: int
    bidder: str
    tender_id: int
    overall_status: str
    requirement_results: list[RequirementResult]
    evaluated_at: datetime


# ---------------------------------------------------------------------------
# Officer Review schemas (Phase 4)
# ---------------------------------------------------------------------------

class OfficerActionEnum(str, Enum):
    accept = "accept"
    reject = "reject"
    request_clarification = "request_clarification"


class OfficerDecisionCreate(BaseModel):
    action: OfficerActionEnum
    comment: str = Field(..., min_length=1)
    officer_name: str = Field(..., min_length=1, max_length=200)


class OfficerDecisionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    evidence_id: int
    action: str
    comment: str | None
    officer_name: str
    decided_at: datetime


class BidderReviewRequirement(BaseModel):
    requirement_id: int
    label: str
    clause_ref: str
    automated_status: str
    confidence: float | None
    extracted_value: str | None
    explanation: str
    source_doc: str | None
    source_page: int | None
    evidence_id: int | None
    requires_officer_review: bool
    officer_decision: OfficerDecisionRead | None


class BidderReviewResponse(BaseModel):
    bidder: dict
    tender: dict
    automated_status: str
    requirements: list[BidderReviewRequirement]


class FinalizeRequest(BaseModel):
    officer_name: str = Field(..., min_length=1, max_length=200)
    comment: str = Field(..., min_length=1)


class FinalizeResponse(BaseModel):
    bidder_id: int
    bidder_name: str
    automated_status: str
    final_status: str
    officer_name: str
    requirements: list[BidderReviewRequirement]


# ---------------------------------------------------------------------------
# Compliance Report schemas (Phase 5)
# ---------------------------------------------------------------------------

class ReportSummary(BaseModel):
    automated_status: str
    final_status: str
    total_requirements: int
    pass_count: int
    fail_count: int
    review_count: int


class AuditEventMetadata(BaseModel):
    event_type: str
    timestamp: datetime
    hash: str
    prev_hash: str


class ReportResponse(BaseModel):
    report_id: str
    generated_at: datetime
    tender: dict
    bidder: dict
    summary: ReportSummary
    requirements: list[BidderReviewRequirement]
    audit_information: list[AuditEventMetadata]


# ---------------------------------------------------------------------------
# Document Processing schemas (Phase 6)
# ---------------------------------------------------------------------------

class DocumentUploadResponse(BaseModel):
    id: int
    filename: str
    document_type: str
    tender_id: int
    bidder_id: int | None
    page_count: int | None
    uploaded_at: datetime


class ProcessDocumentsResponse(BaseModel):
    bidder_id: int
    documents_processed: int
    requirements_processed: int
    evidence_created: int
    review_required: bool

