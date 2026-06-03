"""
data/cache.py
═══════════════════════════════════════════════════════════════════════
SQLite-backed caching layer for the PSX Investment Advisor.

Stores arbitrary JSON-serializable values with insertion timestamps
so that callers can enforce per-key TTL expiration.  The database and
table are created automatically on first access.

Thread safety:  Each public function opens (and closes) its own
connection, so the module is safe for use from Flask request threads.

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

import json
import logging
import os
import sqlite3
import time
from typing import Any, Optional

from config import DB_PATH

logger = logging.getLogger(__name__)

# ── Schema ───────────────────────────────────────────────────────────

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS cache (
    key        TEXT PRIMARY KEY,
    value      TEXT    NOT NULL,
    created_at REAL   NOT NULL
);
"""

_CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_cache_created_at ON cache (created_at);
"""


# ── Internal Helpers ─────────────────────────────────────────────────


def _ensure_db_dir() -> None:
    """Create the directory for the database file if it doesn't exist."""
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)


def _get_connection() -> sqlite3.Connection:
    """
    Open a connection and ensure the schema exists.

    Returns:
        An open ``sqlite3.Connection`` with WAL journaling enabled.
    """
    _ensure_db_dir()
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute(_CREATE_TABLE_SQL)
    conn.execute(_CREATE_INDEX_SQL)
    conn.commit()
    return conn


# ── Public API ───────────────────────────────────────────────────────


def get_cached(key: str, ttl_seconds: int) -> Optional[Any]:
    """
    Retrieve a cached value if it exists and hasn't expired.

    Args:
        key:         Unique cache key (e.g. ``'quote:OGDC'``).
        ttl_seconds: Maximum age in seconds before the entry is
                     considered stale and ``None`` is returned.

    Returns:
        The deserialized Python object, or ``None`` if the key is
        missing or expired.
    """
    try:
        conn = _get_connection()
        try:
            cursor = conn.execute(
                "SELECT value, created_at FROM cache WHERE key = ?",
                (key,),
            )
            row = cursor.fetchone()
            if row is None:
                return None

            value_json, created_at = row
            age = time.time() - created_at

            if age > ttl_seconds:
                # Entry is stale — delete it proactively
                conn.execute("DELETE FROM cache WHERE key = ?", (key,))
                conn.commit()
                logger.debug("Cache EXPIRED for key '%s' (age=%.0fs, ttl=%ds)", key, age, ttl_seconds)
                return None

            logger.debug("Cache HIT for key '%s' (age=%.0fs)", key, age)
            return json.loads(value_json)
        finally:
            conn.close()
    except (sqlite3.Error, json.JSONDecodeError) as exc:
        logger.warning("Cache read error for key '%s': %s", key, exc)
        return None


def set_cached(key: str, value: Any) -> bool:
    """
    Store a JSON-serializable value in the cache.

    If the key already exists it is overwritten (upsert).

    Args:
        key:   Unique cache key.
        value: Any JSON-serializable Python object (dict, list, str, etc.).

    Returns:
        ``True`` on success, ``False`` if serialization or DB write failed.
    """
    try:
        value_json = json.dumps(value, default=str)  # default=str handles datetimes etc.
        conn = _get_connection()
        try:
            conn.execute(
                """
                INSERT INTO cache (key, value, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE
                    SET value = excluded.value,
                        created_at = excluded.created_at;
                """,
                (key, value_json, time.time()),
            )
            conn.commit()
            logger.debug("Cache SET for key '%s' (%d bytes)", key, len(value_json))
            return True
        finally:
            conn.close()
    except (sqlite3.Error, TypeError, ValueError) as exc:
        logger.warning("Cache write error for key '%s': %s", key, exc)
        return False


def clear_expired(max_age_seconds: int = 86400 * 7) -> int:
    """
    Delete all cache entries older than *max_age_seconds*.

    Args:
        max_age_seconds: Entries older than this many seconds are
                         removed.  Defaults to 7 days.

    Returns:
        Number of rows deleted.
    """
    try:
        cutoff = time.time() - max_age_seconds
        conn = _get_connection()
        try:
            cursor = conn.execute(
                "DELETE FROM cache WHERE created_at < ?",
                (cutoff,),
            )
            conn.commit()
            deleted = cursor.rowcount
            logger.info("Cache cleanup: removed %d expired entries (cutoff=%ds)", deleted, max_age_seconds)
            return deleted
        finally:
            conn.close()
    except sqlite3.Error as exc:
        logger.warning("Cache cleanup error: %s", exc)
        return 0


def clear_all() -> int:
    """
    Delete **all** entries in the cache.  Use with care.

    Returns:
        Number of rows deleted.
    """
    try:
        conn = _get_connection()
        try:
            cursor = conn.execute("DELETE FROM cache")
            conn.commit()
            deleted = cursor.rowcount
            logger.info("Cache fully cleared: removed %d entries", deleted)
            return deleted
        finally:
            conn.close()
    except sqlite3.Error as exc:
        logger.warning("Cache clear error: %s", exc)
        return 0


def cache_stats() -> dict:
    """
    Return basic statistics about the cache.

    Returns:
        Dict with keys ``total_entries``, ``oldest_entry_age_seconds``,
        ``newest_entry_age_seconds``, ``db_size_bytes``.
    """
    try:
        conn = _get_connection()
        try:
            row = conn.execute("SELECT COUNT(*), MIN(created_at), MAX(created_at) FROM cache").fetchone()
            total, oldest_ts, newest_ts = row
            now = time.time()
            db_size = os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0
            return {
                "total_entries": total,
                "oldest_entry_age_seconds": round(now - oldest_ts, 1) if oldest_ts else None,
                "newest_entry_age_seconds": round(now - newest_ts, 1) if newest_ts else None,
                "db_size_bytes": db_size,
            }
        finally:
            conn.close()
    except sqlite3.Error as exc:
        logger.warning("Cache stats error: %s", exc)
        return {"total_entries": 0, "error": str(exc)}
