"""
KiraayaShield — automated tests for models, endpoints, and database failure
handling.

Uses FastAPI's TestClient (backed by httpx). Database calls are tested
against the real configured MongoDB if available; tests report clearly
when the database is unreachable rather than silently passing.

Run:
    python -m pytest backend_python/tests/test_api.py -v
"""

import os
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from pymongo.errors import ServerSelectionTimeoutError

# Ensure we use a test database
os.environ.setdefault("MONGODB_DATABASE", "kiraayashield_test")

from backend_python.main import app  # noqa: E402


client = TestClient(app)


# ============================================================================
# Pydantic model validation tests
# ============================================================================

class TestModelValidation:
    """Test that Pydantic models reject invalid inputs."""

    def test_case_create_missing_required_fields(self):
        """POST /api/v1/cases with empty body should return 422."""
        resp = client.post("/api/v1/cases", json={})
        assert resp.status_code == 422

    def test_case_create_negative_rent(self):
        """Negative monthlyRent should be rejected."""
        resp = client.post("/api/v1/cases", json={
            "title": "Test",
            "tenantName": "Alice",
            "landlordName": "Bob",
            "propertyAddress": "123 Street",
            "monthlyRent": -500,
            "depositPaid": 10000,
        })
        assert resp.status_code == 422

    def test_case_create_empty_title(self):
        """Empty title should be rejected."""
        resp = client.post("/api/v1/cases", json={
            "title": "",
            "tenantName": "Alice",
            "landlordName": "Bob",
            "propertyAddress": "123 Street",
            "monthlyRent": 10000,
            "depositPaid": 30000,
        })
        assert resp.status_code == 422

    def test_case_update_invalid_status(self):
        """Invalid status value should be rejected."""
        resp = client.patch("/api/v1/cases/000000000000000000000000", json={
            "status": "invalid_status",
        })
        assert resp.status_code == 422

    def test_evidence_invalid_type(self):
        """Invalid evidenceType should be rejected."""
        resp = client.post("/api/v1/cases/000000000000000000000000/evidence", json={
            "evidenceType": "alien_artifact",
            "filename": "test.pdf",
        })
        assert resp.status_code == 422

    def test_invalid_case_id_format(self):
        """Non-ObjectId string should return 400."""
        resp = client.get("/api/v1/cases/not-a-valid-id")
        assert resp.status_code == 400
        assert "Invalid ID format" in resp.json()["detail"]


# ============================================================================
# Health endpoint tests
# ============================================================================

class TestHealthEndpoint:
    """Test the /health endpoint."""

    def test_health_returns_200(self):
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "healthy"
        assert body["service"] == "KiraayaShield-API"
        assert "database" in body
        assert "timestamp" in body

    def test_health_reports_db_status(self):
        """Health endpoint should include mongodb key."""
        resp = client.get("/health")
        body = resp.json()
        # Database may be connected, fallback_mock, or disconnected — all are valid
        assert body["database"]["mongodb"] in ("connected", "disconnected", "error", "fallback_mock")


# ============================================================================
# Legacy compatibility endpoint tests
# ============================================================================

class TestLegacyEndpoints:
    """Test legacy /api/analyze-lease and /api/generate-complaint."""

    def test_analyze_lease_returns_risk_score(self):
        resp = client.post("/api/analyze-lease", json={
            "leaseText": "Some lease text",
            "monthlyRent": 18000,
            "depositPaid": 54000,
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "success"
        assert "riskScore" in body
        assert 0 <= body["riskScore"] <= 100

    def test_analyze_lease_low_deposit_ratio(self):
        """Deposit within 2x rent should yield valid risk assessment."""
        resp = client.post("/api/analyze-lease", json={
            "leaseText": "Normal lease",
            "monthlyRent": 20000,
            "depositPaid": 30000,
        })
        assert resp.status_code == 200
        assert "riskScore" in resp.json()
        assert 0 <= resp.json()["riskScore"] <= 100

    def test_generate_complaint_returns_ticket(self):
        resp = client.post("/api/generate-complaint", json={
            "preset": "noida",
            "deposit": 54000,
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "success"
        assert body["ticketId"].startswith("NCH-2026-")


# ============================================================================
# Database-dependent CRUD tests
# ============================================================================

# A valid case payload for reuse
VALID_CASE = {
    "title": "Test Case — Noida Flat",
    "tenantName": "Test Tenant",
    "landlordName": "Test Landlord",
    "propertyAddress": "Flat 101, Sector 75, Noida",
    "city": "Noida",
    "monthlyRent": 15000,
    "depositPaid": 45000,
    "upiReference": "UPI-TEST-123",
    "vacateDate": "2026-11-01",
    "leaseText": "Clause 14: Mandatory 50% forfeiture.",
    "deductionsClaimed": [
        {"item": "Painting", "landlordClaim": 16000, "exempt": True},
        {"item": "Deep cleaning", "landlordClaim": 6500, "exempt": False, "fairRate": 1899},
    ],
}


def _db_available() -> bool:
    """Quick probe: can we reach MongoDB (live or mock fallback)?"""
    resp = client.get("/health")
    return resp.json()["database"]["mongodb"] in ("connected", "fallback_mock")


# Mark tests that require a live database
requires_db = pytest.mark.skipif(
    not _db_available(),
    reason="MongoDB not reachable — skipping database-dependent tests",
)


@requires_db
class TestCaseCRUD:
    """Full CRUD lifecycle tests (require a live MongoDB)."""

    def test_create_case(self):
        resp = client.post("/api/v1/cases", json=VALID_CASE)
        assert resp.status_code == 201
        body = resp.json()
        assert body["title"] == VALID_CASE["title"]
        assert body["tenantName"] == VALID_CASE["tenantName"]
        assert body["status"] == "draft"
        assert body["isDemo"] is False
        assert "id" in body
        assert "createdAt" in body

    def test_list_cases(self):
        # Create one first
        client.post("/api/v1/cases", json=VALID_CASE)
        resp = client.get("/api/v1/cases")
        assert resp.status_code == 200
        body = resp.json()
        assert "cases" in body
        assert body["count"] >= 1

    def test_get_case_by_id(self):
        create_resp = client.post("/api/v1/cases", json=VALID_CASE)
        case_id = create_resp.json()["id"]

        resp = client.get(f"/api/v1/cases/{case_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == case_id

    def test_update_case(self):
        create_resp = client.post("/api/v1/cases", json=VALID_CASE)
        case_id = create_resp.json()["id"]

        resp = client.patch(f"/api/v1/cases/{case_id}", json={
            "status": "in_progress",
            "monthlyRent": 20000,
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "in_progress"
        assert body["monthlyRent"] == 20000

    def test_get_nonexistent_case(self):
        resp = client.get("/api/v1/cases/000000000000000000000000")
        assert resp.status_code == 404

    def test_update_nonexistent_case(self):
        resp = client.patch("/api/v1/cases/000000000000000000000000", json={"title": "X"})
        assert resp.status_code == 404

    def test_update_case_no_fields(self):
        """PATCH with empty body should return 400."""
        create_resp = client.post("/api/v1/cases", json=VALID_CASE)
        case_id = create_resp.json()["id"]
        resp = client.patch(f"/api/v1/cases/{case_id}", json={})
        assert resp.status_code == 400

    def test_list_cases_filter_status(self):
        client.post("/api/v1/cases", json=VALID_CASE)
        resp = client.get("/api/v1/cases?status=draft")
        assert resp.status_code == 200
        for c in resp.json()["cases"]:
            assert c["status"] == "draft"

    def test_list_cases_invalid_status_filter(self):
        resp = client.get("/api/v1/cases?status=magic")
        assert resp.status_code == 400

    def test_delete_case(self):
        create_resp = client.post("/api/v1/cases", json=VALID_CASE)
        case_id = create_resp.json()["id"]
        # Delete the case
        del_resp = client.delete(f"/api/v1/cases/{case_id}")
        assert del_resp.status_code == 200
        assert del_resp.json()["status"] == "success"
        # Confirm it is gone
        get_resp = client.get(f"/api/v1/cases/{case_id}")
        assert get_resp.status_code == 404

    def test_delete_nonexistent_case(self):
        resp = client.delete("/api/v1/cases/000000000000000000000000")
        assert resp.status_code == 404


@requires_db
class TestEvidenceCRUD:
    """Evidence metadata tests (require a live MongoDB)."""

    def _create_case(self) -> str:
        resp = client.post("/api/v1/cases", json=VALID_CASE)
        return resp.json()["id"]

    def test_add_evidence(self):
        case_id = self._create_case()
        resp = client.post(f"/api/v1/cases/{case_id}/evidence", json={
            "evidenceType": "photo",
            "filename": "move_out_kitchen.jpg",
            "description": "Kitchen condition at move-out",
            "fileHash": "abc123def456",
        })
        assert resp.status_code == 201
        body = resp.json()
        assert body["caseId"] == case_id
        assert body["filename"] == "move_out_kitchen.jpg"
        assert body["evidenceType"] == "photo"

    def test_list_evidence_for_case(self):
        case_id = self._create_case()
        client.post(f"/api/v1/cases/{case_id}/evidence", json={
            "evidenceType": "document",
            "filename": "lease_agreement.pdf",
        })
        client.post(f"/api/v1/cases/{case_id}/evidence", json={
            "evidenceType": "chat_log",
            "filename": "whatsapp_export.txt",
        })

        resp = client.get(f"/api/v1/cases/{case_id}/evidence")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 2

    def test_add_evidence_to_nonexistent_case(self):
        resp = client.post("/api/v1/cases/000000000000000000000000/evidence", json={
            "evidenceType": "photo",
            "filename": "test.jpg",
        })
        assert resp.status_code == 404


# ============================================================================
# Database failure handling tests (mocked)
# ============================================================================

class TestDatabaseFailures:
    """Verify graceful handling when MongoDB is unreachable."""

    def test_create_case_db_down(self):
        """Should return 503 when DB insert fails."""
        mock_col = MagicMock()
        mock_col.insert_one.side_effect = ServerSelectionTimeoutError("timeout")
        with patch("backend_python.main.get_collection", return_value=mock_col):
            resp = client.post("/api/v1/cases", json=VALID_CASE)
            assert resp.status_code == 503
            assert "Database unavailable" in resp.json()["detail"]

    def test_list_cases_db_down(self):
        """Should return 503 when DB find fails."""
        mock_col = MagicMock()
        mock_col.find.side_effect = ServerSelectionTimeoutError("timeout")
        with patch("backend_python.main.get_collection", return_value=mock_col):
            resp = client.get("/api/v1/cases")
            assert resp.status_code == 503

    def test_get_case_db_down(self):
        mock_col = MagicMock()
        mock_col.find_one.side_effect = ServerSelectionTimeoutError("timeout")
        with patch("backend_python.main.get_collection", return_value=mock_col):
            resp = client.get("/api/v1/cases/000000000000000000000000")
            assert resp.status_code == 503

    def test_health_db_down(self):
        """Health should still return 200 but report disconnected DB."""
        with patch("backend_python.main.check_connection", return_value={
            "mongodb": "disconnected",
            "error": "connection refused",
        }):
            resp = client.get("/health")
            assert resp.status_code == 200
            assert resp.json()["database"]["mongodb"] == "disconnected"
