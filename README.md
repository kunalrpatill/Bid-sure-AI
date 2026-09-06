# BidSure AI — Phase 4 Backend

**Evidence-first GeM bid compliance verification** (SIH26100 hackathon prototype).

## Project Structure

```
bidsureAI/
├── app/
│   ├── __init__.py
│   ├── main.py          # FastAPI app + lifespan (table creation)
│   ├── database.py      # SQLite engine, session factory, get_db dependency
│   ├── models.py        # All SQLAlchemy ORM models (Phases 1–N schema)
│   ├── schemas.py       # Pydantic v2 request/response schemas
│   └── routers/
│       ├── __init__.py
│       ├── tenders.py       # POST /tenders, GET /tenders/{id}
│       ├── requirements.py  # Bulk-create, list, approve + audit log
│       ├── bidders.py       # Bidders + evidence ingestion (Phase 2)
│       ├── evaluation.py    # Deterministic rules engine (Phase 3)
│       ├── officer_review.py # Officer Review & Final Decision (Phase 4)
│       └── audit_log.py     # GET /audit-log
├── tests/
│   ├── __init__.py
│   └── test_audit_chain.py  # pytest: 40 tests across Phases 1-4
├── seed.py              # Canonical fixture data (idempotent)
├── requirements.txt
└── README.md
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/tenders` | Create a tender |
| `GET`  | `/tenders/{id}` | Fetch a tender |
| `POST` | `/tenders/{id}/requirements` | Bulk-create requirements |
| `GET`  | `/tenders/{id}/requirements` | List requirements |
| `POST` | `/requirements/{id}/approve` | Officer approves a requirement |
| `GET`  | `/audit-log` | Hash-chain audit log |
| `POST` | `/tenders/{id}/bidders` | **[Phase 2]** Create a bidder |
| `GET`  | `/tenders/{id}/bidders` | **[Phase 2]** List bidders |
| `POST` | `/bidders/{id}/evidence` | **[Phase 2]** Bulk-ingest evidence |
| `GET`  | `/bidders/{id}/evidence` | **[Phase 2]** List evidence |
| `POST` | `/bidders/{id}/evaluate` | **[Phase 3]** Evaluate bidder evidence against rules |
| `GET`  | `/bidders/{id}/evaluation`| **[Phase 3]** Fetch latest evaluation result |
| `GET`  | `/bidders/{id}/review` | **[Phase 4]** Fetch combined evaluation and officer review state |
| `POST` | `/evidence/{evidence_id}/decision` | **[Phase 4]** Submit an officer decision (`accept`, `reject`, `request_clarification`) |
| `POST` | `/bidders/{id}/finalize` | **[Phase 4]** Finalize overall compliance status |

| `GET`  | `/bidders/{id}/report` | **[Phase 5]** Generate structured JSON compliance report |
| `GET`  | `/bidders/{id}/report/download`| **[Phase 5]** Download compliance report as PDF |
| `POST` | `/tenders/{id}/documents` | **[Phase 6]** Upload a PDF document for a tender |
| `POST` | `/bidders/{id}/documents` | **[Phase 6]** Upload a PDF document for a bidder |
| `POST` | `/bidders/{id}/process-documents` | **[Phase 6]** Extract text, identify evidence, route to rules engine |

---

## Phase 6: Real Document Processing & Frontend Integration

Phase 6 connects the backend to the frontend and enables real end-to-end document processing for the SIH prototype.

1. **Document Ingestion (`POST .../documents`)**:
    - Safely uploads PDFs, validating file types and preventing path traversal.
    - Extracts page counts and tracks file provenance centrally.
2. **Deterministic Evidence Extraction (`POST .../process-documents`)**:
    - Leverages `pypdf` to extract text while maintaining critical page-boundary provenance.
    - Evaluates the text against the active requirements (e.g., matching PAN formats, turnover values, and OEM language).
    - If a requirement cannot be found or is ambiguous, it correctly generates a `not_detected` or `low_confidence` evidence record rather than fabricating data.
3. **Frontend Integration**:
    - Configured local CORS (`http://localhost:3000`, `http://127.0.0.1:3000`) for seamless React/Vue frontend API consumption.
    - All workflows map cleanly from the existing BidSure UI directly to these endpoints without requiring UI rewrites.

---

### 1 — Create and activate a virtual environment

```bash
# Windows (PowerShell)
python -m venv .venv
.venv\Scripts\Activate.ps1

# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate
```

### 2 — Install dependencies

```bash
pip install -r requirements.txt
```

### 3 — Start the server

```bash
uvicorn app.main:app --reload
```

The first startup creates **`bidsure.db`** (SQLite file) with all tables.

Interactive docs: http://127.0.0.1:8000/docs

### 4 — Seed the database

In a second terminal (with the venv active):

```bash
python seed.py
```

This inserts the canonical tender `GEM/2026/IT-HW/00214`, its 6 requirements,
and 3 bidders (Alpha Technologies, Bright Systems LLP, Care Infotech) with 6
evidence rows each. **Idempotent** — running it again skips records that already exist.

### 5 — Run tests

```bash
pytest -v
```

Tests use a **file-based test SQLite** database — no server needs to be running.
All 19 tests should pass.

---

## Key Design Decisions

### Append-only tables

`officer_decisions` and `audit_log` are **insert-only**. There is no `UPDATE` or
`DELETE` logic anywhere in the codebase for these tables.

### Hash-chained audit log

Both approvals (Phase 1) and evidence ingestion (Phase 2) write to the **same
hash chain**:

```
hash = sha256(prev_hash || canonical_json(event_payload))
```

- Genesis `prev_hash = "0" * 64`.
- Every write commits atomically with its business-logic row in one transaction.
- `event_type` distinguishes `requirement_approved` from `evidence_ingested`.

### Evidence validation rules (enforced by Pydantic)

| Rule | Where enforced |
|------|---------------|
| `status` must be one of `evidenced`, `low_confidence`, `not_detected`, `mismatch` | `EvidenceStatus` enum → auto 422 |
| `confidence` must be **null** when `status == not_detected` | `model_validator` in `EvidenceCreate` |
| `confidence` must be **0–100** for all other statuses | `model_validator` in `EvidenceCreate` |
| Must supply exactly **one row per requirement** on the tender | Router-level check (needs DB) |
| No duplicate `requirement_id` in one request | Router-level check |

### `GET /bidders/{id}/evidence` — self-contained response

Returns `requirement_label` and `requirement_clause_ref` joined from the
`requirements` table, so the frontend can render a full compliance grid
without a second API call.

### Future phases (out of scope)

- Rules engine (the `rules` table is already in the schema)
- Officer review (`officer_decisions` table is already in the schema)
- Report generation

---

## Example API Usage

### Create a tender

```bash
curl -X POST http://127.0.0.1:8000/tenders \
  -H "Content-Type: application/json" \
  -d '{"tender_number":"GEM/TEST/001","title":"Test Tender","category":"IT"}'
```

### Bulk-create requirements

```bash
curl -X POST http://127.0.0.1:8000/tenders/1/requirements \
  -H "Content-Type: application/json" \
  -d '{"requirements":[{"label":"PAN / GSTIN Presence","clause_ref":"Clause 3.1"}]}'
```

### Officer approval

```bash
curl -X POST http://127.0.0.1:8000/requirements/1/approve \
  -H "Content-Type: application/json" \
  -d '{"officer_name":"Officer Singh"}'
```

### Create a bidder

```bash
curl -X POST http://127.0.0.1:8000/tenders/1/bidders \
  -H "Content-Type: application/json" \
  -d '{"name":"Alpha Technologies Pvt Ltd"}'
```

### Bulk-ingest evidence

```bash
curl -X POST http://127.0.0.1:8000/bidders/1/evidence \
  -H "Content-Type: application/json" \
  -d '{
    "evidence": [
      {"requirement_id":1,"status":"evidenced","confidence":98,"note":"PAN_GSTIN.pdf p.1","source_doc":"PAN_GSTIN.pdf","source_page":1},
      {"requirement_id":2,"status":"not_detected","confidence":null,"note":"Not found"}
    ]
  }'
```

### View evidence with joined requirement fields

```bash
curl http://127.0.0.1:8000/bidders/1/evidence
```

### Verify audit log

```bash
curl http://127.0.0.1:8000/audit-log
```
