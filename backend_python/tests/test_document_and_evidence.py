"""
KiraayaShield — Tests for Document Extraction, Lease Classification, and Real Evidence Hashing.
"""

import io
import hashlib
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend_python.main import app
from backend_python.document_service import classify_document, extract_and_validate_document
from backend_python.evidence_service import compute_sha256, extract_real_metadata

client = TestClient(app)


def test_classify_academic_tutorial_rejected():
    tutorial_text = """
    Tutorial 6: Algorithms and Data Structures
    Course: CSE 201 - Spring Semester
    Problem 1: Implement Dijkstra's shortest path algorithm.
    Question 2: Analyze worst-case runtime complexity O(V^2).
    Marks: 20
    """
    is_lease, doc_type, reason = classify_document(tutorial_text, "Tutorial 6.pdf")
    assert is_lease is False
    assert "Academic Tutorial" in doc_type
    assert reason is not None
    assert "Tutorial 6.pdf" in reason


def test_classify_genuine_lease_accepted():
    lease_text = """
    RESIDENTIAL TENANCY LEASE AGREEMENT
    This agreement made between Mr. Sharma (Lessor / Landlord) and Ms. Kapoor (Lessee / Tenant).
    1. Demised Premises: Flat 402, Green Meadows, Sector 62, Noida.
    2. Monthly Rent: Rs. 24,000 payable by 5th of each calendar month.
    3. Security Deposit: Rs. 72,000 refundable upon peaceful handover of possession.
    4. Clause 14: Painting charges of Rs. 15,000 shall be deducted unconditionally from deposit upon vacating.
    """
    is_lease, doc_type, reason = classify_document(lease_text, "Lease_Agreement_Noida.pdf")
    assert is_lease is True
    assert "Residential Tenancy" in doc_type
    assert reason is None


def test_classify_empty_document():
    is_lease, doc_type, reason = classify_document("   ", "empty.pdf")
    assert is_lease is False
    assert "Empty" in doc_type


def test_sha256_exact_byte_match():
    test_bytes = b"KiraayaShield-evidence-integrity-check-2026"
    expected_hex = hashlib.sha256(test_bytes).hexdigest()
    actual_hex = compute_sha256(test_bytes)
    assert actual_hex == expected_hex
    assert len(actual_hex) == 64


def test_image_exif_real_extraction_no_fabrication():
    # Generate clean in-memory JPEG without GPS tags
    img = Image.new("RGB", (100, 100), color=(73, 109, 137))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    img_bytes = buf.getvalue()

    metadata = extract_real_metadata(img_bytes, "test_room.jpg")
    assert isinstance(metadata, dict)
    assert metadata["isImage"] is True
    # Crucial honesty assertion: EXIF should NOT contain fabricated GPS or simulated camera models
    assert metadata.get("hasExif") is False
    assert metadata.get("cameraMake") is None
    assert metadata.get("cameraModel") is None
    assert metadata.get("captureDate") is None
    assert "integrityNote" in metadata


def test_document_extract_endpoint_rejects_tutorial():
    # Send an academic tutorial file
    tutorial_content = (
        b"Electronic Devices and Circuits (24B11EC211)\n"
        b"Tutorial Sheet 6\n"
        b"Course: ECE / CSE\n"
        b"Q1. [CO3] In a self-bias n-channel JFET, find operating point.\n"
        b"Question 2: Compute voltage gain and transconductance.\n"
        b"Marks: 25\n"
    )
    resp = client.post(
        "/api/v1/documents/extract",
        files={"file": ("Tutorial_6.txt", tutorial_content, "text/plain")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["isLease"] is False
    assert "Academic Tutorial" in body["documentType"]
    assert body["rejectionReason"] is not None
    assert "Tutorial" in body["rejectionReason"]


def test_real_desktop_tutorial_pdf_if_present():
    import os
    pdf_path = "/Users/devgarg/Desktop/EDC/Tutorial 6.pdf"
    if os.path.exists(pdf_path):
        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()
        resp = client.post(
            "/api/v1/documents/extract",
            files={"file": ("Tutorial 6.pdf", pdf_bytes, "application/pdf")},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["isLease"] is False
        assert "Academic Tutorial" in body["documentType"]
        assert "Tutorial 6.pdf" in body["rejectionReason"]


def test_evidence_inspect_endpoint():
    sample_content = b"Move-out kitchen inspection photo raw bytes"
    resp = client.post(
        "/api/v1/evidence/inspect",
        files={"file": ("kitchen_sink.jpg", sample_content, "image/jpeg")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["filename"] == "kitchen_sink.jpg"
    assert body["sha256"] == hashlib.sha256(sample_content).hexdigest()
    assert "integrityNote" in body
    assert "upload" in body["integrityNote"].lower()


def test_case_evidence_upload_and_disk_persistence():
    # 1. Create a persistent case
    create_resp = client.post("/api/v1/cases", json={
        "title": "Evidence Test Case",
        "tenantName": "Arjun Mehta",
        "landlordName": "Vipin Kumar",
        "propertyAddress": "Sector 137, Noida",
        "city": "Noida",
        "monthlyRent": 20000,
        "depositPaid": 60000,
    })
    assert create_resp.status_code == 201
    case_id = create_resp.json()["id"]

    # 2. Upload real evidence file to case
    file_bytes = b"High-res move-out bathroom tap condition bytes"
    upload_resp = client.post(
        f"/api/v1/cases/{case_id}/evidence/upload",
        files={"file": ("bathroom_tap.png", file_bytes, "image/png")},
    )
    assert upload_resp.status_code == 201
    upload_data = upload_resp.json()
    assert upload_data["caseId"] == case_id
    assert upload_data["filename"] == "bathroom_tap.png"
    assert upload_data["fileHash"] == hashlib.sha256(file_bytes).hexdigest()
    assert upload_data["filePath"].startswith("uploads/")

    # 3. List evidence for case
    list_resp = client.get(f"/api/v1/cases/{case_id}/evidence")
    assert list_resp.status_code == 200
    evidence_list = list_resp.json()["evidence"]
    assert any(e["filename"] == "bathroom_tap.png" for e in evidence_list)

    # 4. Cleanup case (and disk uploads)
    del_resp = client.delete(f"/api/v1/cases/{case_id}")
    assert del_resp.status_code == 200
