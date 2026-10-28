"""
KiraayaShield Backend — FastAPI application.

Provides:
  - Health check with MongoDB connectivity status
  - CRUD for Cases (create, list, get, update)
  - Evidence metadata (create, list by case)
  - Legacy /api/analyze-lease compatibility endpoint

Run with:
    uvicorn backend_python.main:app --reload --port 8000
"""

import os
import shutil
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from bson import ObjectId
from bson.errors import InvalidId
from pydantic import BaseModel
from pymongo import ReturnDocument
from pymongo.errors import PyMongoError

from backend_python.database import (
    check_connection,
    close_client,
    ensure_indexes,
    get_collection,
)
from backend_python.models import (
    CaseCreate,
    CaseResponse,
    CaseUpdate,
    EvidenceCreate,
    EvidenceResponse,
    AssessmentRequest,
    AssessmentResult,
    InvestigationRequest,
    InvestigationResult,
    DocumentExtractResponse,
    CopilotChatRequest,
    CopilotChatResponse,
)
from backend_python.groq_service import (
    analyze_lease_with_groq,
    chat_with_copilot,
    GroqServiceError,
)
from backend_python.tinyfish_service import (
    benchmark_hyperlocal_deductions,
    search_web_tinyfish,
    fetch_page_tinyfish,
)
from backend_python.document_service import extract_and_validate_document, classify_document
from backend_python.evidence_service import extract_real_metadata, store_evidence_file, compute_sha256

load_dotenv()

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger("kiraayashield")

# ---------------------------------------------------------------------------
# Lifespan: startup / shutdown
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown."""
    logger.info("KiraayaShield backend starting …")
    ensure_indexes()
    yield
    logger.info("KiraayaShield backend shutting down …")
    close_client()


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(
    title="KiraayaShield API",
    version="2.5.0",
    description="Evidence-driven rental security deposit deduction assessment platform",
    lifespan=lifespan,
)

# CORS — allow the configured frontend origins (default: localhost:3000 and 127.0.0.1:3000)
cors_env = os.getenv("CORS_ORIGIN", "http://localhost:3000,http://127.0.0.1:3000")
allowed_origins = [orig.strip() for orig in cors_env.split(",") if orig.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _oid(raw: str) -> ObjectId:
    """Parse a string as an ObjectId or raise 400."""
    try:
        return ObjectId(raw)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=400, detail=f"Invalid ID format: {raw}")


def _case_doc_to_response(doc: dict) -> dict:
    """Convert a MongoDB case document to a JSON-serialisable dict."""
    doc["id"] = str(doc.pop("_id"))
    doc.setdefault("title", "Untitled Case")
    doc.setdefault("tenant_name", "")
    doc.setdefault("landlord_name", "")
    doc.setdefault("property_address", "")
    doc.setdefault("city", "")
    doc.setdefault("monthly_rent", 0.0)
    doc.setdefault("deposit_paid", 0.0)
    doc.setdefault("deductions_claimed", [])
    doc.setdefault("upi_reference", "")
    doc.setdefault("vacate_date", None)
    doc.setdefault("lease_text", "")
    doc.setdefault("status", "draft")
    doc.setdefault("is_demo", False)
    doc.setdefault("created_at", _utcnow())
    doc.setdefault("updated_at", _utcnow())
    return doc


def _evidence_doc_to_response(doc: dict) -> dict:
    """Convert a MongoDB evidence document to a JSON-serialisable dict."""
    doc["id"] = str(doc.pop("_id"))
    doc["case_id"] = str(doc["case_id"])
    doc.setdefault("file_path", None)
    return doc


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
@app.get("/health")
def health_check():
    db_status = check_connection()
    return {
        "status": "healthy",
        "service": "KiraayaShield-API",
        "version": "2.5.0",
        "timestamp": _utcnow().isoformat(),
        "database": db_status,
    }


# ---------------------------------------------------------------------------
# Cases CRUD
# ---------------------------------------------------------------------------

@app.post("/api/v1/cases", status_code=201)
def create_case(body: CaseCreate):
    """Create a new case and persist it in MongoDB."""
    now = _utcnow()
    doc = {
        "title": body.title,
        "tenant_name": body.tenant_name,
        "landlord_name": body.landlord_name,
        "property_address": body.property_address,
        "city": body.city,
        "monthly_rent": body.monthly_rent,
        "deposit_paid": body.deposit_paid,
        "deductions_claimed": [d.model_dump(by_alias=False) for d in body.deductions_claimed],
        "upi_reference": body.upi_reference,
        "vacate_date": body.vacate_date,
        "lease_text": body.lease_text,
        "status": "draft",
        "is_demo": False,
        "created_at": now,
        "updated_at": now,
    }
    try:
        result = get_collection("cases").insert_one(doc)
    except PyMongoError as exc:
        logger.error("Failed to create case: %s", exc)
        raise HTTPException(status_code=503, detail="Database unavailable")

    doc["_id"] = result.inserted_id
    resp = _case_doc_to_response(doc)
    return CaseResponse(**resp).model_dump(by_alias=True)


@app.get("/api/v1/cases")
def list_cases(
    status: Optional[str] = Query(None, description="Filter by case status"),
    limit: int = Query(50, ge=1, le=200),
    skip: int = Query(0, ge=0),
):
    """List cases, newest first. Optionally filter by status."""
    query: dict = {}
    if status:
        allowed = {"draft", "in_progress", "assessed", "resolved"}
        if status not in allowed:
            raise HTTPException(status_code=400, detail=f"status must be one of {allowed}")
        query["status"] = status

    try:
        cursor = (
            get_collection("cases")
            .find(query)
            .sort("created_at", -1)
            .skip(skip)
            .limit(limit)
        )
        cases = []
        for doc in cursor:
            resp = _case_doc_to_response(doc)
            cases.append(CaseResponse(**resp).model_dump(by_alias=True))
        return {"cases": cases, "count": len(cases)}
    except PyMongoError as exc:
        logger.error("Failed to list cases: %s", exc)
        raise HTTPException(status_code=503, detail="Database unavailable")


@app.get("/api/v1/cases/{case_id}")
def get_case(case_id: str):
    """Retrieve a single case by ID."""
    oid = _oid(case_id)
    try:
        doc = get_collection("cases").find_one({"_id": oid})
    except PyMongoError as exc:
        logger.error("Failed to get case %s: %s", case_id, exc)
        raise HTTPException(status_code=503, detail="Database unavailable")

    if doc is None:
        raise HTTPException(status_code=404, detail="Case not found")

    resp = _case_doc_to_response(doc)
    return CaseResponse(**resp).model_dump(by_alias=True)


@app.patch("/api/v1/cases/{case_id}")
def update_case(case_id: str, body: CaseUpdate):
    """Update mutable fields of a case."""
    oid = _oid(case_id)
    updates: dict = {}
    for field_name, value in body.model_dump(exclude_unset=True, by_alias=False).items():
        if field_name == "deductions_claimed" and value is not None:
            updates["deductions_claimed"] = [
                d.model_dump(by_alias=False) if hasattr(d, "model_dump") else d
                for d in value
            ]
        else:
            updates[field_name] = value

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    updates["updated_at"] = _utcnow()

    try:
        result = get_collection("cases").find_one_and_update(
            {"_id": oid},
            {"$set": updates},
            return_document=ReturnDocument.AFTER,
        )
    except PyMongoError as exc:
        logger.error("Failed to update case %s: %s", case_id, exc)
        raise HTTPException(status_code=503, detail="Database unavailable")

    if result is None:
        raise HTTPException(status_code=404, detail="Case not found")

    resp = _case_doc_to_response(result)
    return CaseResponse(**resp).model_dump(by_alias=True)


@app.delete("/api/v1/cases/{case_id}", status_code=200)
def delete_case(case_id: str):
    """Delete a case and its associated evidence records and disk files."""
    oid = _oid(case_id)
    try:
        result = get_collection("cases").delete_one({"_id": oid})
        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Case not found")
        # Clean up associated evidence records
        get_collection("evidence").delete_many({"case_id": oid})
        # Clean up associated investigation records
        get_collection("investigations").delete_many({"case_id": case_id})
        # Clean up associated assessment records
        get_collection("assessments").delete_many({"case_id": oid})
        # Clean up uploaded files on disk if folder exists
        case_uploads = os.path.join(PROJECT_ROOT, "uploads", case_id)
        if os.path.exists(case_uploads):
            shutil.rmtree(case_uploads, ignore_errors=True)
        return {"status": "success", "message": f"Case {case_id} and associated evidence deleted successfully"}
    except HTTPException:
        raise
    except PyMongoError as exc:
        logger.error("Failed to delete case %s: %s", case_id, exc)
        raise HTTPException(status_code=503, detail="Database unavailable")


# ---------------------------------------------------------------------------
# Evidence metadata & Real File Upload (Priority 2)
# ---------------------------------------------------------------------------

@app.post("/api/v1/cases/{case_id}/evidence/upload", status_code=201)
async def upload_case_evidence(
    case_id: str,
    file: UploadFile = File(...),
    description: str = Form(""),
    evidence_type: str = Form("photo"),
):
    """
    Upload an actual evidence file (photo/receipt/document), calculate true SHA-256
    hash, extract real EXIF metadata without fabricating data, save binary to disk,
    and persist metadata record to database.
    """
    oid = _oid(case_id)
    try:
        case = get_collection("cases").find_one({"_id": oid}, {"_id": 1})
    except PyMongoError as exc:
        logger.error("Failed to verify case %s: %s", case_id, exc)
        raise HTTPException(status_code=503, detail="Database unavailable")

    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")

    contents = await file.read()
    filename = file.filename or "evidence_file"
    rel_path, sha256_hash = store_evidence_file(contents, filename, case_id)
    metadata = extract_real_metadata(contents, filename)

    now = _utcnow()
    doc = {
        "case_id": oid,
        "evidence_type": evidence_type,
        "filename": filename,
        "file_hash": sha256_hash,
        "file_path": rel_path,
        "description": description,
        "metadata": metadata,
        "created_at": now,
    }

    try:
        result = get_collection("evidence").insert_one(doc)
    except PyMongoError as exc:
        logger.error("Failed to add evidence: %s", exc)
        raise HTTPException(status_code=503, detail="Database unavailable")

    doc["_id"] = result.inserted_id
    resp = _evidence_doc_to_response(doc)
    return EvidenceResponse(**resp).model_dump(by_alias=True)


@app.post("/api/v1/evidence/inspect")
async def inspect_evidence_standalone(file: UploadFile = File(...)):
    """
    Probe and inspect uploaded evidence file without requiring a pre-saved case.
    Calculates true SHA-256 hash and real EXIF tags using Pillow.
    """
    contents = await file.read()
    filename = file.filename or "evidence_file"
    metadata = extract_real_metadata(contents, filename)
    return metadata


@app.post("/api/v1/cases/{case_id}/evidence", status_code=201)
def add_evidence(case_id: str, body: EvidenceCreate):
    """Add evidence metadata to a case."""
    oid = _oid(case_id)

    # Verify the case exists
    try:
        case = get_collection("cases").find_one({"_id": oid}, {"_id": 1})
    except PyMongoError as exc:
        logger.error("Failed to verify case %s: %s", case_id, exc)
        raise HTTPException(status_code=503, detail="Database unavailable")

    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")

    now = _utcnow()
    doc = {
        "case_id": oid,
        "evidence_type": body.evidence_type,
        "filename": body.filename,
        "description": body.description,
        "file_hash": body.file_hash,
        "metadata": body.metadata,
        "created_at": now,
    }

    try:
        result = get_collection("evidence").insert_one(doc)
    except PyMongoError as exc:
        logger.error("Failed to add evidence: %s", exc)
        raise HTTPException(status_code=503, detail="Database unavailable")

    doc["_id"] = result.inserted_id
    resp = _evidence_doc_to_response(doc)
    return EvidenceResponse(**resp).model_dump(by_alias=True)


@app.get("/api/v1/cases/{case_id}/evidence")
def list_evidence(case_id: str):
    """List all evidence metadata for a case."""
    oid = _oid(case_id)
    try:
        cursor = get_collection("evidence").find({"case_id": oid}).sort("created_at", -1)
        items = []
        for doc in cursor:
            resp = _evidence_doc_to_response(doc)
            items.append(EvidenceResponse(**resp).model_dump(by_alias=True))
        return {"evidence": items, "count": len(items)}
    except PyMongoError as exc:
        logger.error("Failed to list evidence: %s", exc)
        raise HTTPException(status_code=503, detail="Database unavailable")


# ---------------------------------------------------------------------------
# Document Text Extraction & Classification (Priority 1)
# ---------------------------------------------------------------------------

@app.post("/api/v1/documents/extract", response_model=DocumentExtractResponse)
async def extract_document_endpoint(file: UploadFile = File(...)):
    """
    Extract text and validate whether an uploaded PDF or TXT document is a genuine lease.
    Detects and rejects academic tutorials, image-only/scanned PDFs, or unrelated documents.
    """
    contents = await file.read()
    filename = file.filename or "uploaded_document"
    return extract_and_validate_document(contents, filename)


# ---------------------------------------------------------------------------
# Groq LLM Lease Analysis Endpoints (Phase 2 & Priority 1 Classification)
# ---------------------------------------------------------------------------

class LeaseAnalysisInput(BaseModel):
    leaseText: str = ""
    monthlyRent: float = 18000
    depositPaid: float = 54000
    city: str = "Noida"
    filename: Optional[str] = "Uploaded Document"
    deductions: Optional[List[dict]] = None


@app.post("/api/analyze-lease")
def analyze_lease_endpoint(body: LeaseAnalysisInput):
    """
    Live AI legal lease deduction assessment using Groq (openai/gpt-oss-120b).
    First verifies the input is a genuine lease; rejects academic tutorials or unrelated files.
    """
    raw_text = (body.leaseText or "").strip()
    if raw_text and len(raw_text) > 30:
        is_lease, doc_type, rejection_reason = classify_document(raw_text, body.filename or "Uploaded Lease")
        if not is_lease:
            return {
                "status": "rejected",
                "isLease": False,
                "documentType": doc_type,
                "rejectionReason": rejection_reason,
                "summary": f"Assessment Halted: The provided document was classified as an '{doc_type}', not a residential rental agreement.",
                "findings": [],
                "caseReadinessScore": 0.0,
                "riskScore": 0.0,
                "recoverableEstimate": 0.0,
                "categoryCounts": {},
                "missingEvidenceSummary": ["A valid residential lease agreement"],
                "recommendedNextSteps": ["Upload a genuine residential tenancy agreement containing landlord, tenant, and rent terms."],
                "disclaimer": "This document was rejected by KiraayaShield because it is not a supported residential rental agreement."
            }

    res: AssessmentResult = analyze_lease_with_groq(
        lease_text=body.leaseText,
        monthly_rent=body.monthlyRent,
        deposit_paid=body.depositPaid,
        city=body.city,
        deductions=body.deductions or [],
    )

    resp_dict = res.model_dump(by_alias=True)
    resp_dict["status"] = "success"
    resp_dict["isLease"] = True
    resp_dict["engine"] = "GroqCloud-openai/gpt-oss-120b"
    resp_dict["city"] = body.city
    return resp_dict


@app.post("/api/v1/cases/{case_id}/analyze")
def analyze_case_by_id(case_id: str):
    """
    Run Groq LLM assessment for a persistent case stored in MongoDB.
    Saves assessment to assessments collection and updates case status to 'assessed'.
    """
    oid = _oid(case_id)
    try:
        case_doc = get_collection("cases").find_one({"_id": oid})
    except PyMongoError as exc:
        logger.error("Failed to fetch case %s: %s", case_id, exc)
        raise HTTPException(status_code=503, detail="Database unavailable")

    if not case_doc:
        raise HTTPException(status_code=404, detail="Case not found")

    lease_text = case_doc.get("lease_text", "")
    if lease_text and len(lease_text.strip()) > 30:
        is_lease, doc_type, rejection_reason = classify_document(lease_text, "Case Lease Document")
        if not is_lease:
            return {
                "status": "rejected",
                "isLease": False,
                "documentType": doc_type,
                "rejectionReason": rejection_reason,
                "summary": f"Assessment Halted: Stored document was classified as an '{doc_type}', not a residential rental agreement.",
                "findings": [],
                "caseReadinessScore": 0.0,
                "riskScore": 0.0,
                "recoverableEstimate": 0.0,
                "categoryCounts": {},
                "missingEvidenceSummary": ["A valid residential lease agreement"],
                "recommendedNextSteps": ["Upload a genuine residential tenancy agreement containing landlord, tenant, and rent terms."],
                "disclaimer": "This case was not assessed because its document text is not a supported residential rental agreement."
            }

    res: AssessmentResult = analyze_lease_with_groq(
        lease_text=lease_text,
        monthly_rent=float(case_doc.get("monthly_rent", 18000)),
        deposit_paid=float(case_doc.get("deposit_paid", 54000)),
        city=case_doc.get("city", "Noida"),
        deductions=case_doc.get("deductions_claimed", []),
        case_id=case_id,
    )

    assessment_data = res.model_dump(by_alias=True)
    assessment_doc = {
        "case_id": oid,
        "summary": res.summary,
        "case_readiness_score": res.case_readiness_score,
        "risk_score": res.risk_score,
        "recoverable_estimate": res.recoverable_estimate,
        "findings": [f.model_dump(by_alias=True) for f in res.findings],
        "category_counts": res.category_counts,
        "missing_evidence_summary": res.missing_evidence_summary,
        "recommended_next_steps": res.recommended_next_steps,
        "disclaimer": res.disclaimer,
        "created_at": _utcnow(),
    }

    try:
        ins = get_collection("assessments").insert_one(assessment_doc)
        assessment_data["id"] = str(ins.inserted_id)
        # Mark case as assessed
        get_collection("cases").update_one(
            {"_id": oid},
            {"$set": {"status": "assessed", "updated_at": _utcnow()}}
        )
    except PyMongoError as exc:
        logger.warning("Could not persist assessment: %s", exc)

    assessment_data["status"] = "success"
    return assessment_data


@app.get("/api/v1/cases/{case_id}/assessment")
def get_latest_assessment(case_id: str):
    """Retrieve the latest assessment for a case."""
    oid = _oid(case_id)
    try:
        doc = get_collection("assessments").find_one({"case_id": oid}, sort=[("created_at", -1)])
    except PyMongoError as exc:
        logger.error("Failed to get assessment for %s: %s", case_id, exc)
        raise HTTPException(status_code=503, detail="Database unavailable")

    if not doc:
        raise HTTPException(status_code=404, detail="No assessment found for this case")

    doc["id"] = str(doc.pop("_id"))
    doc["caseId"] = str(doc.pop("case_id"))
    return doc


# ---------------------------------------------------------------------------
# TinyFish Web Research & Investigation Endpoints (Phase 3)
# ---------------------------------------------------------------------------

class ScrapeRatesInput(BaseModel):
    city: str = "Noida"
    items: Optional[List[dict]] = None


@app.post("/api/scrape-rates")
def scrape_rates_endpoint(body: ScrapeRatesInput):
    """
    Hyperlocal market rates investigation using TinyFish Search & Fetch APIs.
    Fetches real verified prices from Urban Company, NoBroker, and standard rate cards.
    """
    res: InvestigationResult = benchmark_hyperlocal_deductions(
        city=body.city,
        claimed_items=body.items,
    )

    benchmarks = []
    for it in res.items:
        benchmarks.append({
            "item": it.item,
            "landlordClaim": it.landlord_claim,
            "urbanCompanyRate": it.fair_market_rate,
            "localVendorAvg": round(it.fair_market_rate * 0.8),
            "wearableTearExempt": it.wear_and_tear_exempt,
            "disallowedAmount": it.disallowed_amount,
            "rateRange": it.rate_range,
            "source": it.source_name,
            "sourceUrl": it.source_url,
            "snippet": it.snippet,
        })

    return {
        "status": "success",
        "scrapedAt": res.created_at.isoformat(),
        "location": res.city,
        "sourcesQueried": res.sources_queried,
        "totalLandlordClaim": res.total_landlord_claim,
        "totalFairDeduction": res.total_fair_deduction,
        "totalDisallowedAmount": res.total_disallowed_amount,
        "benchmarks": benchmarks,
    }


@app.post("/api/v1/cases/{case_id}/investigate")
def investigate_case_by_id(case_id: str):
    """
    Run TinyFish hyperlocal rate investigation for the deduction items of a stored case.
    """
    oid = _oid(case_id)
    try:
        case_doc = get_collection("cases").find_one({"_id": oid})
    except PyMongoError as exc:
        logger.error("Failed to fetch case %s: %s", case_id, exc)
        raise HTTPException(status_code=503, detail="Database unavailable")

    if not case_doc:
        raise HTTPException(status_code=404, detail="Case not found")

    city = case_doc.get("city", "Noida") or "Noida"
    deductions = case_doc.get("deductions_claimed", [])

    res: InvestigationResult = benchmark_hyperlocal_deductions(
        city=city,
        claimed_items=deductions,
        case_id=case_id,
    )
    return res.model_dump(by_alias=True)


@app.get("/api/v1/cases/{case_id}/investigations")
def get_case_investigations(case_id: str):
    """Retrieve investigation records for a case."""
    oid = _oid(case_id)
    try:
        cursor = get_collection("investigations").find({"case_id": case_id}).sort("created_at", -1)
        items = []
        for doc in cursor:
            doc["id"] = str(doc.pop("_id"))
            items.append(doc)
        return {"investigations": items, "count": len(items)}
    except PyMongoError as exc:
        logger.error("Failed to get investigations for %s: %s", case_id, exc)
        raise HTTPException(status_code=503, detail="Database unavailable")


# ---------------------------------------------------------------------------
# Legacy compatibility endpoint — /api/generate-complaint
# ---------------------------------------------------------------------------

class LegacyComplaintRequest(BaseModel):
    preset: str = ""
    deposit: float = 0


@app.post("/api/generate-complaint")
def legacy_generate_complaint(body: LegacyComplaintRequest):
    """Legacy-compatible complaint stub (no real filing)."""
    import random
    ticket_id = f"NCH-2026-{random.randint(100000, 999999)}"
    return {
        "status": "success",
        "ticketId": ticket_id,
        "generatedAt": _utcnow().isoformat(),
    }


# ---------------------------------------------------------------------------
# AI Copilot Chat Endpoint (Groq openai/gpt-oss-120b)
# ---------------------------------------------------------------------------

@app.post("/api/copilot/chat", response_model=CopilotChatResponse)
async def copilot_chat_endpoint(req: CopilotChatRequest):
    """
    Live conversational legal assistant for KiraayaShield powered by Groq.
    Tailors responses to active case context when present, or provides statutory
    guidance without fabricating landlord/case details.
    """
    message = req.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message cannot be empty.")

    case_context = req.case_context or None

    # If caseId was passed, enrich caseContext from database
    if req.case_id:
        try:
            oid = _oid(req.case_id)
            case_doc = get_collection("cases").find_one({"_id": oid})
            if case_doc:
                db_context = {
                    "tenantName": case_doc.get("tenant_name", ""),
                    "landlordName": case_doc.get("landlord_name", ""),
                    "city": case_doc.get("city", ""),
                    "monthlyRent": case_doc.get("monthly_rent", 0.0),
                    "depositPaid": case_doc.get("deposit_paid", 0.0),
                    "status": case_doc.get("status", "draft"),
                }
                if case_context:
                    db_context.update(case_context)
                case_context = db_context
        except Exception as exc:
            logger.warning("Could not fetch case context for %s: %s", req.case_id, exc)

    history_dicts = [m.model_dump() for m in req.history] if req.history else []

    try:
        res = chat_with_copilot(
            message=message,
            history=history_dicts,
            case_context=case_context,
        )
        return CopilotChatResponse(
            reply=res["reply"],
            model=res["model"],
            status="success",
            hasCaseContext=res.get("hasCaseContext", False),
        )
    except GroqServiceError as err:
        logger.error("Groq Copilot error: %s", err)
        raise HTTPException(status_code=503, detail=str(err))
    except Exception as err:
        logger.error("Unexpected Copilot error: %s", err)
        raise HTTPException(status_code=500, detail=f"Copilot service error: {err}")


# ---------------------------------------------------------------------------
# Frontend static file serving (for direct port 8000 access)
# ---------------------------------------------------------------------------

@app.get("/", include_in_schema=False)
def serve_index():
    index_file = os.path.join(PROJECT_ROOT, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return {"status": "ok", "service": "KiraayaShield API"}


@app.get("/{file_name:path}", include_in_schema=False)
def serve_static_asset(file_name: str):
    """Serve frontend static files (app.js, styles.css, etc.) when running on port 8000."""
    if not file_name or file_name.startswith("api/") or file_name.startswith("backend_python/"):
        raise HTTPException(status_code=404, detail="Not Found")
    safe_path = os.path.normpath(os.path.join(PROJECT_ROOT, file_name))
    if safe_path.startswith(PROJECT_ROOT) and os.path.isfile(safe_path):
        return FileResponse(safe_path)
    raise HTTPException(status_code=404, detail="Not Found")
