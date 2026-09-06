"""
Requirements router.

Endpoints:
  POST /tenders/{id}/requirements  — bulk-create requirements
  GET  /tenders/{id}/requirements  — list requirements for a tender
  POST /requirements/{id}/approve  — officer approves a requirement
                                     + writes a hash-chained audit_log row

INVARIANT: Every write to requirements.approved_at MUST also insert a row
into audit_log. The hash is sha256(prev_hash || json_payload).
The genesis row uses prev_hash = "0" * 64.
"""
import hashlib
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AuditLog, Requirement, Tender
from app.schemas import (
    ApproveRequest,
    ApproveResponse,
    BulkRequirementsCreate,
    RequirementRead,
)

router = APIRouter(tags=["Requirements"])

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

GENESIS_PREV_HASH = "0" * 64


def _latest_audit_hash(db: Session) -> str:
    """Return the hash of the most-recent audit_log row, or the genesis value."""
    last = (
        db.query(AuditLog)
        .order_by(AuditLog.id.desc())
        .first()
    )
    return last.hash if last else GENESIS_PREV_HASH


def _compute_hash(prev_hash: str, payload: dict) -> str:
    """sha256(prev_hash + canonical_json(payload))."""
    raw = prev_hash + json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _insert_audit_log(
    db: Session,
    event_type: str,
    payload: dict,
) -> AuditLog:
    """
    Append an entry to the audit_log with a valid hash chain.
    Must be called inside an open transaction that has NOT yet been committed,
    so that prev_hash is fetched before any concurrent write can race.
    """
    prev_hash = _latest_audit_hash(db)
    h = _compute_hash(prev_hash, payload)
    entry = AuditLog(
        event_type=event_type,
        payload_json=json.dumps(payload, sort_keys=True, default=str),
        prev_hash=prev_hash,
        hash=h,
    )
    db.add(entry)
    return entry


# ---------------------------------------------------------------------------
# Bulk-create requirements
# ---------------------------------------------------------------------------

@router.post(
    "/tenders/{tender_id}/requirements",
    response_model=list[RequirementRead],
    status_code=status.HTTP_201_CREATED,
)
def create_requirements(
    tender_id: int,
    payload: BulkRequirementsCreate,
    db: Session = Depends(get_db),
):
    """Bulk-create requirements for a tender."""
    tender = db.get(Tender, tender_id)
    if not tender:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tender {tender_id} not found.",
        )

    created = []
    for req_in in payload.requirements:
        req = Requirement(
            tender_id=tender_id,
            label=req_in.label,
            clause_ref=req_in.clause_ref,
        )
        db.add(req)
        created.append(req)

    db.commit()
    for req in created:
        db.refresh(req)
    return created


# ---------------------------------------------------------------------------
# List requirements
# ---------------------------------------------------------------------------

@router.get(
    "/tenders/{tender_id}/requirements",
    response_model=list[RequirementRead],
)
def list_requirements(tender_id: int, db: Session = Depends(get_db)):
    """List all requirements for a tender."""
    tender = db.get(Tender, tender_id)
    if not tender:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tender {tender_id} not found.",
        )
    return (
        db.query(Requirement)
        .filter(Requirement.tender_id == tender_id)
        .order_by(Requirement.id)
        .all()
    )


# ---------------------------------------------------------------------------
# Officer approval
# ---------------------------------------------------------------------------

@router.post(
    "/requirements/{requirement_id}/approve",
    response_model=ApproveResponse,
)
def approve_requirement(
    requirement_id: int,
    body: ApproveRequest,
    db: Session = Depends(get_db),
):
    """
    Officer approves a requirement.
    Atomically:
      1. Sets requirements.approved_by / approved_at.
      2. Inserts a hash-chained row into audit_log.
    """
    req = db.get(Requirement, requirement_id)
    if not req:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Requirement {requirement_id} not found.",
        )

    now = datetime.now(timezone.utc)

    # Build audit payload BEFORE mutating the ORM object
    payload = {
        "event": "requirement_approved",
        "requirement_id": requirement_id,
        "tender_id": req.tender_id,
        "label": req.label,
        "clause_ref": req.clause_ref,
        "officer_name": body.officer_name,
        "approved_at": now.isoformat(),
    }

    # Mutate requirement
    req.approved_by = body.officer_name
    req.approved_at = now
    db.add(req)

    # Append to audit log — same transaction
    audit_entry = _insert_audit_log(db, event_type="requirement_approved", payload=payload)

    # Single commit — both writes succeed or both roll back
    db.commit()
    db.refresh(req)
    db.refresh(audit_entry)

    return ApproveResponse(requirement=req, audit_log_id=audit_entry.id)
