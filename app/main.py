"""
BidSure AI — FastAPI application entry point.

Phase 1 routes:
  POST /tenders
  GET  /tenders/{id}
  POST /tenders/{id}/requirements
  GET  /tenders/{id}/requirements
  POST /requirements/{id}/approve
  GET  /audit-log

Phase 2 routes:
  POST /tenders/{id}/bidders
  GET  /tenders/{id}/bidders
  POST /bidders/{id}/evidence
  GET  /bidders/{id}/evidence
"""
from contextlib import asynccontextmanager
from sqlalchemy import text
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import Base, engine
from app.routers import audit_log, requirements, tenders
from app.routers import bidders, evaluation, officer_review, report, documents


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create all tables on startup (idempotent — safe to call every time).
    Base.metadata.create_all(bind=engine)
    
    # Phase 3: Small additive schema change for structured evidence evaluation
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE evidence ADD COLUMN extracted_value VARCHAR(500)"))
    except Exception:
        pass  # Column already exists
    
    yield


app = FastAPI(
    title="BidSure AI — Phase 6",
    description=(
        "Evidence-first GeM bid compliance verification tool. "
        "Phase 1: Tenders, Requirements, Officer Approval, Audit Log. "
        "Phase 2: Bidders & Evidence Ingestion. "
        "Phase 3: Deterministic Rules Engine. "
        "Phase 4: Officer Review & Final Decision. "
        "Phase 5: Audit-Ready Compliance Report. "
        "Phase 6: Real Document Processing & Frontend Integration."
    ),
    version="0.6.0",
    lifespan=lifespan,
)

# Phase 6: Frontend CORS Integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(tenders.router)
app.include_router(requirements.router)
app.include_router(documents.router)
app.include_router(bidders.router)
app.include_router(evaluation.router)
app.include_router(officer_review.router)
app.include_router(report.router)
app.include_router(audit_log.router)


@app.get("/", tags=["Health"])
def root():
    return {"status": "ok", "service": "BidSure AI", "phase": 6}
