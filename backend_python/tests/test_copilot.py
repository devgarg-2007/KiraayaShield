"""
KiraayaShield — Tests for AI Copilot Chat Endpoint & Groq Service Integration.
"""

from unittest.mock import patch, MagicMock
import pytest
from fastapi.testclient import TestClient
from groq import GroqError

from backend_python.main import app
from backend_python.groq_service import chat_with_copilot, GroqServiceError

client = TestClient(app)


# ============================================================================
# Unit & Integration Tests for /api/copilot/chat (Mocked Groq)
# ============================================================================

def test_copilot_chat_empty_message_rejected():
    """Sending an empty or whitespace message must return 400/422 validation error."""
    resp = client.post("/api/copilot/chat", json={"message": "   "})
    assert resp.status_code in (400, 422)


def test_copilot_chat_missing_message_field():
    """Sending a payload without message field must fail validation."""
    resp = client.post("/api/copilot/chat", json={})
    assert resp.status_code == 422


@patch("backend_python.groq_service._get_groq_client")
def test_copilot_chat_success_mocked(mock_get_client):
    """Custom message must call Groq and return structured response."""
    mock_client = MagicMock()
    mock_completion = MagicMock()
    mock_completion.choices = [
        MagicMock(message=MagicMock(content="Under Section 11 of the Model Tenancy Act 2021, residential security deposits are capped at 2 months' rent."))
    ]
    mock_client.chat.completions.create.return_value = mock_completion
    mock_get_client.return_value = mock_client

    resp = client.post("/api/copilot/chat", json={
        "message": "What is the maximum lock-in period for a tenancy?",
        "history": [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello, how can I assist you?"}
        ]
    })

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert "Section 11" in data["reply"]
    assert data["hasCaseContext"] is False
    assert mock_client.chat.completions.create.called


@patch("backend_python.groq_service._get_groq_client")
def test_copilot_chat_suggested_questions(mock_get_client):
    """Suggested question macros must trigger actual backend requests to Groq, not canned answers."""
    mock_client = MagicMock()
    mock_completion = MagicMock()
    mock_completion.choices = [
        MagicMock(message=MagicMock(content="Routine painting is landlord liability under Schedule II."))
    ]
    mock_client.chat.completions.create.return_value = mock_completion
    mock_get_client.return_value = mock_client

    suggested_questions = [
        "Can landlord cut 1 month rent for painting?",
        "How to file on National Consumer Helpline?",
        "What if PG owner refuses to give receipt?",
    ]

    for q in suggested_questions:
        resp = client.post("/api/copilot/chat", json={"message": q})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "success"
        assert len(body["reply"]) > 0


@patch("backend_python.groq_service._get_groq_client")
def test_copilot_chat_with_case_context(mock_get_client):
    """Case context must be accepted and flagged in response."""
    mock_client = MagicMock()
    mock_completion = MagicMock()
    mock_completion.choices = [
        MagicMock(message=MagicMock(content="For your Noida flat, the landlord's 32,000 claim is excessive."))
    ]
    mock_client.chat.completions.create.return_value = mock_completion
    mock_get_client.return_value = mock_client

    resp = client.post("/api/copilot/chat", json={
        "message": "Can the landlord deduct for repainting in my case?",
        "caseContext": {
            "city": "Noida",
            "monthlyRent": 18000,
            "depositPaid": 54000,
            "landlordClaimTotal": 32000,
            "fairDeductionTotal": 2349,
            "tenantName": "Kavita Reddy",
        }
    })

    assert resp.status_code == 200
    data = resp.json()
    assert data["hasCaseContext"] is True
    assert "Noida" in data["reply"]


@patch("backend_python.groq_service._get_groq_client")
def test_copilot_chat_groq_api_failure_shows_error(mock_get_client):
    """If Groq fails, must return 503 error, never a fabricated answer."""
    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = GroqError("503 Service Unavailable / Rate Limit")
    mock_get_client.return_value = mock_client

    resp = client.post("/api/copilot/chat", json={
        "message": "Is Section 11 applicable in Karnataka?"
    })

    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert "Groq" in detail
    assert "failed" in detail.lower() or "unavailable" in detail.lower()


@patch("backend_python.groq_service._get_groq_client")
def test_copilot_chat_malformed_empty_model_response(mock_get_client):
    """If Groq returns empty content across all models, must raise error rather than fabricate."""
    mock_client = MagicMock()
    mock_completion = MagicMock()
    mock_completion.choices = [MagicMock(message=MagicMock(content=""))]
    mock_client.chat.completions.create.return_value = mock_completion
    mock_get_client.return_value = mock_client

    resp = client.post("/api/copilot/chat", json={
        "message": "What is normal wear and tear?"
    })

    assert resp.status_code == 503
    assert "failed" in resp.json()["detail"].lower()
