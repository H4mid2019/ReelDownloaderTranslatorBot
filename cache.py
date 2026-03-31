"""
SQLite-backed cache for AI results (transcripts, translations, caption translations).

- Cache key for transcripts : "transcript:{platform}:{post_id}"  (stable post shortcode/tweet ID)
- Cache key for translations: "translation:{text_hash16}:{lang}" (SHA-256 of source text)
- TTL                        : configurable via CACHE_TTL_DAYS (default 30 days)
- Cleanup                    : purge_expired() is called once at bot startup
- Thread-safety              : SQLite WAL mode + check_same_thread=False
"""

import hashlib
import json
import logging
import re
import sqlite3
import time
from typing import Optional

logger = logging.getLogger(__name__)

# ── URL pattern → (platform, post_id) extraction ──────────────────────────────

_INSTAGRAM_RE = re.compile(
    r"(?:instagram\.com|instagr\.am)/(?:reel|reels|p|tv)/([\w-]+)", re.IGNORECASE
)
_TWITTER_RE = re.compile(r"(?:twitter\.com|x\.com)/\w+/status/(\d+)", re.IGNORECASE)
_YOUTUBE_RE = re.compile(
    r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)([\w-]+)",
    re.IGNORECASE,
)


def extract_post_id(url: str) -> Optional[tuple[str, str]]:
    """
    Extract a stable (platform, post_id) tuple from a supported URL.

    Returns:
        ("instagram", "ABC123xyz") for Instagram posts/reels
        ("twitter",   "1234567890") for Twitter/X statuses
        ("youtube",   "dQw4w9WgXcQ") for YouTube videos
        None if URL is not recognized
    """
    m = _INSTAGRAM_RE.search(url)
    if m:
        return ("instagram", m.group(1))
    m = _TWITTER_RE.search(url)
    if m:
        return ("twitter", m.group(1))
    m = _YOUTUBE_RE.search(url)
    if m:
        return ("youtube", m.group(1))
    return None


def make_text_hash(text: str) -> str:
    """Return a 16-char hex prefix of the SHA-256 hash of the first 2000 chars of text."""
    return hashlib.sha256(text[:2000].encode("utf-8", errors="replace")).hexdigest()[
        :16
    ]


# ── SQLite cache class ─────────────────────────────────────────────────────────

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS ai_cache (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    created_at REAL NOT NULL,
    hits       INTEGER DEFAULT 0
);
"""

_CREATE_INDEX = "CREATE INDEX IF NOT EXISTS idx_created_at ON ai_cache(created_at);"


class AICache:
    """
    Persistent SQLite cache for AI call results.

    Usage:
        cache = AICache("./ai_cache.db", ttl_days=30)

        # Store
        cache.set("transcript:instagram:ABC123", result_dict)

        # Retrieve  (None on miss or expired)
        result = cache.get("transcript:instagram:ABC123")
    """

    def __init__(self, db_path: str, ttl_days: int = 30):
        self.db_path = db_path
        self.ttl_seconds = ttl_days * 86400
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._conn.executescript(_CREATE_TABLE + _CREATE_INDEX)
        self._conn.commit()
        logger.info(f"AICache initialized: {db_path} (TTL={ttl_days}d)")

    # ── Public API ─────────────────────────────────────────────────────────────

    def get(self, key: str) -> Optional[dict]:
        """
        Return the cached dict for *key*, or None on cache miss / expiry.
        Increments the hit counter on a successful read.
        """
        cutoff = time.time() - self.ttl_seconds
        row = self._conn.execute(
            "SELECT value FROM ai_cache WHERE key = ? AND created_at >= ?",
            (key, cutoff),
        ).fetchone()

        if row is None:
            return None

        try:
            self._conn.execute(
                "UPDATE ai_cache SET hits = hits + 1 WHERE key = ?", (key,)
            )
            self._conn.commit()
            return json.loads(row[0])
        except Exception as e:
            logger.warning(f"AICache: failed to decode entry for key={key!r}: {e}")
            return None

    def set(self, key: str, value: dict) -> None:
        """
        Insert or replace the cached value for *key*.
        Only stores if value is a non-empty dict.
        """
        if not value or not isinstance(value, dict):
            return
        try:
            self._conn.execute(
                "INSERT OR REPLACE INTO ai_cache (key, value, created_at, hits) VALUES (?, ?, ?, 0)",
                (key, json.dumps(value, ensure_ascii=False), time.time()),
            )
            self._conn.commit()
        except Exception as e:
            logger.warning(f"AICache: failed to store key={key!r}: {e}")

    def purge_expired(self) -> int:
        """
        Delete all entries older than TTL.
        Returns the number of rows deleted.
        """
        cutoff = time.time() - self.ttl_seconds
        cur = self._conn.execute("DELETE FROM ai_cache WHERE created_at < ?", (cutoff,))
        self._conn.commit()
        removed = cur.rowcount
        if removed:
            logger.info(
                f"AICache: purged {removed} expired entries (TTL={self.ttl_seconds // 86400}d)"
            )
        return removed

    def clear_all(self) -> int:
        """Delete ALL entries regardless of TTL. Returns rows deleted."""
        cur = self._conn.execute("DELETE FROM ai_cache")
        self._conn.commit()
        logger.info(f"AICache: cleared all {cur.rowcount} entries")
        return cur.rowcount

    def stats(self) -> dict:
        """Return a dict with total entries, cumulative hits, and oldest entry age."""
        cutoff = time.time() - self.ttl_seconds
        row = self._conn.execute(
            "SELECT COUNT(*), SUM(hits), MIN(created_at) FROM ai_cache"
        ).fetchone()
        expired = self._conn.execute(
            "SELECT COUNT(*) FROM ai_cache WHERE created_at < ?", (cutoff,)
        ).fetchone()[0]

        total = row[0] or 0
        total_hits = row[1] or 0
        oldest_ts = row[2]
        oldest_age_days = (
            round((time.time() - oldest_ts) / 86400, 1) if oldest_ts else 0
        )
        return {
            "total": total,
            "expired": expired,
            "valid": total - expired,
            "total_hits": total_hits,
            "oldest_age_days": oldest_age_days,
        }

    def close(self):
        """Close the underlying SQLite connection."""
        try:
            self._conn.close()
        except Exception:
            pass
