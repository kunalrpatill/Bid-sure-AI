"""
Tender router — POST /tenders and GET /tenders/{id}.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Tender
from app.schemas import TenderCreate, TenderRead

router = APIRouter(prefix="/tenders", tags=["Tenders"])


@router.post("", response_model=TenderRead, status_code=status.HTTP_201_CREATED)
def create_tender(payload: TenderCreate, db: Session = Depends(get_db)):
    """Create a new GeM tender."""
    existing = db.query(Tender).filter(Tender.tender_number == payload.tender_number).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Tender with number '{payload.tender_number}' already exists.",
        )

    tender = Tender(
        tender_number=payload.tender_number,
        title=payload.title,
        category=payload.category,
    )
    db.add(tender)
    db.commit()
    db.refresh(tender)
    return tender


@router.get("/{tender_id}", response_model=TenderRead)
def get_tender(tender_id: int, db: Session = Depends(get_db)):
    """Fetch a tender by its ID."""
    tender = db.get(Tender, tender_id)
    if not tender:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tender {tender_id} not found.",
        )
    return tender
