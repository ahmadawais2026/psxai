"""
data/cache.py
═══════════════════════════════════════════════════════════════════════
Firestore-backed caching layer for the PSX Investment Advisor.

Stores arbitrary JSON-serializable values in a "cache" collection in
Cloud Firestore with insertion timestamps so that callers can enforce
per-key TTL expiration.

Thread safety and connection reuse are handled automatically by the
Firebase Admin SDK client.

Usage::

    from data.cache import get_cached, set_cached, clear_expired

    val = get_cached("quote:OGDC", ttl_seconds=300)
    if val is None:
        val = fetch_from_network(...)
        set_cached("quote:OGDC", val)

    clear_expired(max_age_seconds=86400)
═══════════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

import firebase_admin
from firebase_admin import firestore

logger = logging.getLogger(__name__)

# Ensure Firebase App is initialized
if not firebase_admin._apps:
    firebase_admin.initialize_app()

# Initialize Firestore Client
db = firestore.client()
cache_ref = db.collection("cache")

_credentials_valid = None

def _verify_credentials() -> bool:
    """Verify that Application Default Credentials are valid and can be refreshed."""
    global _credentials_valid
    if _credentials_valid is not None:
        return _credentials_valid
        
    try:
        import os
        # Bypass checks in production or local emulator contexts
        if os.getenv("K_SERVICE") or os.getenv("FUNCTIONS_EMULATOR") or os.getenv("FIRESTORE_EMULATOR_HOST"):
            _credentials_valid = True
            return True

        import google.auth
        import google.auth.transport.requests
        
        credentials, project = google.auth.default()
        if not hasattr(credentials, "refresh"):
            _credentials_valid = True
            return True
            
        request = google.auth.transport.requests.Request()
        credentials.refresh(request)
        _credentials_valid = True
    except Exception as exc:
        logger.warning(
            "[!] Firebase Credentials Warning: Application Default Credentials check failed: %s. "
            "Firestore cache operations will be bypassed. "
            "Run `gcloud auth application-default login` to fix credentials locally.", exc
        )
        _credentials_valid = False
        
    return _credentials_valid


# ── Public API ───────────────────────────────────────────────────────


def get_cached(key: str, ttl_seconds: int) -> Optional[Any]:
    """
    Retrieve a cached value if it exists and hasn't expired.

    Args:
        key:         Unique cache key (e.g. ``'quote:OGDC'``).
        ttl_seconds: Maximum age in seconds before the entry is
                     considered stale and ``None`` is returned.

    Returns:
        The cached Python object, or ``None`` if the key is
        missing or expired.
    """
    if not _verify_credentials():
        return None

    try:
        # Sanitize key for Firestore document ID (slashes/spaces)
        doc_id = key.replace("/", "_").replace(" ", "_")
        doc_ref = cache_ref.document(doc_id)
        doc = doc_ref.get()

        if not doc.exists:
            return None

        data = doc.to_dict() or {}
        created_at = data.get("created_at", 0.0)
        age = time.time() - created_at

        if age > ttl_seconds:
            # Entry is stale — delete it proactively
            doc_ref.delete()
            logger.debug("Cache EXPIRED for key '%s' (age=%.0fs, ttl=%ds)", key, age, ttl_seconds)
            return None

        logger.debug("Cache HIT for key '%s' (age=%.0fs)", key, age)
        return data.get("value")
    except Exception as exc:
        logger.warning("Cache read error for key '%s': %s", key, exc)
        return None


def set_cached(key: str, value: Any) -> bool:
    """
    Store a value in the Firestore cache.

    If the key already exists it is overwritten (upsert).

    Args:
        key:   Unique cache key.
        value: Any JSON-serializable Python object (dict, list, str, etc.).

    Returns:
        ``True`` on success, ``False`` if DB write failed.
    """
    if not _verify_credentials():
        return False
    try:
        doc_id = key.replace("/", "_").replace(" ", "_")
        cache_ref.document(doc_id).set({
            "key": key,
            "value": value,
            "created_at": time.time()
        })
        logger.debug("Cache SET for key '%s'", key)
        return True
    except Exception as exc:
        logger.warning("Cache write error for key '%s': %s", key, exc)
        return False


def clear_expired(max_age_seconds: int = 86400 * 7) -> int:
    """
    Delete all cache entries older than *max_age_seconds*.

    Args:
        max_age_seconds: Entries older than this many seconds are
                         removed. Defaults to 7 days.

    Returns:
        Number of documents deleted.
    """
    if not _verify_credentials():
        return 0
    try:
        cutoff = time.time() - max_age_seconds
        # Firestore query to fetch expired documents
        expired_docs = cache_ref.where("created_at", "<", cutoff).stream()
        deleted = 0
        
        # Batch deletes are more efficient (max 500 per batch)
        batch = db.batch()
        for doc in expired_docs:
            batch.delete(doc.reference)
            deleted += 1
            if deleted % 500 == 0:
                batch.commit()
                batch = db.batch()
                
        if deleted % 500 != 0:
            batch.commit()

        if deleted > 0:
            logger.info("Cache cleanup: removed %d expired entries", deleted)
        return deleted
    except Exception as exc:
        logger.warning("Cache cleanup error: %s", exc)
        return 0


def clear_all() -> int:
    """
    Delete **all** entries in the cache. Use with care.

    Returns:
        Number of documents deleted.
    """
    if not _verify_credentials():
        return 0
    try:
        docs = cache_ref.stream()
        deleted = 0
        
        batch = db.batch()
        for doc in docs:
            batch.delete(doc.reference)
            deleted += 1
            if deleted % 500 == 0:
                batch.commit()
                batch = db.batch()
                
        if deleted % 500 != 0:
            batch.commit()

        logger.info("Cache fully cleared: removed %d entries", deleted)
        return deleted
    except Exception as exc:
        logger.warning("Cache clear error: %s", exc)
        return 0


def cache_stats() -> dict:
    """
    Return basic statistics about the cache.

    Returns:
        Dict with keys ``total_entries``, ``oldest_entry_age_seconds``,
        ``newest_entry_age_seconds``.
    """
    try:
        docs = list(cache_ref.stream())
        total = len(docs)
        if total == 0:
            return {"total_entries": 0, "oldest_entry_age_seconds": None, "newest_entry_age_seconds": None}

        created_ats = [doc.to_dict().get("created_at", 0.0) for doc in docs]
        now = time.time()
        oldest_ts = min(created_ats)
        newest_ts = max(created_ats)

        return {
            "total_entries": total,
            "oldest_entry_age_seconds": round(now - oldest_ts, 1) if oldest_ts else None,
            "newest_entry_age_seconds": round(now - newest_ts, 1) if newest_ts else None,
        }
    except Exception as exc:
        logger.warning("Cache stats error: %s", exc)
        return {"total_entries": 0, "error": str(exc)}
