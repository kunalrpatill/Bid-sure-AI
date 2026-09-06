"""
Phase 4: Officer Review & Final Decision Endpoints.
"""
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AuditLog, Bidder, Evidence, OfficerDecision, Tender
from app.schemas import (
    BidderReviewRequirement,
    BidderReviewResponse,
    FinalizeRequest,
    FinalizeResponse,
    OfficerDecisionCreate,
    OfficerDecisionRead,
)
from app.routers.bidders import _insert_audit_log

router = APIRouter(tags=["Officer Review (Phase 4)"])


def _get_latest_evaluation(bidder_id: int, db: Session) -> dict:
    log = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == "bidder_evaluated")
        .filter(AuditLog.payload_json.like(f'%"bidder_id": {bidder_id},%'))
        .order_by(AuditLog.id.desc())
        .first()
    )
    if not log:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No evaluation found for this bidder. Cannot proceed with review.",
        )
    return json.loads(log.payload_json)


def _build_review_requirements(eval_data: dict, db: Session) -> list[BidderReviewRequirement]:
    req_results = eval_data.get("requirement_results", [])
    if not req_results:
        return []

    evidence_ids = [r.get("evidence_id") for r in req_results if r.get("evidence_id")]
    
    decisions = []
    if evidence_ids:
        # Get the LATEST officer decision for each evidence
        # (Though we shouldn't have multiple per evidence, just in case, we order by id desc)
        decisions = (
            db.query(OfficerDecision)
            .filter(OfficerDecision.evidence_id.in_(evidence_ids))
            .order_by(OfficerDecision.id.desc())
            .all()
        )
    
    dec_map = {}
    for d in decisions:
        if d.evidence_id not in dec_map:
            dec_map[d.evidence_id] = d

    review_reqs = []
    for r in req_results:
        ev_id = r.get("evidence_id")
        dec = dec_map.get(ev_id) if ev_id else None
        
        auto_status = r.get("status", "REVIEW")
        
        # Requires officer review if automated status is REVIEW or FAIL?
        # Actually, let's say REVIEW always requires an officer review.
        requires_review = (auto_status == "REVIEW")
        
        dec_read = None
        if dec:
            dec_read = OfficerDecisionRead(
                id=dec.id,
                evidence_id=dec.evidence_id,
                action=dec.action,
                comment=dec.comment,
                officer_name=dec.officer_name,
                decided_at=dec.decided_at,
            )
            
        review_reqs.append(
            BidderReviewRequirement(
                requirement_id=r.get("requirement_id"),
                label=r.get("requirement_label"),
                clause_ref=r.get("clause_ref"),
                automated_status=auto_status,
                confidence=r.get("confidence"),
                extracted_value=r.get("extracted_value"),
                explanation=r.get("explanation"),
                source_doc=r.get("source_doc"),
                source_page=r.get("source_page"),
                evidence_id=ev_id,
                requires_officer_review=requires_review,
                officer_decision=dec_read,
            )
        )
    return review_reqs


@router.get("/bidders/{bidder_id}/review", response_model=BidderReviewResponse)
def get_bidder_review(bidder_id: int, db: Session = Depends(get_db)):
    """
    Returns everything an officer needs to review the bidder, joining the
    latest automated evaluation with existing officer decisions.
    """
    bidder = db.get(Bidder, bidder_id)
    if not bidder:
        raise HTTPException(status_code=404, detail="Bidder not found")
        
    tender = db.get(Tender, bidder.tender_id)
    
    eval_data = _get_latest_evaluation(bidder_id, db)
    review_reqs = _build_review_requirements(eval_data, db)
    
    return BidderReviewResponse(
        bidder={"id": bidder.id, "name": bidder.name},
        tender={"id": tender.id, "tender_number": tender.tender_number},
        automated_status=eval_data.get("overall_status", "REVIEW"),
        requirements=review_reqs,
    )


@router.post("/evidence/{evidence_id}/decision", response_model=OfficerDecisionRead, status_code=status.HTTP_201_CREATED)
def submit_officer_decision(evidence_id: int, decision: OfficerDecisionCreate, db: Session = Depends(get_db)):
    """
    Submit an officer decision for a specific piece of evidence.
    This creates an immutable record in the officer_decisions table.
    """
    ev = db.get(Evidence, evidence_id)
    if not ev:
        raise HTTPException(status_code=404, detail="Evidence not found")
        
    # Check if a decision already exists. The prompt states "NEVER update an existing officer_decisions row".
    # We should probably prevent duplicate decisions on the same evidence entirely,
    # or just insert a new one and the latest counts. The prompt says "Existing officer decisions cannot be updated/deleted".
    # Let's just prevent multiple decisions on the same evidence ID to strictly follow "Never update" logic natively.
    existing = db.query(OfficerDecision).filter(OfficerDecision.evidence_id == evidence_id).first()
    if existing:
        raise HTTPException(
            status_code=409, 
            detail="A decision for this evidence already exists. Existing decisions cannot be updated."
        )

    now = datetime.now(timezone.utc)
    new_dec = OfficerDecision(
        evidence_id=evidence_id,
        action=decision.action.value,
        comment=decision.comment,
        officer_name=decision.officer_name,
        decided_at=now
    )
    db.add(new_dec)
    db.flush()
    
    # Audit log
    payload = {
        "event": "officer_decision",
        "officer_decision_id": new_dec.id,
        "evidence_id": evidence_id,
        "bidder_id": ev.bidder_id,
        "requirement_id": ev.requirement_id,
        "action": new_dec.action,
        "comment": new_dec.comment,
        "officer_name": new_dec.officer_name,
        "timestamp": now.isoformat()
    }
    _insert_audit_log(db, event_type="officer_decision", payload=payload)
    db.commit()
    db.refresh(new_dec)
    
    return new_dec


@router.post("/bidders/{bidder_id}/finalize", response_model=FinalizeResponse)
def finalize_bidder_decision(bidder_id: int, req: FinalizeRequest, db: Session = Depends(get_db)):
    """
    Calculates the final bidder status combining automated results and officer decisions.
    """
    bidder = db.get(Bidder, bidder_id)
    if not bidder:
        raise HTTPException(status_code=404, detail="Bidder not found")
        
    eval_data = _get_latest_evaluation(bidder_id, db)
    review_reqs = _build_review_requirements(eval_data, db)
    
    # Calculate final status
    # 1. If any requirement has an unresolved FAIL -> FAIL
    # 2. If automated status is REVIEW:
    #    - accepted -> satisfied
    #    - rejected -> FAIL
    #    - clarification -> REVIEW
    # 3. If all requirements are satisfied -> PASS
    # 4. If unresolved REVIEW remains -> REVIEW
    
    final_status = "PASS"
    has_review = False
    
    for r in review_reqs:
        current_status = r.automated_status
        dec = r.officer_decision
        
        if dec:
            if dec.action == "accept":
                current_status = "PASS"
            elif dec.action == "reject":
                current_status = "FAIL"
            elif dec.action == "request_clarification":
                current_status = "REVIEW"
                
        if current_status == "FAIL":
            final_status = "FAIL"
            break  # one FAIL ruins everything immediately
        elif current_status == "REVIEW":
            has_review = True
            
    if final_status != "FAIL" and has_review:
        final_status = "REVIEW"

    # The prompt: "Do NOT allow finalization to silently ignore unresolved REVIEW cases."
    # If it ends up as REVIEW, we just output final_status = "REVIEW".
    # It says "If unresolved REVIEW remains -> final status = REVIEW".
    # This is handled correctly above.
    
    resp = FinalizeResponse(
        bidder_id=bidder.id,
        bidder_name=bidder.name,
        automated_status=eval_data.get("overall_status", "REVIEW"),
        final_status=final_status,
        officer_name=req.officer_name,
        requirements=review_reqs
    )
    
    # Audit logging
    payload = {
        "event": "bidder_finalized",
        "bidder_id": bidder.id,
        "tender_id": bidder.tender_id,
        "automated_status": resp.automated_status,
        "final_status": resp.final_status,
        "officer_name": req.officer_name,
        "comment": req.comment,
        "requirement_results": [json.loads(r.model_dump_json()) for r in review_reqs],
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    _insert_audit_log(db, event_type="bidder_finalized", payload=payload)
    db.commit()
    
    return resp
