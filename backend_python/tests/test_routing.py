"""
KiraayaShield — Tests for Vercel and API Routing.

Verifies:
  1. GET /health returns application status and database mode.
  2. Multi-path aliasing (/api/health, /api, /api/index.py) routes to health check.
  3. Vercel rewrite mapping via __path__ query parameter preserves target endpoints.
  4. Vercel edge proxy mapping via x-matched-path header preserves target endpoints.
  5. Vercel /api/copilot/chat routing correctly executes Copilot chat handler.
  6. Vercel /api/v1/... routing correctly executes Case management handlers.
  7. api/index.py entrypoint routes requests identically to backend_python.main:app.
"""

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from backend_python.main import app as main_app
import api.index as api_index

client = TestClient(main_app)
api_client = TestClient(api_index.app)


class TestHealthRouting:
    """Verify health endpoint responses and database mode reporting."""

    def test_direct_health_endpoint(self):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("healthy", "ok")
        assert data["service"] == "KiraayaShield-API"
        assert "database" in data
        assert "database_mode" in data
        assert data["database_mode"] in ("connected_atlas", "local_mongodb", "in_memory_fallback", "disconnected", "unknown")

    def test_api_health_alias(self):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("healthy", "ok")
        assert "database_mode" in data

    def test_api_root_alias(self):
        resp = client.get("/api")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("healthy", "ok")

    def test_api_index_py_direct_fallback(self):
        resp = client.get("/api/index.py")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("healthy", "ok")


class TestVercelRewrites:
    """Verify Vercel serverless request path restoration."""

    def test_health_rewritten_via_query_param(self):
        resp = client.get("/api/index.py?__path__=/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("healthy", "ok")
        assert "database_mode" in data

    def test_health_rewritten_via_x_matched_path_header(self):
        resp = client.get("/api/index.py", headers={"x-matched-path": "/health"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("healthy", "ok")
        assert "database_mode" in data

    def test_cases_rewritten_via_query_param(self):
        resp = client.get("/api/index.py?__path__=/api/v1/cases&limit=5")
        assert resp.status_code == 200
        data = resp.json()
        assert "cases" in data
        assert "count" in data

    def test_cases_rewritten_via_x_matched_path_header(self):
        resp = client.get("/api/index.py?limit=5", headers={"x-matched-path": "/api/v1/cases"})
        assert resp.status_code == 200
        data = resp.json()
        assert "cases" in data

    def test_copilot_chat_rewritten_via_query_param(self):
        with patch("backend_python.main.chat_with_copilot") as mock_copilot:
            mock_copilot.return_value = {
                "reply": "Tenant security deposit is protected under Section 7.",
                "model": "mocked-model",
                "citations": ["Draft Model Tenancy Act"],
            }
            resp = client.post(
                "/api/index.py?__path__=/api/copilot/chat",
                json={"message": "What is the deposit limit?"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert "reply" in data
            assert data["model"] == "mocked-model"

    def test_copilot_chat_rewritten_via_x_matched_path(self):
        with patch("backend_python.main.chat_with_copilot") as mock_copilot:
            mock_copilot.return_value = {
                "reply": "Dispute notice response generated.",
                "model": "mocked-model",
                "citations": [],
            }
            resp = client.post(
                "/api/index.py",
                headers={"x-matched-path": "/api/copilot/chat"},
                json={"message": "How to dispute deduction?"},
            )
            assert resp.status_code == 200
            assert resp.json()["model"] == "mocked-model"

    def test_api_index_entrypoint_matches_main_app(self):
        resp = api_client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] in ("healthy", "ok")

        resp_rewrite = api_client.get("/api/index.py?__path__=/health")
        assert resp_rewrite.status_code == 200
        assert resp_rewrite.json()["status"] in ("healthy", "ok")
