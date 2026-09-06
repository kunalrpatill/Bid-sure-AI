import json
from datetime import datetime, timezone
import io

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy.orm import Session
from fpdf import FPDF

from app.database import get_db
from app.models import AuditLog, Bidder, Tender
from app.schemas import AuditEventMetadata, ReportResponse, ReportSummary
from app.routers.bidders import _insert_audit_log
from app.routers.officer_review import _build_review_requirements, _get_latest_evaluation

router = APIRouter(tags=["Compliance Report (Phase 5)"])


def _generate_report_data(bidder_id: int, db: Session) -> dict:
    """
    Gathers all data for the report, ensuring the bidder is finalized.
    """
    bidder = db.get(Bidder, bidder_id)
    if not bidder:
        raise HTTPException(status_code=404, detail="Bidder not found")
        
    tender = db.get(Tender, bidder.tender_id)
    
    # Check if finalized
    final_log = (
        db.query(AuditLog)
        .filter(AuditLog.event_type == "bidder_finalized")
        .filter(AuditLog.payload_json.like(f'%"bidder_id": {bidder_id},%'))
        .order_by(AuditLog.id.desc())
        .first()
    )
    
    if not final_log:
        raise HTTPException(
            status_code=400,
            detail="Bidder compliance decision has not been finalized. Cannot generate a final report."
        )
        
    final_payload = json.loads(final_log.payload_json)
    
    # We could use the payload, but let's safely rebuild the requirements state from DB 
    # to ensure full schema alignment with BidderReviewRequirement.
    eval_data = _get_latest_evaluation(bidder_id, db)
    reqs = _build_review_requirements(eval_data, db)
    
    # Summary
    pass_c = 0
    fail_c = 0
    review_c = 0
    
    for r in reqs:
        # Determine final status for this requirement based on officer decision or auto status
        req_status = r.automated_status
        if r.officer_decision:
            if r.officer_decision.action == "accept":
                req_status = "PASS"
            elif r.officer_decision.action == "reject":
                req_status = "FAIL"
            elif r.officer_decision.action == "request_clarification":
                req_status = "REVIEW"
        
        if req_status == "PASS":
            pass_c += 1
        elif req_status == "FAIL":
            fail_c += 1
        else:
            review_c += 1
            
    summary = ReportSummary(
        automated_status=final_payload.get("automated_status", "REVIEW"),
        final_status=final_payload.get("final_status", "REVIEW"),
        total_requirements=len(reqs),
        pass_count=pass_c,
        fail_count=fail_c,
        review_count=review_c
    )
    
    # Fetch relevant audit events
    audit_events = (
        db.query(AuditLog)
        .filter(AuditLog.payload_json.like(f'%"bidder_id": {bidder_id},%'))
        .order_by(AuditLog.id.asc())
        .all()
    )
    
    audit_info = [
        AuditEventMetadata(
            event_type=a.event_type,
            timestamp=a.created_at,
            hash=a.hash,
            prev_hash=a.prev_hash
        ) for a in audit_events
    ]
    
    now = datetime.now(timezone.utc)
    report_id = f"BID-{bidder_id}-REP-{int(now.timestamp())}"
    
    return {
        "report_id": report_id,
        "generated_at": now,
        "tender": {"id": tender.id, "tender_number": tender.tender_number, "title": tender.title},
        "bidder": {"id": bidder.id, "name": bidder.name},
        "summary": summary,
        "requirements": reqs,
        "audit_information": audit_info
    }


def _record_report_generation(bidder_id: int, tender_id: int, report_id: str, final_status: str, generated_at: datetime, db: Session):
    payload = {
        "event": "report_generated",
        "bidder_id": bidder_id,
        "tender_id": tender_id,
        "report_id": report_id,
        "final_status": final_status,
        "generated_at": generated_at.isoformat()
    }
    _insert_audit_log(db, event_type="report_generated", payload=payload)
    db.commit()


@router.get("/bidders/{bidder_id}/report", response_model=ReportResponse)
def get_compliance_report(bidder_id: int, db: Session = Depends(get_db)):
    """
    Returns the complete audit-ready compliance report as structured JSON.
    """
    data = _generate_report_data(bidder_id, db)
    
    _record_report_generation(
        bidder_id=data["bidder"]["id"],
        tender_id=data["tender"]["id"],
        report_id=data["report_id"],
        final_status=data["summary"].final_status,
        generated_at=data["generated_at"],
        db=db
    )
    
    return data


class PDFReport(FPDF):
    def header(self):
        self.set_font('helvetica', 'B', 15)
        self.cell(0, 10, 'BidSure AI - Compliance Report', border=0, align='C')
        self.ln(15)
        
    def footer(self):
        self.set_y(-15)
        self.set_font('helvetica', 'I', 8)
        self.cell(0, 10, f'Page {self.page_no()}', 0, 0, 'C')


@router.get("/bidders/{bidder_id}/report/download")
def download_compliance_report(bidder_id: int, db: Session = Depends(get_db)):
    """
    Returns the compliance report as a downloadable PDF.
    """
    data = _generate_report_data(bidder_id, db)
    
    pdf = PDFReport()
    pdf.add_page()
    
    # Metadata
    pdf.set_font("helvetica", "B", 12)
    pdf.cell(0, 10, "Report Metadata", ln=True)
    pdf.set_font("helvetica", "", 10)
    pdf.cell(0, 6, f"Report ID: {data['report_id']}", ln=True)
    pdf.cell(0, 6, f"Generated At: {data['generated_at'].isoformat()}", ln=True)
    pdf.ln(5)
    
    # Tender & Bidder Info
    pdf.set_font("helvetica", "B", 12)
    pdf.cell(0, 10, "Tender & Bidder Information", ln=True)
    pdf.set_font("helvetica", "", 10)
    pdf.cell(0, 6, f"Tender Number: {data['tender']['tender_number']}", ln=True)
    pdf.cell(0, 6, f"Tender Title: {data['tender']['title']}", ln=True)
    pdf.cell(0, 6, f"Bidder Name: {data['bidder']['name']}", ln=True)
    pdf.ln(5)
    
    # Summary
    pdf.set_font("helvetica", "B", 12)
    pdf.cell(0, 10, "Executive Summary", ln=True)
    pdf.set_font("helvetica", "", 10)
    summary = data["summary"]
    pdf.cell(0, 6, f"Automated Status: {summary.automated_status}", ln=True)
    pdf.cell(0, 6, f"Final Decision: {summary.final_status}", ln=True)
    pdf.cell(0, 6, f"Total Requirements: {summary.total_requirements} (Pass: {summary.pass_count}, Fail: {summary.fail_count}, Review: {summary.review_count})", ln=True)
    pdf.ln(5)
    
    # Requirements
    pdf.set_font("helvetica", "B", 12)
    pdf.cell(0, 10, "Requirement-by-Requirement Assessment", ln=True)
    pdf.set_font("helvetica", "", 9)
    
    for req in data["requirements"]:
        pdf.set_font("helvetica", "B", 10)
        pdf.cell(0, 8, f"Requirement: {req.label} (Clause: {req.clause_ref})", ln=True, border='T')
        pdf.set_font("helvetica", "", 9)
        pdf.cell(0, 6, f"Automated Result: {req.automated_status}", ln=True)
        pdf.cell(0, 6, f"Evidence Source: {req.source_doc or 'N/A'} (Page {req.source_page or 'N/A'})", ln=True)
        pdf.cell(0, 6, f"Extracted Value: {req.extracted_value or 'N/A'} (Confidence: {req.confidence or 'N/A'})", ln=True)
        pdf.multi_cell(0, 6, f"Explanation: {req.explanation}")
        
        if req.officer_decision:
            pdf.set_text_color(0, 50, 150)
            dec = req.officer_decision
            pdf.cell(0, 6, f"Officer Action: {dec.action.upper()} by {dec.officer_name} at {dec.decided_at.isoformat()}", ln=True)
            pdf.multi_cell(0, 6, f"Officer Comment: {dec.comment}")
            pdf.set_text_color(0, 0, 0)
        pdf.ln(3)
        
    # Audit Logs
    pdf.add_page()
    pdf.set_font("helvetica", "B", 12)
    pdf.cell(0, 10, "Audit Trail (SHA-256 Hash Chain)", ln=True)
    pdf.set_font("helvetica", "", 7)
    
    for audit in data["audit_information"]:
        pdf.cell(0, 5, f"Event: {audit.event_type} | Time: {audit.timestamp.isoformat()}", ln=True)
        pdf.cell(0, 5, f"Hash: {audit.hash}", ln=True)
        pdf.cell(0, 5, f"Prev: {audit.prev_hash}", ln=True)
        pdf.ln(2)

    # Output to memory
    pdf_bytes = pdf.output()
    
    # Record Audit Log
    _record_report_generation(
        bidder_id=data["bidder"]["id"],
        tender_id=data["tender"]["id"],
        report_id=data["report_id"],
        final_status=data["summary"].final_status,
        generated_at=data["generated_at"],
        db=db
    )
    
    return Response(
        content=bytes(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=BidSure_Report_{data['report_id']}.pdf"}
    )
