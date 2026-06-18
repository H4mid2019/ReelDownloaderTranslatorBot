"""
Per-cookie-file Instagram session health checker.

Run periodically via cron. For each cookie file in INSTAGRAM_COOKIES_FILES,
sends a single tiny request through the WireGuard residential proxy and
classifies the response. Sends a Telegram alert ONLY when state changes,
to avoid spam.

States per cookie:
  - alive          200 OK with user data
  - expired        401/403 with login_required
  - checkpoint     400 with challenge_required (needs human)
  - rate_limited   429 or feedback_required
  - unknown        anything else (logged with status code)

State is persisted in cookie_health_state.json next to this script.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from typing import Dict, Optional

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import (  # noqa: E402
    ADMIN_CHAT_ID,
    INSTAGRAM_COOKIES_FILES,
    RESIDENTIAL_PROXY,
    TELEGRAM_BOT_TOKEN,
)

_HERE = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(_HERE, "cookie_health_state.json")

# Auto-refresh: when the primary cookie expires, run the CloakBrowser refresh.
# Only cookies1.txt is auto-refreshable (the account we hold credentials for);
# cookies2/3 are other accounts and only get an alert.
AUTO_REFRESH = os.getenv("AUTO_REFRESH_COOKIES", "true").lower() in ("1", "true", "yes")
REFRESH_SCRIPT = os.path.join(_HERE, "refresh_cookies_run.sh")
REFRESH_TARGET = "cookies1.txt"
REFRESH_LOCK = os.path.join(_HERE, ".last_auto_refresh")
REFRESH_COOLDOWN_SEC = int(os.getenv("AUTO_REFRESH_COOLDOWN_SEC", str(6 * 3600)))

# Rotate probe targets + user-agents so Instagram doesn't pattern-throttle
# the hourly health check. Same UA + same target + same endpoint every hour
# was getting blanket 429s and confusing the auto-refresh trigger.
_PROBE_TARGETS = ["instagram", "natgeo", "nasa", "nike", "9gag", "bbc"]
_PROBE_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:130.0) "
    "Gecko/20100101 Firefox/130.0",
]


def _build_probe() -> tuple[str, dict]:
    """Pick a (URL, headers) tuple — rotates by hour so probes don't look
    like a fixed pattern to Instagram."""
    import hashlib
    import time as _t
    bucket = int(_t.time()) // 3600
    target = _PROBE_TARGETS[bucket % len(_PROBE_TARGETS)]
    h = int(hashlib.md5(f"{bucket}".encode()).hexdigest(), 16)
    ua = _PROBE_UAS[h % len(_PROBE_UAS)]
    url = f"https://www.instagram.com/api/v1/users/web_profile_info/?username={target}"
    headers = {
        "user-agent": ua,
        "x-ig-app-id": "936619743392459",
        "accept": "*/*",
        "accept-language": "en-US,en;q=0.9",
    }
    return url, headers

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO
)
log = logging.getLogger("cookie_health")


def load_state() -> Dict[str, str]:
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state: Dict[str, str]) -> None:
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def cookies_to_string(filepath: str) -> Optional[str]:
    try:
        out = []
        with open(filepath) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) >= 7:
                    out.append(f"{parts[5]}={parts[6]}")
        return "; ".join(out) if out else None
    except FileNotFoundError:
        return None


def classify(resp: requests.Response) -> str:
    if resp.status_code == 200:
        try:
            if "data" in resp.json():
                return "alive"
        except Exception:
            pass
        return "unknown"
    if resp.status_code == 429:
        return "rate_limited"
    text = resp.text.lower()
    if "login_required" in text:
        return "expired"
    if "challenge_required" in text or "checkpoint" in text:
        return "checkpoint"
    if "feedback_required" in text or "spam" in text:
        return "rate_limited"
    return f"unknown_{resp.status_code}"


def check_cookie(filepath: str) -> str:
    cookie_str = cookies_to_string(filepath)
    if not cookie_str:
        return "missing"
    proxies = (
        {"http": RESIDENTIAL_PROXY, "https": RESIDENTIAL_PROXY}
        if RESIDENTIAL_PROXY
        else None
    )
    # First attempt with this hour's rotating probe.
    url, headers = _build_probe()
    try:
        r = requests.get(
            url,
            headers={**headers, "cookie": cookie_str},
            proxies=proxies,
            timeout=10,
        )
        result = classify(r)
    except requests.RequestException as e:
        return f"error_{type(e).__name__}"

    # Disambiguate: if we got 429-like rate_limited, our PROBE was throttled
    # by IG's pattern detection — that doesn't tell us the cookie's state.
    # Retry once with a different probe to check the cookie itself.
    if result == "rate_limited":
        import random as _r
        import time as _t
        _t.sleep(2 + _r.random() * 3)  # small jitter, ~2-5s
        alt_url, alt_headers = _build_probe()
        # Force a different probe than the first attempt.
        if alt_url == url:
            alt_url = alt_url.replace("?username=", "?username=") + "z"  # bogus query
            alt_url = url.split("?")[0] + "?username=" + (
                "natgeo" if "instagram" in url else "instagram"
            )
        try:
            r2 = requests.get(
                alt_url,
                headers={**alt_headers, "cookie": cookie_str},
                proxies=proxies,
                timeout=10,
            )
            result2 = classify(r2)
            # If the second probe succeeds, the cookie is fine — our first
            # probe was just throttled. Trust the second result.
            if result2 != "rate_limited":
                return result2
        except requests.RequestException:
            pass

    return result


def telegram(message: str) -> None:
    if not (TELEGRAM_BOT_TOKEN and ADMIN_CHAT_ID):
        log.warning("telegram not configured, skipping notification")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data={"chat_id": ADMIN_CHAT_ID, "text": message},
            timeout=10,
        )
    except Exception as e:
        log.warning(f"telegram send failed: {e}")


def _refresh_on_cooldown() -> bool:
    """True if a refresh was attempted within the cooldown window."""
    try:
        return (time.time() - os.path.getmtime(REFRESH_LOCK)) < REFRESH_COOLDOWN_SEC
    except OSError:
        return False


def trigger_refresh() -> str:
    """Run refresh_cookies_run.sh. Returns a short status string."""
    # Touch the lock first so a failing refresh doesn't retry every hour.
    try:
        open(REFRESH_LOCK, "w").close()
    except OSError:
        pass
    if not os.path.exists(REFRESH_SCRIPT):
        return "refresh script missing"
    try:
        r = subprocess.run(
            ["bash", REFRESH_SCRIPT],
            capture_output=True, text=True, timeout=300, cwd=_HERE,
        )
        if r.returncode == 0:
            return "ok"
        tail = (r.stdout or r.stderr or "").strip().splitlines()[-1:] or [""]
        return f"failed rc={r.returncode}: {tail[0][:120]}"
    except subprocess.TimeoutExpired:
        return "timeout"
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"


def main() -> int:
    if not INSTAGRAM_COOKIES_FILES:
        log.warning("no cookie files configured")
        return 0

    state = load_state()
    new_state = {}
    changes = []

    for cf in INSTAGRAM_COOKIES_FILES:
        if not os.path.exists(cf):
            new_state[cf] = "missing"
            log.warning(f"{cf}: file missing")
            continue

        cur = check_cookie(cf)
        prev = state.get(cf, "unknown")
        new_state[cf] = cur
        log.info(f"{cf}: {cur} (was {prev})")

        name = os.path.basename(cf)

        # Auto-refresh the primary cookie when it expires (independent of the
        # state-change gate, so a persistently-expired cookie keeps retrying
        # after each cooldown window — not just on the first transition).
        if (
            cur == "expired"
            and name == REFRESH_TARGET
            and AUTO_REFRESH
            and not _refresh_on_cooldown()
        ):
            log.info(f"{name} expired — triggering auto-refresh")
            result = trigger_refresh()
            log.info(f"auto-refresh result: {result}")
            if result == "ok":
                new_cur = check_cookie(cf)
                new_state[cf] = new_cur
                changes.append(f"REFRESHED {name}: auto-refresh -> {new_cur}")
            else:
                changes.append(f"REFRESH FAILED {name}: {result}")
            continue  # handled — skip the generic state-change messaging

        if cur != prev:
            if cur == "alive" and prev != "unknown":
                changes.append(f"OK {name}: recovered (was {prev})")
            elif cur == "expired":
                changes.append(f"EXPIRED {name}: needs refresh")
            elif cur == "checkpoint":
                changes.append(
                    f"CHECKPOINT {name}: human verification needed in Instagram app"
                )
            elif cur == "rate_limited":
                changes.append(f"RATE-LIMITED {name}: backing off (transient)")
            elif cur.startswith("unknown") or cur.startswith("error"):
                changes.append(f"WARN {name}: {cur}")

    save_state(new_state)

    if changes:
        msg = "Cookie health update:\n" + "\n".join(changes)
        telegram(msg)
        log.info(f"sent telegram: {msg}")
    else:
        log.info("no state changes")

    return 0


if __name__ == "__main__":
    sys.exit(main())
