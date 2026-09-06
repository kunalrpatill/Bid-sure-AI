"""
seed.py — Populate the database with canonical fixture data.

Idempotent: safe to run multiple times. Checks for existing records
before inserting (by tender_number for the tender, by name for bidders).

Usage:
    python seed.py
"""
from app.database import Base, SessionLocal, engine
from app.models import Bidder, Evidence, Requirement, Tender
from datetime import datetime, timezone

# Ensure tables exist (safe to call even if they already exist)
Base.metadata.create_all(bind=engine)

# ---------------------------------------------------------------------------
# Phase 1 fixture data
# ---------------------------------------------------------------------------

TENDER = {
    "tender_number": "GEM/2026/IT-HW/00214",
    "title": "Procurement of Desktop Computers & Related IT Hardware",
    "category": "IT Hardware",
}

REQUIREMENTS = [
    {"label": "PAN / GSTIN Presence",                           "clause_ref": "Clause 3.1"},
    {"label": "Avg. Annual Turnover >= INR 50 Lakh (last 3 FY)", "clause_ref": "Clause 4.2"},
    {"label": "OEM Authorization Letter",                        "clause_ref": "Clause 5.4"},
    {"label": "Technical Parameters Match",                      "clause_ref": "Clause 6.1"},
    {"label": "Company Name Consistency",                        "clause_ref": "Clause 3.3"},
    {"label": "ISO 9001 / BIS Certification",                    "clause_ref": "Clause 5.7"},
]

# ---------------------------------------------------------------------------
# Phase 3 fixture data (Rules)
# ---------------------------------------------------------------------------

RULES = [
    {"version": "1.0", "requirement_type": "document_presence", "field": "pan_gstin", "operator": "exists", "threshold": None, "source_clause": "Clause 3.1"},
    {"version": "1.0", "requirement_type": "numeric", "field": "average_turnover", "operator": "greater_than_or_equal", "threshold": "5000000", "source_clause": "Clause 4.2"},
    {"version": "1.0", "requirement_type": "document_presence", "field": "oem_authorization", "operator": "exists", "threshold": None, "source_clause": "Clause 5.4"},
    {"version": "1.0", "requirement_type": "technical", "field": "technical_match", "operator": "equals", "threshold": "true", "source_clause": "Clause 6.1"},
    {"version": "1.0", "requirement_type": "identity", "field": "company_name_consistent", "operator": "equals", "threshold": "true", "source_clause": "Clause 3.3"},
    {"version": "1.0", "requirement_type": "certification", "field": "certification_valid", "operator": "equals", "threshold": "true", "source_clause": "Clause 5.7"},
]

# ---------------------------------------------------------------------------
# Phase 2 fixture data (updated with Phase 3 extracted_value)
# label index: 0=PAN, 1=Turnover, 2=OEM, 3=Tech, 4=Name, 5=Cert
# ---------------------------------------------------------------------------

BIDDERS_EVIDENCE = [
    {
        "name": "Alpha Technologies Pvt Ltd",
        "evidence": [
            # (req_label_index, status, confidence, note, source_doc, source_page, extracted_value)
            (0, "evidenced",  98,   "PAN_GSTIN.pdf, p.1",                          "PAN_GSTIN.pdf",    1, "true"),
            (1, "evidenced",  95,   "Audited_FS.pdf, p.4 - avg INR 62L",           "Audited_FS.pdf",   4, "6200000"),
            (2, "evidenced",  91,   "OEM_Auth.pdf, p.1",                           "OEM_Auth.pdf",     1, "true"),
            (3, "evidenced",  96,   "Technical_Bid.pdf, p.7",                      "Technical_Bid.pdf",7, "true"),
            (4, "evidenced",  99,   "Consistent across PAN, GSTIN, OEM letter",    None,               None, "true"),
            (5, "evidenced",  94,   "ISO_Cert.pdf, p.1 - valid till 2027",         "ISO_Cert.pdf",     1, "true"),
        ],
    },
    {
        "name": "Bright Systems LLP",
        "evidence": [
            (0, "evidenced",       97,   "PAN_GSTIN.pdf, p.1",                                              "PAN_GSTIN.pdf",    1, "true"),
            (1, "low_confidence",  61,   "Audited_FS.pdf, p.3 - avg INR 48L, below stated threshold",       "Audited_FS.pdf",   3, "4800000"),
            (2, "not_detected",    None, "No OEM authorization letter found in submission",                  None,               None, None),
            (3, "evidenced",       89,   "Technical_Bid.pdf, p.5",                                          "Technical_Bid.pdf",5, "true"),
            (4, "evidenced",       96,   "Consistent across documents",                                     None,               None, "true"),
            (5, "evidenced",       90,   "ISO_Cert.pdf, p.1",                                               "ISO_Cert.pdf",     1, "true"),
        ],
    },
    {
        "name": "Care Infotech",
        "evidence": [
            (0, "evidenced",       95,   "PAN_GSTIN.pdf, p.1",                                                       "PAN_GSTIN.pdf",    1, "true"),
            (1, "evidenced",       93,   "Audited_FS.pdf, p.4 - avg INR 71L",                                        "Audited_FS.pdf",   4, "7100000"),
            (2, "evidenced",       88,   "OEM_Auth.pdf, p.2",                                                        "OEM_Auth.pdf",     2, "true"),
            (3, "evidenced",       90,   "Technical_Bid.pdf, p.6",                                                   "Technical_Bid.pdf",6, "true"),
            (4, "mismatch",        82,   "PAN: 'Care Infotech Pvt Ltd' vs GSTIN: 'Care InfoTech Solutions'",         None,               None, "false"),
            (5, "low_confidence",  54,   "ISO_Cert.pdf, p.1 - expiry date partially unreadable",                     "ISO_Cert.pdf",     1, "true"),
        ],
    },
]


def seed():
    db = SessionLocal()
    try:
        # ----------------------------------------------------------------
        # Phase 1: tender + requirements (idempotent)
        # ----------------------------------------------------------------
        tender = (
            db.query(Tender)
            .filter(Tender.tender_number == TENDER["tender_number"])
            .first()
        )
        if tender:
            print(f"[seed] Tender '{TENDER['tender_number']}' already exists (id={tender.id}). Skipping tender creation.")
        else:
            tender = Tender(**TENDER)
            db.add(tender)
            db.flush()  # get tender.id

            for r in REQUIREMENTS:
                db.add(Requirement(tender_id=tender.id, **r))

            db.commit()
            db.refresh(tender)
            print(f"[seed] Created tender id={tender.id}: {tender.tender_number}")
            print(f"[seed] Inserted {len(REQUIREMENTS)} requirements.")

        # Fetch requirements ordered by id (needed to map label-index → id)
        reqs = (
            db.query(Requirement)
            .filter(Requirement.tender_id == tender.id)
            .order_by(Requirement.id)
            .all()
        )

        # ----------------------------------------------------------------
        # Phase 3: rules (idempotent)
        # ----------------------------------------------------------------
        for rule_data in RULES:
            existing_rule = (
                db.query(Rule)
                .filter(Rule.source_clause == rule_data["source_clause"])
                .first()
            )
            if not existing_rule:
                db.add(Rule(**rule_data))
        db.commit()

        # ----------------------------------------------------------------
        # Phase 2: bidders + evidence (idempotent per bidder name)
        # ----------------------------------------------------------------
        now = datetime.now(timezone.utc)

        for bidder_data in BIDDERS_EVIDENCE:
            existing_bidder = (
                db.query(Bidder)
                .filter(
                    Bidder.tender_id == tender.id,
                    Bidder.name == bidder_data["name"],
                )
                .first()
            )
            if existing_bidder:
                print(f"[seed] Bidder '{bidder_data['name']}' already exists. Skipping.")
                continue

            bidder = Bidder(tender_id=tender.id, name=bidder_data["name"])
            db.add(bidder)
            db.flush()  # get bidder.id

            for (req_idx, ev_status, confidence, note, source_doc, source_page, extracted_value) in bidder_data["evidence"]:
                req = reqs[req_idx]
                db.add(Evidence(
                    bidder_id=bidder.id,
                    requirement_id=req.id,
                    status=ev_status,
                    confidence=confidence,
                    extracted_value=extracted_value,
                    note=note,
                    source_doc=source_doc,
                    source_page=source_page,
                    extracted_at=now,
                ))

            db.commit()
            print(f"[seed] Created bidder '{bidder_data['name']}' with {len(bidder_data['evidence'])} evidence rows.")

    finally:
        db.close()


if __name__ == "__main__":
    seed()
