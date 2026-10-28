"""
KiraayaShield — Integration Tests for Groq LLM and TinyFish Research.

Tests:
  - Groq service output validation (categories, readiness score, disclaimer)
  - TinyFish service output validation (hyperlocal benchmarks, sources, citations)
  - End-to-end FastAPI endpoints for analysis and investigations
"""

import os
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from backend_python.main import app
from backend_python.models import (
    AssessmentResult,
    InvestigationResult,
    DeductionItem,
)
from backend_python.groq_service import (
    analyze_lease_with_groq,
    _deterministic_fallback_assessment,
    PRIMARY_MODEL,
)
from backend_python.tinyfish_service import (
    benchmark_hyperlocal_deductions,
    DEFAULT_BENCHMARK_CATALOG,
)

client = TestClient(app)


class TestGroqService:
    """Unit and integration tests for Groq LLM module."""

    def test_deterministic_fallback_schema(self):
        """Fallback assessment must satisfy all Pydantic constraints and statutory categories."""
        res = _deterministic_fallback_assessment(
            lease_text="Clause 14: Tenant shall forfeit 50% deposit for painting.",
            monthly_rent=20000,
            deposit_paid=60000,
            city="Noida",
            deductions=[
                {"item": "Painting", "landlord_claim": 16000, "exempt": True},
                {"item": "Deep Cleaning", "landlord_claim": 6500, "fair_rate": 1899},
            ],
        )
        assert isinstance(res, AssessmentResult)
        assert 0.0 <= res.case_readiness_score <= 100.0
        assert res.risk_score is not None
        assert len(res.findings) >= 2
        for f in res.findings:
            assert f.category in {"Supported", "Needs Justification", "Potentially Excessive", "Contradictory"}
            assert f.severity in {"LOW", "MODERATE", "HIGH", "CRITICAL"}
        assert "not constitute formal legal advice" in res.disclaimer

    def test_groq_service_live_or_fallback(self):
        """Calling analyze_lease_with_groq should succeed and return validated AssessmentResult."""
        res = analyze_lease_with_groq(
            lease_text="Clause 4: 3 months deposit. Clause 14: Tenant forfeits 50% for painting.",
            monthly_rent=18000,
            deposit_paid=54000,
            city="Noida",
        )
        assert isinstance(res, AssessmentResult)
        assert res.case_readiness_score >= 0
        assert len(res.findings) > 0


class TestTinyFishService:
    """Unit and integration tests for TinyFish research module."""

    def test_catalog_integrity(self):
        """Verify baseline benchmark catalog contains required rental items."""
        assert len(DEFAULT_BENCHMARK_CATALOG) >= 4
        patterns = [c["pattern"] for c in DEFAULT_BENCHMARK_CATALOG]
        assert "cleaning" in patterns
        assert "paint" in patterns

    def test_hyperlocal_deduction_benchmarks(self):
        """Verify benchmark calculation and output schema."""
        res = benchmark_hyperlocal_deductions(
            city="Noida",
            claimed_items=[
                {"item": "1BHK / Studio Full Move-Out Deep Cleaning", "landlordClaim": 6500},
                {"item": "Interior Wall Touch-Up & Distemper Paint (1 Room)", "landlordClaim": 16000},
            ],
        )
        assert isinstance(res, InvestigationResult)
        assert res.city == "Noida"
        assert len(res.items) == 2
        assert res.total_landlord_claim == 22500.0
        assert res.total_disallowed_amount > 0
        for it in res.items:
            assert it.landlord_claim > 0
            assert it.fair_market_rate > 0
            assert it.source_url.startswith("http")


class TestAPIEndpoints:
    """API endpoint verification for Phase 2 & Phase 3."""

    def test_analyze_lease_endpoint(self):
        resp = client.post("/api/analyze-lease", json={
            "leaseText": "Clause 14: 50% deduction for painting.",
            "monthlyRent": 15000,
            "depositPaid": 45000,
            "city": "Bengaluru",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "success"
        assert "findings" in body
        assert "caseReadinessScore" in body

    def test_scrape_rates_endpoint(self):
        resp = client.post("/api/scrape-rates", json={
            "city": "Noida",
            "items": [
                {"item": "Wall Painting", "landlordClaim": 15000, "exempt": True}
            ]
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "success"
        assert "benchmarks" in body
        assert len(body["benchmarks"]) >= 1

    def test_case_lifecycle_with_analysis_and_investigation(self):
        # 1. Create case
        create_resp = client.post("/api/v1/cases", json={
            "title": "Full Cycle Case",
            "tenantName": "Kavita Reddy",
            "landlordName": "Ramesh",
            "propertyAddress": "Indiranagar, Bengaluru",
            "city": "Bengaluru",
            "monthlyRent": 25000,
            "depositPaid": 75000,
            "leaseText": "Clause 14: Repainting mandatory charge.",
            "deductionsClaimed": [
                {"item": "Painting", "landlordClaim": 20000, "exempt": True}
            ]
        })
        assert create_resp.status_code == 201
        case_id = create_resp.json()["id"]

        # 2. Analyze case
        analyze_resp = client.post(f"/api/v1/cases/{case_id}/analyze")
        assert analyze_resp.status_code == 200
        assert analyze_resp.json()["status"] == "success"

        # 3. Retrieve assessment
        get_ass_resp = client.get(f"/api/v1/cases/{case_id}/assessment")
        assert get_ass_resp.status_code == 200
        assert get_ass_resp.json()["caseId"] == case_id

        # 4. Investigate case
        inv_resp = client.post(f"/api/v1/cases/{case_id}/investigate")
        assert inv_resp.status_code == 200
        assert len(inv_resp.json()["items"]) >= 1

        # 5. Clean up
        del_resp = client.delete(f"/api/v1/cases/{case_id}")
        assert del_resp.status_code == 200
