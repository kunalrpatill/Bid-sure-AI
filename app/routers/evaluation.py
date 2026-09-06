"""
Deterministic Rules Engine  (Phase 3)

Endpoints:
  POST /bidders/{id}/evaluate    — evaluates structured evidence against rules
  GET  /bidders/{id}/evaluation  — fetches the latest evaluation result

Design:
  - Deterministic evaluation of structured evidence (`extracted_value`).
  - No LLM decisions; strictly rule-based (exists, equals, greater_than, etc).
  - Handles null/missing evidence gracefully.
  - Overall status calculation (PASS / FAIL / REVIEW).
"""
import json
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AuditLog, Bidder, Evidence, Requirement, Rule, Tender
from app.schemas import EvaluationResponse, RequirementResult
from app.routers.bidders import _insert_audit_log

router = APIRouter(tags=["Evaluation (Phase 3)"])

# ---------------------------------------------------------------------------
# Core Rule Engine Logic
# ---------------------------------------------------------------------------

def _evaluate_rule(operator: str, threshold: str | None, value: str | None) -> bool | None:
    """
    Evaluates a single rule deterministically.
    Returns:
      True  -> PASS
      False -> FAIL
      None  -> Missing value, parsing error, or unsupported operator -> REVIEW
    """
    if operator == "exists":
        # Any evidence that reaches here (i.e. not 'not_detected') satisfies 'exists'.
        return value is not None and str(value).lower() not in ("none", "", "null")

    if value is None:
        return None  # Cannot evaluate deterministic comparison without a value

    operator = operator.lower()
    val_str = str(value).lower()
    thresh_str = str(threshold).lower() if threshold is not None else ""

    if operator == "equals":
        return val_str == thresh_str
    if operator == "not_equals":
        return val_str != thresh_str
    if operator == "contains":
        return thresh_str in val_str
    if operator == "matches":
        try:
            return re.search(str(threshold), str(value)) is not None
        except re.error:
            return None

    # Numeric comparisons
    try:
        v = float(value)
        t = float(threshold) if threshold is not None else 0.0
        if operator == "greater_than":
            return v > t
        if operator == "greater_than_or_equal":
            return v >= t
        if operator == "less_than":
            return v < t
        if operator == "less_than_or_equal":
            return v <= t
    except ValueError:
        return None  # Could not parse as numeric

    return None  # Unsupported operator


def _determine_status(
    evidence: Evidence | None,
    rule: Rule | None,
) -> tuple[str, str]:
    """
    Determines PASS, FAIL, or REVIEW and provides an explanation.
    """
    if not evidence:
        return "REVIEW", "Evidence missing. Manual review required."
    
    if not rule:
        return "REVIEW", "No automated rule defined for this requirement. Manual review required."

    if evidence.status == "not_detected":
        return "REVIEW", "Evidence not detected. Manual review required."
    
    if evidence.status == "mismatch":
        return "REVIEW", "Evidence mismatch. Manual review required to resolve conflict."

    # Evaluate against the rule
    rule_passed = _evaluate_rule(rule.operator, rule.threshold, evidence.extracted_value)

    if rule_passed is False:
        # A deterministic failure trumps low_confidence
        return "FAIL", f"Evidence deterministically failed rule ({rule.operator} {rule.threshold})."
    
    if rule_passed is None:
        return "REVIEW", "Unable to deterministically evaluate rule (missing or invalid value)."

    # Rule passed (rule_passed is True)
    if evidence.status == "low_confidence":
        return "REVIEW", "Evidence meets rule but has low confidence. Manual review required."

    return "PASS", f"Evidence was detected and deterministically satisfies the rule."


def _calculate_overall_status(results: list[RequirementResult]) -> str:
    """
    If any requirement is FAIL -> FAIL.
    Else if any requirement is REVIEW -> REVIEW.
    Else -> PASS.
    """
    statuses = [r.status for r in results]
    if "FAIL" in statuses:
        return "FAIL"
    if "REVIEW" in statuses:
        return "REVIEW"
    return "PASS"


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/bidders/{bidder_id}/evaluate",
    response_model=EvaluationResponse,
    status_code=status.HTTP_201_CREATED,
)
def evaluate_bidder(bidder_id: int, db: Session = Depends(get_db)):
    bidder = db.get(Bidder, bidder_id)
    if not bidder:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Bidder {bidder_id} not found.",
        )

    # Fetch tender and requirements
    requirements = db.query(Requirement).filter(Requirement.tender_id == bidder.tender_id).all()
    if not requirements:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tender has no requirements to evaluate.",
        )

    # Fetch evidence for this bidder
    evidence_list = db.query(Evidence).filter(Evidence.bidder_id == bidder_id).all()
    ev_map = {e.requirement_id: e for e in evidence_list}

    # Fetch all rules (this matches on clause_ref, which acts as our domain linkage)
    clause_refs = {r.clause_ref for r in requirements}
    rules = db.query(Rule).filter(Rule.source_clause.in_(clause_refs)).all()
    rule_map = {r.source_clause: r for r in rules}

    # Evaluate each requirement
    results: list[RequirementResult] = []
    
    for req in requirements:
        ev = ev_map.get(req.id)
        rule = rule_map.get(req.clause_ref)

        req_status, explanation = _determine_status(evidence=ev, rule=rule)

        results.append(
            RequirementResult(
                requirement_id=req.id,
                requirement_label=req.label,
                clause_ref=req.clause_ref,
                rule_id=rule.id if rule else None,
                rule_version=rule.version if rule else None,
                status=req_status,
                confidence=ev.confidence if ev else None,
                extracted_value=ev.extracted_value if ev else None,
                threshold=rule.threshold if rule else None,
                operator=rule.operator if rule else None,
                explanation=explanation,
                source_doc=ev.source_doc if ev else None,
                source_page=ev.source_page if ev else None,
                evidence_id=ev.id if ev else None,
            )
        )

    overall_status = _calculate_overall_status(results)
    now = datetime.now(timezone.utc)

    resp = EvaluationResponse(
        bidder_id=bidder.id,
        bidder=bidder.name,
        tender_id=bidder.tender_id,
        overall_status=overall_status,
        requirement_results=results,
        evaluated_at=now,
    )

    # Insert into Audit Log
    payload = json.loads(resp.model_dump_json())
    payload["event"] = "bidder_evaluated"
    
    _insert_audit_log(db, event_type="bidder_evaluated", payload=payload)
    db.commit()

    return resp


@router.get(
    "/bidders/{bidder_id}/evaluation",
    response_model=EvaluationResponse,
)
def get_evaluation(bidder_id: int, db: Session = Depends(get_db)):
    """
    Returns the latest evaluation result from the audit log.
    """
    bidder = db.get(Bidder, bidder_id)
    if not bidder:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Bidder {bidder_id} not found.",
        )

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
            detail="No evaluation found for this bidder. Run POST /bidders/{id}/evaluate first.",
        )

    return json.loads(log.payload_json)
