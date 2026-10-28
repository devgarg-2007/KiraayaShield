"""
KiraayaShield — Pydantic data models.

Defines validated schemas for Cases, Evidence, Assessments, and
Investigations. All models use camelCase aliases for JSON serialisation
so the frontend can consume them directly.
"""

from datetime import datetime, timezone
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Deduction line-item (embedded in a Case)
# ---------------------------------------------------------------------------
class DeductionItem(BaseModel):
    item: str = Field(..., min_length=1, max_length=500)
    landlord_claim: float = Field(..., ge=0, alias="landlordClaim")
    fair_rate: Optional[float] = Field(None, ge=0, alias="fairRate")
    exempt: bool = False
    source: str = ""

    model_config = {"populate_by_name": True}


# ---------------------------------------------------------------------------
# Case — create / update requests
# ---------------------------------------------------------------------------
class CaseCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)
    tenant_name: str = Field(..., min_length=1, max_length=200, alias="tenantName")
    landlord_name: str = Field(..., min_length=1, max_length=200, alias="landlordName")
    property_address: str = Field(..., min_length=1, max_length=500, alias="propertyAddress")
    city: str = Field("", max_length=200)
    monthly_rent: float = Field(..., ge=0, alias="monthlyRent")
    deposit_paid: float = Field(..., ge=0, alias="depositPaid")
    deductions_claimed: List[DeductionItem] = Field(default_factory=list, alias="deductionsClaimed")
    upi_reference: str = Field("", max_length=200, alias="upiReference")
    vacate_date: Optional[str] = Field(None, alias="vacateDate")
    lease_text: str = Field("", alias="leaseText")

    model_config = {"populate_by_name": True}

    @field_validator("monthly_rent", "deposit_paid")
    @classmethod
    def must_be_positive(cls, v: float) -> float:
        if v < 0:
            raise ValueError("Amount must not be negative")
        return v


class CaseUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=300)
    tenant_name: Optional[str] = Field(None, min_length=1, max_length=200, alias="tenantName")
    landlord_name: Optional[str] = Field(None, min_length=1, max_length=200, alias="landlordName")
    property_address: Optional[str] = Field(None, min_length=1, max_length=500, alias="propertyAddress")
    city: Optional[str] = Field(None, max_length=200)
    monthly_rent: Optional[float] = Field(None, ge=0, alias="monthlyRent")
    deposit_paid: Optional[float] = Field(None, ge=0, alias="depositPaid")
    deductions_claimed: Optional[List[DeductionItem]] = Field(None, alias="deductionsClaimed")
    upi_reference: Optional[str] = Field(None, max_length=200, alias="upiReference")
    vacate_date: Optional[str] = Field(None, alias="vacateDate")
    lease_text: Optional[str] = Field(None, alias="leaseText")
    status: Optional[str] = None

    model_config = {"populate_by_name": True}

    @field_validator("status")
    @classmethod
    def valid_status(cls, v: Optional[str]) -> Optional[str]:
        allowed = {"draft", "in_progress", "assessed", "resolved"}
        if v is not None and v not in allowed:
            raise ValueError(f"status must be one of {allowed}")
        return v


# ---------------------------------------------------------------------------
# Case — response
# ---------------------------------------------------------------------------
class CaseResponse(BaseModel):
    id: str
    title: str
    tenant_name: str = Field(alias="tenantName")
    landlord_name: str = Field(alias="landlordName")
    property_address: str = Field(alias="propertyAddress")
    city: str
    monthly_rent: float = Field(alias="monthlyRent")
    deposit_paid: float = Field(alias="depositPaid")
    deductions_claimed: List[DeductionItem] = Field(alias="deductionsClaimed")
    upi_reference: str = Field(alias="upiReference")
    vacate_date: Optional[str] = Field(alias="vacateDate")
    lease_text: str = Field(alias="leaseText")
    status: str
    is_demo: bool = Field(alias="isDemo")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")

    model_config = {"populate_by_name": True, "from_attributes": True}


# ---------------------------------------------------------------------------
# Evidence metadata
# ---------------------------------------------------------------------------
EVIDENCE_TYPES = {"photo", "document", "chat_log", "receipt", "other"}


class EvidenceCreate(BaseModel):
    evidence_type: str = Field(..., alias="evidenceType")
    filename: str = Field(..., min_length=1, max_length=500)
    description: str = Field("", max_length=2000)
    file_hash: Optional[str] = Field(None, alias="fileHash")
    metadata: dict = Field(default_factory=dict)

    model_config = {"populate_by_name": True}

    @field_validator("evidence_type")
    @classmethod
    def valid_type(cls, v: str) -> str:
        if v not in EVIDENCE_TYPES:
            raise ValueError(f"evidence_type must be one of {EVIDENCE_TYPES}")
        return v


class EvidenceResponse(BaseModel):
    id: str
    case_id: str = Field(alias="caseId")
    evidence_type: str = Field(alias="evidenceType")
    filename: str
    description: str = ""
    file_hash: Optional[str] = Field(None, alias="fileHash")
    file_path: Optional[str] = Field(None, alias="filePath")
    metadata: dict = Field(default_factory=dict)
    created_at: datetime = Field(alias="createdAt")

    model_config = {"populate_by_name": True, "from_attributes": True}


# ---------------------------------------------------------------------------
# Assessment (Groq LLM Milestone)
# ---------------------------------------------------------------------------
ASSESSMENT_CATEGORIES = {"Supported", "Needs Justification", "Potentially Excessive", "Contradictory"}


class AssessmentFinding(BaseModel):
    id: str
    clause_title: str = Field(..., alias="clauseTitle")
    extracted_text: str = Field("", alias="extractedText")
    category: str = Field("Needs Justification")
    severity: str = Field("MODERATE")  # LOW, MODERATE, HIGH, CRITICAL
    supporting_evidence: List[str] = Field(default_factory=list, alias="supportingEvidence")
    missing_evidence: List[str] = Field(default_factory=list, alias="missingEvidence")
    legal_framework: str = Field("", alias="legalFramework")
    explanation: str = Field("")
    recoverable_amount: float = Field(0.0, ge=0, alias="recoverableAmount")
    page_number: Optional[int] = Field(None, alias="pageNumber")
    source_section: Optional[str] = Field(None, alias="sourceSection")

    model_config = {"populate_by_name": True}

    @field_validator("category")
    @classmethod
    def valid_category(cls, v: str) -> str:
        if v not in ASSESSMENT_CATEGORIES:
            return "Needs Justification"
        return v


# ---------------------------------------------------------------------------
# Document Extraction & Classification Models (Priority 1)
# ---------------------------------------------------------------------------
class DocumentExtractResponse(BaseModel):
    filename: str
    file_size_bytes: int = Field(alias="fileSizeBytes")
    is_lease: bool = Field(alias="isLease")
    document_type: str = Field(alias="documentType")
    page_count: int = Field(alias="pageCount")
    extracted_text: str = Field("", alias="extractedText")
    rejection_reason: Optional[str] = Field(None, alias="rejectionReason")
    is_scanned: bool = Field(False, alias="isScanned")
    sample_preview: str = Field("", alias="samplePreview")

    model_config = {"populate_by_name": True}


class AssessmentRequest(BaseModel):
    lease_text: str = Field("", alias="leaseText")
    monthly_rent: float = Field(18000.0, ge=0, alias="monthlyRent")
    deposit_paid: float = Field(54000.0, ge=0, alias="depositPaid")
    city: str = Field("Noida", alias="city")
    deductions: List[DeductionItem] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class AssessmentResult(BaseModel):
    id: Optional[str] = None
    case_id: Optional[str] = Field(None, alias="caseId")
    summary: str = ""
    case_readiness_score: float = Field(..., ge=0, le=100, alias="caseReadinessScore")
    risk_score: Optional[float] = Field(None, alias="riskScore")
    recoverable_estimate: float = Field(0.0, alias="recoverableEstimate")
    findings: List[AssessmentFinding] = Field(default_factory=list)
    category_counts: dict = Field(default_factory=dict, alias="categoryCounts")
    missing_evidence_summary: List[str] = Field(default_factory=list, alias="missingEvidenceSummary")
    recommended_next_steps: List[str] = Field(default_factory=list, alias="recommendedNextSteps")
    disclaimer: str = (
        "This assessment is an informational evidentiary evaluation based on provided inputs "
        "and statutory guidelines (e.g., Model Tenancy Act 2021). It does not constitute formal "
        "legal advice or a binding judicial verdict. Case readiness score measures evidence completeness."
    )
    created_at: datetime = Field(default_factory=_utcnow, alias="createdAt")

    model_config = {"populate_by_name": True}


class AssessmentResponse(BaseModel):
    id: str
    case_id: str = Field(alias="caseId")
    risk_score: Optional[float] = Field(None, alias="riskScore")
    findings: List[dict] = Field(default_factory=list)
    categories: dict = Field(default_factory=dict)
    case_readiness_score: Optional[float] = Field(None, alias="caseReadinessScore")
    created_at: datetime = Field(alias="createdAt")

    model_config = {"populate_by_name": True, "from_attributes": True}


# ---------------------------------------------------------------------------
# Investigation record (TinyFish Milestone)
# ---------------------------------------------------------------------------
class InvestigationItem(BaseModel):
    item: str
    landlord_claim: float = Field(..., alias="landlordClaim")
    fair_market_rate: float = Field(..., alias="fairMarketRate")
    rate_range: str = Field("", alias="rateRange")
    wear_and_tear_exempt: bool = Field(False, alias="wearAndTearExempt")
    disallowed_amount: float = Field(0.0, alias="disallowedAmount")
    source_name: str = Field("", alias="sourceName")
    source_url: str = Field("", alias="sourceUrl")
    snippet: str = ""
    verified_at: str = Field("", alias="verifiedAt")
    is_live: bool = Field(False, alias="isLive")
    currency: str = Field("INR", alias="currency")

    model_config = {"populate_by_name": True}


class InvestigationRequest(BaseModel):
    city: str = Field("Noida", alias="city")
    items: Optional[List[dict]] = None

    model_config = {"populate_by_name": True}


class InvestigationResult(BaseModel):
    id: Optional[str] = None
    case_id: Optional[str] = Field(None, alias="caseId")
    city: str
    items: List[InvestigationItem] = Field(default_factory=list)
    total_landlord_claim: float = Field(0.0, alias="totalLandlordClaim")
    total_fair_deduction: float = Field(0.0, alias="totalFairDeduction")
    total_disallowed_amount: float = Field(0.0, alias="totalDisallowedAmount")
    sources_queried: List[str] = Field(default_factory=list, alias="sourcesQueried")
    live_search_success: bool = Field(False, alias="liveSearchSuccess")
    data_source: str = Field("stored_baseline_catalog", alias="dataSource")
    disclaimer: str = (
        "Market benchmarks are estimates gathered from public service rate cards and portals. "
        "They provide market context and do not constitute proof of tenant liability or legally binding quotes."
    )
    created_at: datetime = Field(default_factory=_utcnow, alias="createdAt")

    model_config = {"populate_by_name": True}


class InvestigationResponse(BaseModel):
    id: str
    case_id: str = Field(alias="caseId")
    query: str
    source: str
    status: str
    results: dict = Field(default_factory=dict)
    created_at: datetime = Field(alias="createdAt")

    model_config = {"populate_by_name": True, "from_attributes": True}


# ---------------------------------------------------------------------------
# Copilot Chat Models
# ---------------------------------------------------------------------------
class CopilotChatMessage(BaseModel):
    role: str = Field(..., pattern="^(user|assistant|system)$")
    content: str = Field(..., min_length=1, max_length=4000)


class CopilotChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    history: List[CopilotChatMessage] = Field(default_factory=list)
    case_id: Optional[str] = Field(None, alias="caseId")
    case_context: Optional[dict] = Field(None, alias="caseContext")

    model_config = {"populate_by_name": True}


class CopilotChatResponse(BaseModel):
    reply: str
    model: str
    status: str = "success"
    has_case_context: bool = Field(False, alias="hasCaseContext")
    created_at: datetime = Field(default_factory=_utcnow, alias="createdAt")

    model_config = {"populate_by_name": True}
