"""
Audit log router — GET /audit-log.

Returns all audit log entries in ascending ID order so the caller can
walk the hash chain for verification.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AuditLog
from app.schemas import AuditLogRead

router = APIRouter(prefix="/audit-log", tags=["Audit Log"])


@router.get("", response_model=list[AuditLogRead])
def list_audit_log(db: Session = Depends(get_db)):
    """
    Return all audit log entries ordered by insertion sequence.
    Use this endpoint to verify the hash chain:
      - entries[0].prev_hash == "0" * 64
      - entries[n].prev_hash == entries[n-1].hash  for n >= 1
    """
    return db.query(AuditLog).order_by(AuditLog.id.asc()).all()
