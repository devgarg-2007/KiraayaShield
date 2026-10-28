"""
KiraayaShield — Evidence Verification & Cryptographic Hashing Service.

Provides:
  - Real SHA-256 calculation from actual file bytes
  - Honest EXIF metadata extraction using Pillow (never fabricates GPS/dates)
  - Clear cryptographic disclaimers (integrity vs authenticity)
  - Safe on-disk binary storage (avoiding MongoDB document size bloat)
"""

import os
import io
import hashlib
import logging
from typing import Dict, Any, Optional, Tuple
from PIL import Image, ExifTags

logger = logging.getLogger("kiraayashield.evidence")

UPLOAD_DIR_NAME = "uploads"
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def compute_sha256(file_bytes: bytes) -> str:
    """Compute true SHA-256 hexadecimal digest from raw bytes."""
    return hashlib.sha256(file_bytes).hexdigest()


def extract_real_metadata(file_bytes: bytes, filename: str) -> Dict[str, Any]:
    """
    Extract real image metadata and EXIF tags from bytes.
    Never invents GPS coordinates, timestamps, or device models.
    """
    sha256_hash = compute_sha256(file_bytes)
    metadata: Dict[str, Any] = {
        "filename": filename,
        "fileSizeBytes": len(file_bytes),
        "sha256": sha256_hash,
        "fileHash": sha256_hash,
        "isImage": False,
        "hasExif": False,
        "exifData": {},
        "integrityNote": (
            "SHA-256 cryptographic hash provides chain-of-custody data integrity. "
            "It proves the file has not been altered since upload, but does not independently "
            "prove when or where the photo was originally captured."
        ),
        "damageDetectionStatus": "Automated computer vision damage classification is currently in demonstration mode. No automated liability determination made.",
    }

    try:
        with Image.open(io.BytesIO(file_bytes)) as img:
            metadata["isImage"] = True
            metadata["imageWidth"] = img.width
            metadata["imageHeight"] = img.height
            metadata["imageFormat"] = img.format
            metadata["imageMode"] = img.mode

            # Extract EXIF if present
            raw_exif = img._getexif() if hasattr(img, "_getexif") else None
            if raw_exif:
                clean_exif: Dict[str, Any] = {}
                for tag_id, val in raw_exif.items():
                    tag_name = ExifTags.TAGS.get(tag_id, str(tag_id))
                    # Convert bytes to string or repr if necessary
                    if isinstance(val, bytes):
                        try:
                            val = val.decode("utf-8", errors="replace")
                        except Exception:
                            val = str(val)
                    elif not isinstance(val, (str, int, float, list, dict, bool)):
                        val = str(val)

                    # Only keep readable tags (skip huge raw binary dumps)
                    if len(str(val)) < 200:
                        clean_exif[tag_name] = val

                if clean_exif:
                    metadata["hasExif"] = True
                    metadata["exifData"] = clean_exif
                    metadata["captureDate"] = clean_exif.get("DateTimeOriginal") or clean_exif.get("DateTime")
                    metadata["cameraMake"] = clean_exif.get("Make")
                    metadata["cameraModel"] = clean_exif.get("Model")

    except Exception as exc:
        # Not an image or unreadable by PIL
        logger.debug("File %s not parsed as PIL image: %s", filename, exc)

    return metadata


def store_evidence_file(file_bytes: bytes, filename: str, case_id: str) -> Tuple[str, str]:
    """
    Save uploaded file bytes to project uploads/ folder.
    Falls back to /tmp/uploads/ if running on a read-only serverless filesystem (e.g. Vercel).
    Returns (relative_file_path, sha256_hash).
    """
    sha256_hash = compute_sha256(file_bytes)
    safe_name = os.path.basename(filename)

    try:
        case_folder = os.path.join(PROJECT_ROOT, UPLOAD_DIR_NAME, case_id)
        os.makedirs(case_folder, exist_ok=True)
        target_path = os.path.join(case_folder, safe_name)
        with open(target_path, "wb") as f:
            f.write(file_bytes)
        rel_path = os.path.relpath(target_path, PROJECT_ROOT)
    except OSError as err:
        logger.warning("Local upload directory unwritable (%s); falling back to /tmp/uploads", err)
        fallback_folder = os.path.join("/tmp", UPLOAD_DIR_NAME, case_id)
        os.makedirs(fallback_folder, exist_ok=True)
        target_path = os.path.join(fallback_folder, safe_name)
        with open(target_path, "wb") as f:
            f.write(file_bytes)
        rel_path = target_path

    return rel_path, sha256_hash
