"""
Instagram cookie refresh via CloakBrowser (anti-detect Chromium).

Runs INSIDE the cloakhq/cloakbrowser Docker container, on the wg_net network
so the login originates from the home residential IP. Logs in with
username + password + TOTP, extracts the fresh session cookies, and writes
them to the mounted /output directory in two formats:
  - cookies_new.txt        (Netscape — for gallery-dl / yt-dlp / instaloader)
  - cobalt_cookies_new.json (Cobalt COOKIE_PATH format)

A host-side wrapper (refresh_cookies_run.sh) runs this container, then on
success swaps the new files into place and reloads the dependent services.

Credentials are read from environment variables:
  IG_USERNAME, IG_PASSWORD, IG_TOTP_SECRET (base32)

Exit codes: 0 = success, 2 = bad/missing creds, 3 = login failed,
4 = could not extract sessionid.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import struct
import sys
import time


def totp_now(secret_b32: str) -> str:
    """Generate the current 6-digit TOTP code from a base32 secret (RFC 6238)."""
    # Normalize: strip spaces, uppercase, pad to a multiple of 8 for base32.
    s = secret_b32.replace(" ", "").upper()
    s += "=" * (-len(s) % 8)
    key = base64.b32decode(s)
    counter = int(time.time()) // 30
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code_int = (struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{code_int:06d}"


def log(msg: str) -> None:
    print(f"[refresh] {msg}", flush=True)


def main() -> int:
    username = os.getenv("IG_USERNAME", "").strip()
    password = os.getenv("IG_PASSWORD", "")
    totp_secret = os.getenv("IG_TOTP_SECRET", "").strip()

    if not username or not password:
        log("IG_USERNAME / IG_PASSWORD not set")
        return 2

    out_dir = os.getenv("OUTPUT_DIR", "/output")
    os.makedirs(out_dir, exist_ok=True)

    from cloakbrowser import launch

    log(f"launching stealth browser for {username!r}")
    browser = launch(headless=True)
    page = browser.new_page()
    context = page.context

    try:
        page.goto("https://www.instagram.com/accounts/login/", timeout=60000,
                  wait_until="domcontentloaded")

        # Dismiss cookie-consent dialog if present (EU).
        for label in ("Allow all cookies", "Accept all", "Allow"):
            try:
                page.get_by_role("button", name=label).click(timeout=2500)
                break
            except Exception:
                pass

        # Instagram serves two field-naming variants: username/password OR email/pass.
        user_sel = _first_present(page, ['input[name="username"]', 'input[name="email"]'])
        pass_sel = _first_present(page, ['input[name="password"]', 'input[name="pass"]'])
        if not user_sel or not pass_sel:
            log("login form fields not found")
            _dump_debug(page, out_dir)
            return 3

        log(f"filling credentials (fields: {user_sel}, {pass_sel})")
        page.fill(user_sel, username)
        page.fill(pass_sel, password)
        # Submit: prefer the submit button, else press Enter on the password field.
        try:
            page.click('button[type="submit"]', timeout=3000)
        except Exception:
            try:
                page.click('input[type="submit"]', timeout=3000)
            except Exception:
                page.press(pass_sel, "Enter")

        # Wait for either the 2FA prompt or a logged-in state.
        page.wait_for_timeout(6000)

        if "two_factor" in page.url or _has_2fa_input(page):
            if not totp_secret:
                log("2FA prompted but IG_TOTP_SECRET not set")
                return 3
            code = totp_now(totp_secret)
            log(f"entering TOTP code {code}")
            sel = _find_2fa_selector(page)
            if not sel:
                log("could not locate 2FA input")
                return 3
            page.fill(sel, code)
            # Submit (button text varies: Confirm / Continue / Next)
            for label in ("Confirm", "Continue", "Next", "Submit"):
                try:
                    page.get_by_role("button", name=label).click(timeout=2500)
                    break
                except Exception:
                    pass
            page.wait_for_timeout(6000)

        # Dismiss "Save info" / "Turn on notifications" interstitials.
        for label in ("Not now", "Not Now", "Save info", "Dismiss"):
            try:
                page.get_by_role("button", name=label).click(timeout=2500)
            except Exception:
                pass

        cookies = context.cookies("https://www.instagram.com")
        cookie_map = {c["name"]: c for c in cookies}

        if "sessionid" not in cookie_map or not cookie_map["sessionid"]["value"]:
            log("login did not yield a sessionid — wrong password / checkpoint?")
            _dump_debug(page, out_dir)
            return 4

        log(f"logged in — sessionid present, {len(cookies)} cookies")
        _write_netscape(cookies, os.path.join(out_dir, "cookies_new.txt"))
        _write_cobalt(cookie_map, os.path.join(out_dir, "cobalt_cookies_new.json"))
        log("wrote cookies_new.txt and cobalt_cookies_new.json")
        return 0

    finally:
        try:
            browser.close()
        except Exception:
            pass


def _first_present(page, selectors: list, timeout_ms: int = 30000):
    """Return the first selector that becomes visible within the timeout."""
    import time as _time
    deadline = _time.monotonic() + timeout_ms / 1000.0
    while _time.monotonic() < deadline:
        for sel in selectors:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    return sel
            except Exception:
                pass
        page.wait_for_timeout(500)
    return None


def _has_2fa_input(page) -> bool:
    for sel in ('input[name="verificationCode"]', 'input[autocomplete="one-time-code"]'):
        try:
            if page.query_selector(sel):
                return True
        except Exception:
            pass
    return False


def _find_2fa_selector(page):
    for sel in ('input[name="verificationCode"]', 'input[autocomplete="one-time-code"]'):
        try:
            if page.query_selector(sel):
                return sel
        except Exception:
            pass
    return None


def _write_netscape(cookies: list, path: str) -> None:
    lines = ["# Netscape HTTP Cookie File\n"]
    for c in cookies:
        domain = c.get("domain", ".instagram.com")
        include_sub = "TRUE" if domain.startswith(".") else "FALSE"
        secure = "TRUE" if c.get("secure") else "FALSE"
        expiry = int(c.get("expires") or 0)
        if expiry <= 0:
            expiry = int(time.time()) + 60 * 60 * 24 * 365
        lines.append(
            f"{domain}\t{include_sub}\t{c.get('path','/')}\t{secure}\t"
            f"{expiry}\t{c['name']}\t{c['value']}\n"
        )
    with open(path, "w") as f:
        f.writelines(lines)


def _write_cobalt(cookie_map: dict, path: str) -> None:
    cookie_str = "; ".join(f"{n}={c['value']}" for n, c in cookie_map.items())
    with open(path, "w") as f:
        json.dump({"instagram": [cookie_str]}, f, indent=4)


def _dump_debug(page, out_dir: str) -> None:
    try:
        page.screenshot(path=os.path.join(out_dir, "login_debug.png"))
        with open(os.path.join(out_dir, "login_debug.html"), "w") as f:
            f.write(page.content())
        log("saved login_debug.png / .html for inspection")
    except Exception:
        pass


if __name__ == "__main__":
    sys.exit(main())
