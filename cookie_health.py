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
import sys
from typing import Dict, Optional

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import (  # noqa: E402
    ADMIN_CHAT_ID,
    INSTAGRAM_COOKIES_FILES,
    RESIDENTIAL_PROXY,
    TELEGRAM_BOT_TOKEN,
)

STATE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "cookie_health_state.json"
)

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

        if cur != prev:
            name = os.path.basename(cf)
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
