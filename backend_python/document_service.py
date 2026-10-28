"""
KiraayaShield — Document Extraction & Lease Classification Service.

Provides:
  - Real PDF and TXT document text extraction using pypdf
  - File size and MIME type validation
  - Detection of scanned/image-only PDFs (OCR unavailable warning)
  - Strict classification: Distinguishes genuine rental leases from academic tutorials,
    homework, syllabus, and other unrelated documents.
  - Page-by-page text mapping with page citation preservation
"""

import io
import re
import logging
from typing import Tuple, List, Dict, Any, Optional

import pypdf
from backend_python.models import DocumentExtractResponse

logger = logging.getLogger("kiraayashield.document")

MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB

# Rental / Tenancy agreement terminology
LEASE_KEYWORD_PATTERNS = [
    r"\b(lease|tenancy|rental|agreement)\b",
    r"\b(leave\s+and\s+licen[sc]e)\b",
    r"\b(lessor|landlord|licensor)\b",
    r"\b(lessee|tenant|licensee)\b",
    r"\b(security\s+deposit|refundable\s+deposit|deposit)\b",
    r"\b(monthly\s+rent|rent\s+payable|rent)\b",
    r"\b(demised\s+premises|rented\s+premises|residential\s+premises|premises|flat|apartment)\b",
    r"\b(maintenance\s+charges|maintenance\s+fee|deduction|deductions|repainting|painting)\b",
    r"\b(vacating|handover|peaceful\s+possession|move-out|move-in)\b",
    r"\b(lock-in\s+period|notice\s+period|clause\s+[0-9]+)\b",
    r"\b(model\s+tenancy\s+act|rent\s+control\s+act|wear\s+and\s+tear)\b",
]

# Academic, coursework, or non-lease terminology
ACADEMIC_KEYWORD_PATTERNS = [
    r"\b(tutorial\s*[0-9]*|assignment\s*[0-9]*|homework\s*[0-9]*)\b",
    r"\b(lecture\s*notes|syllabus|coursework|curriculum)\b",
    r"\b(computer\s+science|cse|ece|algorithm|pseudocode|data\s+structure)\b",
    r"\b(problem\s+statement|question\s+[0-9]+|solution|marks|grade)\b",
    r"\b(semester|exam|quiz|midterm|final\s+exam)\b",
    r"\b(instructor|professor|faculty|student\s+id|roll\s+no)\b",
    r"\b(python\s+programming|java\s+programming|c\+\+|compiler|runtime)\b",
]


def classify_document(text: str, filename: str) -> Tuple[bool, str, Optional[str]]:
    """
    Classify whether the extracted text is a genuine residential lease/tenancy agreement.
    Returns (is_lease, document_type, rejection_reason).
    """
    clean_text = text.lower()
    clean_filename = filename.lower()

    # Tally lease matches
    lease_score = sum(1 for p in LEASE_KEYWORD_PATTERNS if re.search(p, clean_text))
    # Tally academic / unrelated matches
    academic_score = sum(1 for p in ACADEMIC_KEYWORD_PATTERNS if re.search(p, clean_text))

    # Filename hints
    if any(k in clean_filename for k in ["tutorial", "assignment", "hw", "lecture", "lab", "syllabus"]):
        academic_score += 3
    if any(k in clean_filename for k in ["lease", "rent", "tenancy", "agreement"]):
        lease_score += 3

    # Check for empty / whitespace-only text
    if not clean_text.strip():
        return (
            False,
            "Empty or Scanned Document",
            f"The document '{filename}' contains no extractable text. "
            "If this is a scanned PDF, OCR is currently unavailable. Please provide a searchable PDF or text copy."
        )

    # Academic tutorial detection
    if academic_score >= 1 and academic_score > lease_score:
        return (
            False,
            "Academic Tutorial / Coursework",
            f"The uploaded document '{filename}' is identified as an Academic Tutorial or Coursework, "
            "not a residential rental lease agreement. KiraayaShield will not run a deposit deduction "
            "audit on non-lease documents to avoid producing false legal findings."
        )

    # If strongly non-lease (zero lease keywords in text of more than 100 characters and no filename lease hints)
    if len(clean_text) > 100 and lease_score == 0:
        return (
            False,
            "Unrelated Non-Lease Document",
            f"The uploaded document '{filename}' does not appear to be a residential tenancy or lease agreement "
            "(no clauses referring to landlord, tenant, rent, or security deposit were identified). "
            "Please upload a valid rental lease agreement to receive an evidentiary assessment."
        )

    return (True, "Residential Tenancy / Lease Agreement", None)


def extract_and_validate_document(file_bytes: bytes, filename: str) -> DocumentExtractResponse:
    """
    Extract text from uploaded PDF, TXT, or MD file bytes.
    Validates size, type, scans for image-only content, and classifies content.
    """
    file_size = len(file_bytes)
    if file_size > MAX_FILE_SIZE_BYTES:
        return DocumentExtractResponse(
            filename=filename,
            fileSizeBytes=file_size,
            isLease=False,
            documentType="Rejected File Size",
            pageCount=0,
            extractedText="",
            rejectionReason=f"File exceeds maximum permissible size of {MAX_FILE_SIZE_BYTES // (1024*1024)} MB.",
            isScanned=False,
            samplePreview="",
        )

    lower_name = filename.lower()
    extracted_text = ""
    pages: List[Dict[str, Any]] = []
    is_scanned = False

    if lower_name.endswith(".txt") or lower_name.endswith(".md"):
        try:
            extracted_text = file_bytes.decode("utf-8")
        except UnicodeDecodeError:
            extracted_text = file_bytes.decode("latin-1", errors="replace")
        pages = [{"page": 1, "text": extracted_text}]

    elif lower_name.endswith(".pdf"):
        try:
            reader = pypdf.PdfReader(io.BytesIO(file_bytes))
            for idx, page in enumerate(reader.pages):
                page_text = page.extract_text() or ""
                pages.append({"page": idx + 1, "text": page_text})
            
            # Combine pages with clear page markers
            extracted_text = "\n\n".join(
                f"--- [Page {p['page']}] ---\n{p['text']}" for p in pages if p['text'].strip()
            )

            # Check if PDF is image-only / scanned
            total_chars = sum(len(p['text'].strip()) for p in pages)
            if total_chars < 50:
                is_scanned = True

        except Exception as exc:
            logger.warning("PDF extraction failed for %s: %s", filename, exc)
            return DocumentExtractResponse(
                filename=filename,
                fileSizeBytes=file_size,
                isLease=False,
                documentType="Corrupt or Unreadable PDF",
                pageCount=0,
                extractedText="",
                rejectionReason=f"Unable to read PDF file ({exc}). Please ensure the file is not password-protected or corrupted.",
                isScanned=False,
                samplePreview="",
            )
    else:
        return DocumentExtractResponse(
            filename=filename,
            fileSizeBytes=file_size,
            isLease=False,
            documentType="Unsupported File Type",
            pageCount=0,
            extractedText="",
            rejectionReason=f"Unsupported file format '{filename}'. Please upload a .pdf, .txt, or .md document.",
            isScanned=False,
            samplePreview="",
        )

    # Classify the extracted text
    is_lease, doc_type, rejection_reason = classify_document(extracted_text, filename)
    sample_preview = extracted_text[:400].strip() if extracted_text else ""

    return DocumentExtractResponse(
        filename=filename,
        fileSizeBytes=file_size,
        isLease=is_lease,
        documentType=doc_type,
        pageCount=len(pages),
        extractedText=extracted_text,
        rejectionReason=rejection_reason,
        isScanned=is_scanned,
        samplePreview=sample_preview,
    )
