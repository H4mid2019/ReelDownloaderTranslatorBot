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

CHECK_URL = (
    "https://www.instagram.com/api/v1/users/web_profile_info/?username=instagram"
)
CHECK_HEADERS = {
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "x-ig-app-id": "936619743392459",
    "accept": "*/*",
    "accept-language": "en-US,en;q=0.9",
}

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
    try:
        r = requests.get(
            CHECK_URL,
            headers={**CHECK_HEADERS, "cookie": cookie_str},
            proxies=proxies,
            timeout=10,
        )
        return classify(r)
    except requests.RequestException as e:
        return f"error_{type(e).__name__}"


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
