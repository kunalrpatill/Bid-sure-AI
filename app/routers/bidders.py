"""
Bidders & Evidence router  (Phase 2)

Endpoints:
  POST /tenders/{id}/bidders      — create a bidder under a tender
  GET  /tenders/{id}/bidders      — list bidders for a tender
  POST /bidders/{id}/evidence     — bulk-create evidence for a bidder
                                    + one hash-chained audit_log entry
  GET  /bidders/{id}/evidence     — list evidence with joined requirement fields

Validation rules (enforced here, not just in comments):
  - POST /bidders/{id}/evidence must supply exactly one row per requirement
    on the bidder's tender — missing or duplicate requirement_ids → 422.
  - confidence / status cross-check is done in EvidenceCreate (schema level).

Audit invariant:
  - evidence ingestion writes ONE audit_log row per call (per bidder),
    using the same sha256 hash-chain as Phase 1.
"""
import hashlib
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AuditLog, Bidder, Evidence, Requirement, Tender
from app.schemas import (
    BidderCreate,
    BidderRead,
    BulkEvidenceCreate,
    EvidenceIngestResponse,
    EvidenceRead,
)

router = APIRouter(tags=["Bidders & Evidence"])

# ---------------------------------------------------------------------------
# Audit-log helpers (mirrors requirements.py — shared logic kept local
# to each router so routers stay independently deployable in future)
# ---------------------------------------------------------------------------

GENESIS_PREV_HASH = "0" * 64


def _latest_audit_hash(db: Session) -> str:
    last = db.query(AuditLog).order_by(AuditLog.id.desc()).first()
    return last.hash if last else GENESIS_PREV_HASH


def _compute_hash(prev_hash: str, payload: dict) -> str:
    raw = prev_hash + json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _insert_audit_log(db: Session, event_type: str, payload: dict) -> AuditLog:
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
# Helper: build EvidenceRead from an ORM Evidence object + its Requirement
# ---------------------------------------------------------------------------

def _evidence_to_read(ev: Evidence, req: Requirement) -> EvidenceRead:
    return EvidenceRead(
        id=ev.id,
        bidder_id=ev.bidder_id,
        requirement_id=ev.requirement_id,
        requirement_label=req.label,
        requirement_clause_ref=req.clause_ref,
        status=ev.status,
        confidence=ev.confidence,
        extracted_value=ev.extracted_value,
        note=ev.note,
        source_doc=ev.source_doc,
        source_page=ev.source_page,
        extracted_at=ev.extracted_at,
    )


# ---------------------------------------------------------------------------
# POST /tenders/{tender_id}/bidders
# ---------------------------------------------------------------------------

@router.post(
    "/tenders/{tender_id}/bidders",
    response_model=BidderRead,
    status_code=status.HTTP_201_CREATED,
)
def create_bidder(
    tender_id: int,
    body: BidderCreate,
    db: Session = Depends(get_db),
):
    """Create a bidder under a tender."""
    tender = db.get(Tender, tender_id)
    if not tender:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tender {tender_id} not found.",
        )

    bidder = Bidder(tender_id=tender_id, name=body.name)
    db.add(bidder)
    db.commit()
    db.refresh(bidder)
    return bidder


# ---------------------------------------------------------------------------
# GET /tenders/{tender_id}/bidders
# ---------------------------------------------------------------------------

@router.get(
    "/tenders/{tender_id}/bidders",
    response_model=list[BidderRead],
)
def list_bidders(tender_id: int, db: Session = Depends(get_db)):
    """List all bidders for a tender."""
    tender = db.get(Tender, tender_id)
    if not tender:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tender {tender_id} not found.",
        )
    return (
        db.query(Bidder)
        .filter(Bidder.tender_id == tender_id)
        .order_by(Bidder.id)
        .all()
    )


# ---------------------------------------------------------------------------
# POST /bidders/{bidder_id}/evidence
# ---------------------------------------------------------------------------

@router.post(
    "/bidders/{bidder_id}/evidence",
    response_model=EvidenceIngestResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_evidence(
    bidder_id: int,
    body: BulkEvidenceCreate,
    db: Session = Depends(get_db),
):
    """
    Bulk-create evidence for a bidder.

    Business rules enforced here:
    1. Exactly one row per requirement on the bidder's tender
       (no missing, no duplicates).
    2. Atomically inserts all evidence rows + one audit_log entry.
    """
    bidder = db.get(Bidder, bidder_id)
    if not bidder:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Bidder {bidder_id} not found.",
        )

    # Fetch the full requirement set for this bidder's tender
    tender_requirements: list[Requirement] = (
        db.query(Requirement)
        .filter(Requirement.tender_id == bidder.tender_id)
        .order_by(Requirement.id)
        .all()
    )
    tender_req_ids = {r.id for r in tender_requirements}
    req_map = {r.id: r for r in tender_requirements}

    # --- Validate completeness & uniqueness ---
    submitted_ids = [e.requirement_id for e in body.evidence]

    # Duplicate check
    seen: set[int] = set()
    duplicates: set[int] = set()
    for rid in submitted_ids:
        if rid in seen:
            duplicates.add(rid)
        seen.add(rid)
    if duplicates:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Duplicate requirement_id(s) in request: {sorted(duplicates)}. "
                   "Submit exactly one evidence row per requirement.",
        )

    # Unknown requirement_ids (not on this tender)
    unknown = seen - tender_req_ids
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"requirement_id(s) {sorted(unknown)} do not belong to tender "
                   f"{bidder.tender_id}.",
        )

    # Missing requirement_ids
    missing = tender_req_ids - seen
    if missing:
        missing_labels = [
            f"{rid} ({req_map[rid].label})" for rid in sorted(missing)
        ]
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Missing evidence for requirement(s): {missing_labels}. "
                   "Every requirement on the tender must have exactly one evidence row.",
        )

    # --- Insert evidence rows ---
    now = datetime.now(timezone.utc)
    created_evidence: list[Evidence] = []

    for ev_in in body.evidence:
        ev = Evidence(
            bidder_id=bidder_id,
            requirement_id=ev_in.requirement_id,
            status=ev_in.status.value,
            confidence=ev_in.confidence,
            extracted_value=ev_in.extracted_value,
            note=ev_in.note,
            source_doc=ev_in.source_doc,
            source_page=ev_in.source_page,
            extracted_at=now,
        )
        db.add(ev)
        created_evidence.append(ev)

    # --- One audit_log entry per ingestion call ---
    audit_payload = {
        "event": "evidence_ingested",
        "bidder_id": bidder_id,
        "bidder_name": bidder.name,
        "tender_id": bidder.tender_id,
        "evidence_count": len(created_evidence),
        "requirement_ids": sorted(submitted_ids),
        "ingested_at": now.isoformat(),
    }
    audit_entry = _insert_audit_log(db, event_type="evidence_ingested", payload=audit_payload)

    # Single commit — all evidence rows + audit entry
    db.commit()
    for ev in created_evidence:
        db.refresh(ev)
    db.refresh(audit_entry)

    evidence_reads = [
        _evidence_to_read(ev, req_map[ev.requirement_id])
        for ev in created_evidence
    ]

    return EvidenceIngestResponse(evidence=evidence_reads, audit_log_id=audit_entry.id)


# ---------------------------------------------------------------------------
# GET /bidders/{bidder_id}/evidence
# ---------------------------------------------------------------------------

@router.get(
    "/bidders/{bidder_id}/evidence",
    response_model=list[EvidenceRead],
)
def list_evidence(bidder_id: int, db: Session = Depends(get_db)):
    """
    List all evidence for a bidder, with requirement label and clause_ref
    joined in — self-contained for frontend rendering.
    """
    bidder = db.get(Bidder, bidder_id)
    if not bidder:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Bidder {bidder_id} not found.",
        )

    rows = (
        db.query(Evidence, Requirement)
        .join(Requirement, Evidence.requirement_id == Requirement.id)
        .filter(Evidence.bidder_id == bidder_id)
        .order_by(Requirement.id)
        .all()
    )

    return [_evidence_to_read(ev, req) for ev, req in rows]
