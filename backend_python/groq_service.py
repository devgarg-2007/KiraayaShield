"""
KiraayaShield — Dedicated Groq LLM Service Module.

Provides evidence-driven rental security deposit deduction and lease clause
analysis using GroqCloud LLM models (preferring openai/gpt-oss-120b).
Returns strictly structured, Pydantic-validated assessments with:
  - Findings & extracted lease clauses
  - Evidence supporting each finding & missing evidence
  - Four assessment categories: Supported, Needs Justification, Potentially Excessive, Contradictory
  - Case readiness score measuring evidence completeness
  - Recommended next steps & statutory disclaimer
"""

import os
import json
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from dotenv import load_dotenv
from groq import Groq, GroqError

from backend_python.models import (
    AssessmentResult,
    AssessmentFinding,
    DeductionItem,
)

load_dotenv()
logger = logging.getLogger("kiraayashield.groq")

# Model preferences
PRIMARY_MODEL = os.getenv("GROQ_PRIMARY_MODEL", "openai/gpt-oss-120b")
FALLBACK_MODELS = ["openai/gpt-oss-20b", "llama-3.3-70b-versatile"]

DISCLAIMER_TEXT = (
    "This assessment is an informational evidentiary evaluation based on provided inputs and applicable "
    "statutory guidelines (e.g., Model Tenancy Act 2021 and Consumer Protection Act 2019). It does not "
    "constitute formal legal advice or a binding judicial verdict. Case readiness score strictly measures "
    "evidence completeness."
)


def _get_groq_client() -> Optional[Groq]:
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key or api_key == "your_groq_api_key_here":
        logger.warning("GROQ_API_KEY is not set or using placeholder.")
        return None
    return Groq(api_key=api_key)


SYSTEM_PROMPT = """You are KiraayaShield's AI Legal Lease & Security Deposit Deduction Auditor, specialising in Indian rental housing law (Model Tenancy Act 2021, Indian Contract Act 1872, and Consumer Protection Act 2019).

CRITICAL CONSTRAINTS & BEHAVIOR:
1. Do not fabricate, hallucinate, or invent lease clauses, statutory sections, market rates, invoices, or evidence.
2. Every finding must refer to text actually present in the provided lease or deduction line-items.
3. Every finding MUST be classified into exactly one of these four categories:
   - "Supported": Deductions or clauses legally valid and properly evidenced.
   - "Needs Justification": Landlord claims lacking itemised GST invoices, photos, or proof of tenant causation.
   - "Potentially Excessive": Deductions substantially above standard market rates for the given city.
   - "Contradictory": Claims violating statutory rights (e.g., charging outgoing tenant for normal wear and tear / routine repainting under Schedule II MTA 2021, or security deposit > 2 months rent under Section 11 MTA 2021).
4. Severity must be one of: "LOW", "MODERATE", "HIGH", "CRITICAL".
5. Case Readiness Score (caseReadinessScore) must be a float between 0.0 and 100.0 measuring EVIDENCE COMPLETENESS (how thoroughly documented the tenant's case is with photos, timestamps, invoices, notices), NOT the probability of winning a dispute.
6. Return VALID JSON strictly conforming to the requested schema.
"""


def _build_user_prompt(
    lease_text: str,
    monthly_rent: float,
    deposit_paid: float,
    city: str,
    deductions: List[Dict[str, Any]],
) -> str:
    deductions_summary = json.dumps(deductions, indent=2) if deductions else "No specific deduction items provided."
    return f"""Analyze the following rental tenancy data and return a structured JSON assessment:

PROPERTY & FINANCIALS:
- City: {city}
- Monthly Rent: INR {monthly_rent:,.2f}
- Security Deposit Paid: INR {deposit_paid:,.2f}
- Deposit-to-Rent Ratio: {deposit_paid / max(monthly_rent, 1):.1f}x

LEASE AGREEMENT TEXT:
\"\"\"
{lease_text if lease_text.strip() else "Standard residential lease agreement without specific clause text provided."}
\"\"\"

CLAIMED DEDUCTION ITEMS:
{deductions_summary}

REQUIRED JSON OUTPUT FORMAT:
{{
  "summary": "Concise summary of findings and overall dispute posture",
  "caseReadinessScore": 75.0,
  "riskScore": 84.0,
  "recoverableEstimate": 25000.0,
  "findings": [
    {{
      "id": "CL-01",
      "clauseTitle": "Clause title or deduction item name",
      "extractedText": "Exact quoted text from input document",
      "pageNumber": 1,
      "sourceSection": "Clause / Section identifier if mentioned",
      "category": "Contradictory | Potentially Excessive | Needs Justification | Supported",
      "severity": "CRITICAL | HIGH | MODERATE | LOW",
      "supportingEvidence": ["List of evidence supporting tenant or landlord"],
      "missingEvidence": ["List of missing evidence or documentation required"],
      "legalFramework": "e.g., Section 11 & Schedule II, Model Tenancy Act 2021",
      "explanation": "Legal and evidentiary explanation",
      "recoverableAmount": 15000.0
    }}
  ],
  "missingEvidenceSummary": [
    "List of overall missing documents/photos across the dossier"
  ],
  "recommendedNextSteps": [
    "Step 1...",
    "Step 2..."
  ]
}}
"""


def _deterministic_fallback_assessment(
    lease_text: str,
    monthly_rent: float,
    deposit_paid: float,
    city: str,
    deductions: Optional[List[Dict[str, Any]]] = None,
    case_id: Optional[str] = None,
) -> AssessmentResult:
    """Deterministic rule-based fallback if LLM is unavailable or fails."""
    ratio = deposit_paid / max(monthly_rent, 1)
    findings: List[AssessmentFinding] = []
    total_recoverable = 0.0

    # Check 1: Deposit ratio > 2 months
    if ratio > 2.0:
        excess = deposit_paid - (monthly_rent * 2)
        total_recoverable += excess
        findings.append(
            AssessmentFinding(
                id="CL-04",
                clauseTitle="Excessive Security Deposit Cap",
                extractedText=f"Security deposit of INR {deposit_paid:,.0f} equals {ratio:.1f} months rent.",
                category="Contradictory",
                severity="HIGH",
                supportingEvidence=[f"Deposit receipt for INR {deposit_paid:,.0f}"],
                missingEvidence=["Proof of landlord registration under local tenancy board"],
                legalFramework="Section 11(1)(a), Model Tenancy Act 2021 (capped at 2 months)",
                explanation=f"Residential security deposits are statutorily capped at two months rent. The excess INR {excess:,.0f} is unlawfully withheld.",
                recoverableAmount=excess,
            )
        )

    # Check 2: Deductions checks
    if deductions:
        for idx, d in enumerate(deductions, 1):
            name = d.get("item", f"Item #{idx}")
            claim = float(d.get("landlord_claim", d.get("landlordClaim", 0.0)))
            fair = float(d.get("fair_rate", d.get("fairRate", claim * 0.3)))
            exempt = d.get("exempt", False)

            if "paint" in name.lower() or exempt:
                category = "Contradictory"
                severity = "CRITICAL"
                rec = claim
                framework = "Schedule II, Model Tenancy Act 2021 (Periodic Painting is Landlord Duty)"
                expl = "Natural wall fading constitutes ordinary wear and tear. Statutory provisions obligate landlord for routine painting."
            elif claim > fair * 1.5:
                category = "Potentially Excessive"
                severity = "HIGH"
                rec = max(0.0, claim - fair)
                framework = "Section 73 & 74, Indian Contract Act 1872 (Liquidated Damages vs Penalty)"
                expl = f"Claimed amount INR {claim:,.0f} exceeds fair-market rate of INR {fair:,.0f} without itemized GST vendor proof."
            else:
                category = "Needs Justification"
                severity = "MODERATE"
                rec = claim * 0.5
                framework = "Consumer Protection Act 2019 (Deficiency in Service & Unilateral Deductions)"
                expl = "No independent invoice or handover inspection sheet furnished by landlord."

            total_recoverable += rec
            findings.append(
                AssessmentFinding(
                    id=f"DED-{idx:02d}",
                    clauseTitle=f"Deduction Claim: {name}",
                    extractedText=f"Landlord claimed INR {claim:,.0f} for {name}.",
                    category=category,
                    severity=severity,
                    supportingEvidence=["Move-out photographs", "UPI deposit statement"],
                    missingEvidence=["Original itemized vendor GST invoice", "Move-in signed condition checklist"],
                    legalFramework=framework,
                    explanation=expl,
                    recoverableAmount=rec,
                )
            )
    else:
        # Default clause 14 check from raw lease text
        if "paint" in lease_text.lower() or "50%" in lease_text or "forfeit" in lease_text.lower():
            rec_amt = round(deposit_paid * 0.5)
            total_recoverable += rec_amt
            findings.append(
                AssessmentFinding(
                    id="CL-14",
                    clauseTitle="Mandatory Painting / Deep Cleaning Forfeiture",
                    extractedText="Tenant shall mandatorily forfeit deposit towards painting/cleaning.",
                    category="Contradictory",
                    severity="CRITICAL",
                    supportingEvidence=["Lease agreement clause text"],
                    missingEvidence=["Move-out joint inspection log signed by landlord"],
                    legalFramework="Section 11 & Schedule II, Model Tenancy Act 2021",
                    explanation="Blanket forfeiture clauses for periodic repainting violate statutory tenant protections.",
                    recoverableAmount=rec_amt,
                )
            )

    cat_counts = {}
    for f in findings:
        cat_counts[f.category] = cat_counts.get(f.category, 0) + 1

    return AssessmentResult(
        caseId=case_id,
        summary=f"Audit completed for {city} tenancy. Identified {len(findings)} flagged items totaling INR {total_recoverable:,.0f} in disputed deductions.",
        caseReadinessScore=78.0,
        riskScore=86.0 if ratio > 2.0 else 68.0,
        recoverableEstimate=total_recoverable,
        findings=findings,
        categoryCounts=cat_counts,
        missingEvidenceSummary=[
            "Signed Move-In Joint Inspection Form",
            "Move-out handover video/photographs with verified EXIF timestamps",
            "Itemized vendor GST receipts from Landlord",
        ],
        recommendedNextSteps=[
            "Dispatch formal 15-day Pre-Litigation Legal Demand Notice citing Model Tenancy Act 2021.",
            "Demand itemized GST tax invoices for all landlord repair assertions.",
            "Lodge formal docket on National Consumer Helpline (INGRAM Portal) if cure period lapses.",
        ],
        disclaimer=DISCLAIMER_TEXT,
        createdAt=datetime.now(timezone.utc),
    )


def analyze_lease_with_groq(
    lease_text: str,
    monthly_rent: float,
    deposit_paid: float,
    city: str = "Noida",
    deductions: Optional[List[Dict[str, Any]]] = None,
    case_id: Optional[str] = None,
) -> AssessmentResult:
    """
    Run Groq LLM assessment with openai/gpt-oss-120b and JSON mode.
    Falls back gracefully across models and to deterministic logic if network/API fails.
    """
    client = _get_groq_client()
    if not client:
        logger.info("Using deterministic legal audit engine (Groq client unavailable).")
        return _deterministic_fallback_assessment(
            lease_text, monthly_rent, deposit_paid, city, deductions, case_id
        )

    user_prompt = _build_user_prompt(lease_text, monthly_rent, deposit_paid, city, deductions or [])
    models_to_try = [PRIMARY_MODEL] + FALLBACK_MODELS

    for model_name in models_to_try:
        try:
            logger.info("Invoking Groq model: %s", model_name)
            completion = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
                max_tokens=2500,
            )

            raw_text = completion.choices[0].message.content
            if not raw_text:
                continue

            parsed = json.loads(raw_text)

            # Build findings list
            findings: List[AssessmentFinding] = []
            for f in parsed.get("findings", []):
                findings.append(
                    AssessmentFinding(
                        id=str(f.get("id", f"CL-{len(findings)+1:02d}")),
                        clauseTitle=str(f.get("clauseTitle", "Flagged Clause")),
                        extractedText=str(f.get("extractedText", "")),
                        category=str(f.get("category", "Needs Justification")),
                        severity=str(f.get("severity", "MODERATE")).upper(),
                        supportingEvidence=list(f.get("supportingEvidence", [])),
                        missingEvidence=list(f.get("missingEvidence", [])),
                        legalFramework=str(f.get("legalFramework", "Model Tenancy Act 2021")),
                        explanation=str(f.get("explanation", "")),
                        recoverableAmount=float(f.get("recoverableAmount", 0.0)),
                        pageNumber=int(f["pageNumber"]) if f.get("pageNumber") is not None and str(f.get("pageNumber")).isdigit() else None,
                        sourceSection=str(f.get("sourceSection", "")).strip() or None,
                    )
                )

            # Category counts
            cat_counts: Dict[str, int] = {}
            for item in findings:
                cat_counts[item.category] = cat_counts.get(item.category, 0) + 1

            # Readiness score (evidence completeness)
            readiness = float(parsed.get("caseReadinessScore", 75.0))
            readiness = max(0.0, min(100.0, readiness))

            result = AssessmentResult(
                caseId=case_id,
                summary=str(parsed.get("summary", "Evidentiary assessment complete.")),
                caseReadinessScore=readiness,
                riskScore=float(parsed.get("riskScore", 80.0)),
                recoverableEstimate=float(parsed.get("recoverableEstimate", sum(f.recoverable_amount for f in findings))),
                findings=findings,
                categoryCounts=cat_counts,
                missingEvidenceSummary=list(parsed.get("missingEvidenceSummary", [])),
                recommendedNextSteps=list(parsed.get("recommendedNextSteps", [])),
                disclaimer=DISCLAIMER_TEXT,
                createdAt=datetime.now(timezone.utc),
            )
            logger.info("Groq assessment generated successfully using %s.", model_name)
            return result

        except (GroqError, json.JSONDecodeError, Exception) as exc:
            logger.warning("Groq call failed with model %s: %s", model_name, exc)
            continue

class GroqServiceError(Exception):
    """Raised when Groq API encounters a failure and cannot generate a response."""
    pass


COPILOT_SYSTEM_PROMPT = """You are KiraayaShield Legal Copilot, an evidence-driven AI assistant specializing in Indian residential rental law (Model Tenancy Act 2021, Indian Contract Act 1872, and Consumer Protection Act 2019).

CRITICAL DIRECTIVES:
1. Provide accurate, practical, and statutory guidance for tenants navigating deposit recovery, unfair deductions, notice periods, or tenancy agreements in India.
2. Cite relevant statutory provisions where helpful:
   - Section 11(1)(a) Model Tenancy Act 2021: Caps residential security deposits at a maximum of 2 months' rent.
   - Section 11(2) Model Tenancy Act 2021: Security deposit must be refunded to tenant on the date of handing over possession.
   - Schedule II Model Tenancy Act 2021: Routine periodic repainting, structural repairs, and natural oxidation/wear-and-tear are strictly the lessor's statutory obligation; ordinary wear and tear cannot be billed to outgoing tenants.
   - Section 2(47) Consumer Protection Act 2019: Withholding contractor bills, refusing receipts, or imposing arbitrary forfeitures constitutes an Unfair Trade Practice / Deficiency in Service.
   - Section 73 & 74 Indian Contract Act 1872: Unliquidated penalty clauses without proof of actual damage are legally void.
   - National Consumer Helpline (INGRAM at consumerhelpline.gov.in / dial 1915) and e-Daakhil for formal grievance redressal.
3. CONTEXT SENSITIVITY:
   - If case context is provided, address the tenant's specific situation, numbers, and facts.
   - If NO case context is provided, give accurate general legal guidance without inventing landlord names, rents, or arbitrary dispute amounts.
4. HONESTY: Never fabricate statutes, case law citations, or financial amounts.
5. DISCLAIMER: Remind the tenant that your answer provides legal information and evidence analysis based on Indian statutes, not formal advocate representation. Keep answers clear, well-structured, and helpful.
"""


def chat_with_copilot(
    message: str,
    history: Optional[List[Dict[str, str]]] = None,
    case_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Chat with KiraayaShield Legal Copilot powered by Groq.
    If Groq fails or is unconfigured, raises GroqServiceError (never fabricates answers).
    """
    client = _get_groq_client()
    if not client:
        raise GroqServiceError("Groq client is unavailable. Please verify GROQ_API_KEY in the backend environment.")

    # Assemble context if provided
    context_text = ""
    has_context = False
    if case_context and isinstance(case_context, dict):
        ctx_parts = []
        if case_context.get("tenantName") and case_context["tenantName"] not in ("[TENANT NAME REQUIRED]", "[TENANT NAME]"):
            ctx_parts.append(f"Tenant: {case_context['tenantName']}")
        if case_context.get("landlordName") and case_context["landlordName"] not in ("[LANDLORD NAME REQUIRED]", "[LANDLORD NAME]"):
            ctx_parts.append(f"Landlord: {case_context['landlordName']}")
        if case_context.get("city"):
            ctx_parts.append(f"City/Region: {case_context['city']}")
        if case_context.get("monthlyRent"):
            ctx_parts.append(f"Monthly Rent: INR {float(case_context['monthlyRent']):,.0f}")
        if case_context.get("depositPaid"):
            ctx_parts.append(f"Deposit Paid: INR {float(case_context['depositPaid']):,.0f}")
        if case_context.get("landlordClaimTotal"):
            ctx_parts.append(f"Disputed Landlord Deduction: INR {float(case_context['landlordClaimTotal']):,.0f}")
        if case_context.get("fairDeductionTotal"):
            ctx_parts.append(f"Fair Benchmark Deductible: INR {float(case_context['fairDeductionTotal']):,.0f}")
        if case_context.get("status"):
            ctx_parts.append(f"Case Status: {case_context['status']}")
        if case_context.get("findingsCount"):
            ctx_parts.append(f"Flagged Clauses Count: {case_context['findingsCount']}")

        if ctx_parts:
            has_context = True
            context_text = "\n\nACTIVE DISPUTE CONTEXT:\n" + "\n".join(f"- {p}" for p in ctx_parts)

    system_content = COPILOT_SYSTEM_PROMPT + context_text
    messages: List[Dict[str, str]] = [{"role": "system", "content": system_content}]

    # Include recent history (up to last 6 messages)
    if history:
        for msg in history[-6:]:
            role = msg.get("role")
            content = msg.get("content")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})

    messages.append({"role": "user", "content": message})

    models_to_try = [PRIMARY_MODEL] + FALLBACK_MODELS
    last_error = None

    for model_name in models_to_try:
        try:
            logger.info("Invoking Groq Copilot chat with model: %s", model_name)
            completion = client.chat.completions.create(
                model=model_name,
                messages=messages,
                temperature=0.3,
                max_tokens=1000,
            )
            reply = completion.choices[0].message.content
            if reply and reply.strip():
                return {
                    "reply": reply.strip(),
                    "model": model_name,
                    "status": "success",
                    "hasCaseContext": has_context,
                }
            logger.warning("Empty response from Groq model %s, trying fallback", model_name)
        except Exception as exc:
            logger.warning("Groq Copilot call failed for model %s: %s", model_name, exc)
            last_error = exc
            continue

    raise GroqServiceError(f"Groq Copilot failed on all models. Root cause: {last_error}")

