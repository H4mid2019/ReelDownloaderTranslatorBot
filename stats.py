"""
Lightweight per-download statistics logger.

Records every successful download attempt (timestamp, url type, winning method)
to the existing ai_cache.db SQLite file. Used by the /report Telegram command.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Dict, Optional

from config import CACHE_DB_PATH

_logger = logging.getLogger(__name__)
_init_done = False


@contextmanager
def _conn():
    c = sqlite3.connect(CACHE_DB_PATH)
    try:
        yield c
        c.commit()
    finally:
        c.close()


def _ensure_schema() -> None:
    global _init_done
    if _init_done:
        return
    try:
        with _conn() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS download_stats (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts          INTEGER NOT NULL,
                    platform    TEXT    NOT NULL,
                    url_type    TEXT    NOT NULL,
                    method      TEXT    NOT NULL,
                    success     INTEGER NOT NULL,
                    duration_ms INTEGER DEFAULT 0,
                    error       TEXT
                )
            """)
            c.execute("CREATE INDEX IF NOT EXISTS idx_stats_ts ON download_stats(ts)")
        _init_done = True
    except Exception as e:
        _logger.warning(f"stats schema init failed: {e}")


def classify(url: str, platform: str) -> str:
    """Return a short bucket name for grouping in reports."""
    if platform == "instagram":
        if "/reel" in url:
            return "reel"
        if "/tv/" in url:
            return "igtv"
        if "/p/" in url:
            return "post"
        return "ig_other"
    if platform == "twitter":
        return "tweet"
    if platform == "youtube":
        return "youtube"
    return platform or "unknown"


def log(
    platform: str,
    url: str,
    method: str,
    success: bool,
    duration_ms: int = 0,
    error: Optional[str] = None,
) -> None:
    """Record one method invocation. Never raises."""
    try:
        _ensure_schema()
        with _conn() as c:
            c.execute(
                "INSERT INTO download_stats "
                "(ts, platform, url_type, method, success, duration_ms, error) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    int(time.time()),
                    platform,
                    classify(url, platform),
                    method,
                    1 if success else 0,
                    duration_ms,
                    (error or "")[:300],
                ),
            )
    except Exception as e:
        _logger.warning(f"stats log failed: {e}")


def track(platform: str, url: str, method: str, fn, *args, **kwargs):
    """
    Time and log a download method call. Returns whatever fn returns.
    Expects fn to return a MediaResult (with .error attribute) or raise.
    """
    t0 = time.monotonic()
    try:
        res = fn(*args, **kwargs)
    except Exception as e:
        log(platform, url, method, False,
            int((time.monotonic() - t0) * 1000), str(e))
        raise
    dur_ms = int((time.monotonic() - t0) * 1000)
    err = getattr(res, "error", None)
    log(platform, url, method, not err, dur_ms, err)
    return res


def parse_range(spec: str) -> Optional[int]:
    """
    Parse range like '1m', '1d', '20d', '12h', '30s' to seconds.
    Returns None if unparseable.
    """
    m = re.fullmatch(r"\s*(\d+)\s*([smhdwM])\s*", spec)
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    mult = {
        "s": 1,
        "m": 60,           # minutes — treat lowercase m as minutes? See below
        "h": 3600,
        "d": 86400,
        "w": 604800,
        "M": 30 * 86400,   # months (uppercase M)
    }
    # User asked for 1m=month, 1d=day. Override 'm' to month for this command.
    mult["m"] = 30 * 86400
    return n * mult[unit]


def report(seconds: int) -> Dict[str, Any]:
    """
    Return aggregated stats for the last N seconds.
    Structure:
      {
        "total_success": int,
        "total_attempts": int,
        "by_url_type": {
            "reel": {"total": N, "methods": {"yt-dlp-desktop": 5, ...}},
            ...
        },
        "failed_methods": {method: count, ...},
      }
    """
    _ensure_schema()
    cutoff = int(time.time()) - seconds
    out: Dict[str, Any] = {
        "total_success": 0,
        "total_attempts": 0,
        "by_url_type": {},
        "failed_methods": {},
    }
    try:
        with _conn() as c:
            # Total attempts
            row = c.execute(
                "SELECT COUNT(*) FROM download_stats WHERE ts >= ?",
                (cutoff,),
            ).fetchone()
            out["total_attempts"] = row[0]

            # Successes grouped by url_type + method
            for url_type, method, n in c.execute(
                "SELECT url_type, method, COUNT(*) "
                "FROM download_stats WHERE ts >= ? AND success = 1 "
                "GROUP BY url_type, method",
                (cutoff,),
            ):
                bucket = out["by_url_type"].setdefault(
                    url_type, {"total": 0, "methods": {}}
                )
                bucket["methods"][method] = n
                bucket["total"] += n
                out["total_success"] += n

            # Failures by method (for diagnostics)
            for method, n in c.execute(
                "SELECT method, COUNT(*) FROM download_stats "
                "WHERE ts >= ? AND success = 0 GROUP BY method",
                (cutoff,),
            ):
                out["failed_methods"][method] = n
    except Exception as e:
        _logger.warning(f"stats report failed: {e}")
    return out


def format_report(seconds: int, range_label: str) -> str:
    """Format stats as a Telegram-friendly plain text report."""
    r = report(seconds)
    lines = [f"Download report — last {range_label}"]
    lines.append(f"Total successful: {r['total_success']}")
    lines.append(f"Total attempts:   {r['total_attempts']}")
    lines.append("")
    if not r["by_url_type"]:
        lines.append("No downloads in this period.")
    else:
        for url_type in sorted(r["by_url_type"].keys()):
            bucket = r["by_url_type"][url_type]
            lines.append(f"{url_type}: {bucket['total']}")
            for method in sorted(bucket["methods"].keys()):
                lines.append(f"  - {method}: {bucket['methods'][method]}")
            lines.append("")
    if r["failed_methods"]:
        lines.append("Failures (per method):")
        for method, n in sorted(r["failed_methods"].items(), key=lambda x: -x[1]):
            lines.append(f"  - {method}: {n}")
    return "\n".join(lines).rstrip()
