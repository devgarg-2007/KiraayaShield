"""
KiraayaShield — MongoDB connection management.

Reads MONGODB_URI and MONGODB_DATABASE from environment variables
(with safe defaults for local development). Provides a singleton
client and convenience accessors for the database and collections.
"""

import os
import logging
from typing import Optional, Any

import certifi
from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError
import mongomock

load_dotenv()

logger = logging.getLogger("kiraayashield.database")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
MONGODB_URI: str = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
MONGODB_DATABASE: str = os.getenv("MONGODB_DATABASE", "kiraayashield")
ALLOW_MOCK_FALLBACK: bool = os.getenv("ALLOW_MOCK_FALLBACK", "true").lower() in ("true", "1", "yes")

# ---------------------------------------------------------------------------
# Singleton client & resilient mock fallback
# ---------------------------------------------------------------------------
_client: Optional[MongoClient] = None
_mock_client: Optional[Any] = None
_using_mock: bool = False
_atlas_error_detail: Optional[str] = None


def _get_mock_client():
    global _mock_client, _using_mock
    if _mock_client is None:
        _mock_client = mongomock.MongoClient()
        _using_mock = True
    return _mock_client


def get_client() -> Any:
    """Return the shared MongoClient, falling back to in-memory mock if live DB is unreachable."""
    global _client, _using_mock, _atlas_error_detail
    if os.getenv("USE_MOCK_DB", "").lower() in ("true", "1", "yes"):
        _using_mock = True
        return _get_mock_client()

    if _client is not None:
        return _client

    if _using_mock and _mock_client is not None:
        return _mock_client

    uri = os.getenv("MONGODB_URI", MONGODB_URI)
    client_kwargs = {
        "serverSelectionTimeoutMS": 3000,
        "connectTimeoutMS": 3000,
    }
    if uri.startswith("mongodb+srv://") or "ssl=true" in uri.lower() or "tls=true" in uri.lower():
        client_kwargs["tlsCAFile"] = certifi.where()

    try:
        candidate = MongoClient(uri, **client_kwargs)
        # Test connection immediately
        candidate.admin.command("ping")
        _client = candidate
        _using_mock = False
        _atlas_error_detail = None
        logger.info("Successfully connected to live MongoDB database.")
        return _client
    except Exception as exc:
        sanitized = _sanitize_error(str(exc))
        _atlas_error_detail = sanitized
        if ALLOW_MOCK_FALLBACK:
            logger.warning(
                "Live MongoDB Atlas unreachable (%s). Atlas IP Whitelist likely required. "
                "Falling back to in-memory mock database for continuous service.",
                sanitized,
            )
            _using_mock = True
            return _get_mock_client()
        logger.error("MongoDB Atlas connection failed and mock fallback disabled: %s", sanitized)
        raise exc


def get_database():
    """Return the application database handle."""
    db_name = os.getenv("MONGODB_DATABASE", MONGODB_DATABASE)
    return get_client()[db_name]


def get_collection(name: str):
    """Shortcut: return a collection from the application database."""
    return get_database()[name]


def _sanitize_error(error_msg: str) -> str:
    """Strip potential credentials or full connection strings from error messages."""
    import re
    sanitized = re.sub(r"://([^:]+):([^@]+)@", "://***:***@", str(error_msg))
    return sanitized


# ---------------------------------------------------------------------------
# Health probe
# ---------------------------------------------------------------------------
def check_connection() -> dict:
    """
    Ping MongoDB and return an explicit status dict.
    Clearly distinguishes:
      - 'connected_atlas': Live connection to MongoDB Atlas
      - 'local_mongodb': Live connection to local MongoDB
      - 'in_memory_fallback': In-memory mock database fallback
      - 'disconnected': No database reachable
    Never exposes credentials.
    """
    db_name = os.getenv("MONGODB_DATABASE", MONGODB_DATABASE)
    uri = os.getenv("MONGODB_URI", MONGODB_URI)
    is_atlas_uri = uri.startswith("mongodb+srv://") or "mongodb.net" in uri

    try:
        client = get_client()
        client.admin.command("ping")

        if _using_mock:
            database_mode = "in_memory_fallback"
            status_text = "fallback_mock"
            note = (
                "Running in in-memory mock database mode. Atlas IP access whitelist is pending. "
                "Data persists in-process while running, but will not sync to Atlas."
            )
        elif is_atlas_uri:
            database_mode = "connected_atlas"
            status_text = "connected"
            note = "Connected to live MongoDB Atlas cluster."
        else:
            database_mode = "local_mongodb"
            status_text = "connected"
            note = "Connected to local MongoDB instance."

        resp = {
            "mongodb": status_text,
            "database_mode": database_mode,
            "database": db_name,
            "is_in_memory": _using_mock,
            "note": note,
        }
        if _using_mock and _atlas_error_detail:
            # Provide high-level safe diagnostic
            if "TLSV1_ALERT_INTERNAL_ERROR" in _atlas_error_detail:
                resp["diagnostics"] = "Atlas rejected TLS handshake (IP address not in Atlas Network Access whitelist)."
            else:
                resp["diagnostics"] = "Live database server selection timeout."
        return resp
    except (ConnectionFailure, ServerSelectionTimeoutError) as exc:
        logger.warning("MongoDB connection check failed: %s", _sanitize_error(str(exc)))
        return {
            "mongodb": "disconnected",
            "database_mode": "disconnected",
            "database": db_name,
            "is_in_memory": False,
            "error": "MongoDB server unreachable",
        }
    except Exception as exc:
        logger.error("Unexpected error checking MongoDB: %s", _sanitize_error(str(exc)))
        return {
            "mongodb": "error",
            "database_mode": "error",
            "database": db_name,
            "is_in_memory": False,
            "error": "Database error",
        }


# ---------------------------------------------------------------------------
# Index setup (called once at startup)
# ---------------------------------------------------------------------------
def ensure_indexes():
    """Create indexes used by the application. Safe to call repeatedly."""
    try:
        db = get_database()
        db["cases"].create_index("created_at")
        db["cases"].create_index("status")
        db["evidence"].create_index("case_id")
        db["assessments"].create_index("case_id")
        db["investigations"].create_index("case_id")
        logger.info("MongoDB indexes ensured on database '%s'.", MONGODB_DATABASE)
    except Exception as exc:
        logger.warning("Could not create indexes (DB may be unavailable): %s", exc)


# ---------------------------------------------------------------------------
# Teardown
# ---------------------------------------------------------------------------
def close_client():
    """Close the MongoClient. Call on application shutdown."""
    global _client
    if _client is not None:
        _client.close()
        _client = None
        logger.info("MongoDB client closed.")
