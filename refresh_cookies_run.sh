#!/bin/bash
# Refresh Instagram cookies via CloakBrowser (Docker, residential IP).
# On success: swaps new cookies into cookies1.txt + cobalt_cookies.json,
# rebuilds the instaloader session, and restarts Cobalt.
#
# Reads IG_USERNAME / IG_PASSWORD / IG_TOTP_SECRET from .env.

set -euo pipefail
cd "$(dirname "$0")"

PROJECT_DIR="$(pwd)"
OUT_DIR="$PROJECT_DIR/.cookie_refresh_out"
BOT="$(grep '^TELEGRAM_BOT_TOKEN=' .env | cut -d= -f2- | tr -d '"')"
CHAT="$(grep '^ADMIN_CHAT_ID=' .env | cut -d= -f2-)"

notify() {
    [ -n "$BOT" ] && [ -n "$CHAT" ] && \
    curl -s -X POST "https://api.telegram.org/bot${BOT}/sendMessage" \
        -d chat_id="$CHAT" -d text="$1" >/dev/null 2>&1 || true
}

# Load credentials from .env
IG_USERNAME="$(grep '^IG_USERNAME=' .env | cut -d= -f2- | tr -d '"')"
IG_PASSWORD="$(grep '^IG_PASSWORD=' .env | cut -d= -f2- | tr -d '"')"
IG_TOTP_SECRET="$(grep '^IG_TOTP_SECRET=' .env | cut -d= -f2- | tr -d '"')"

if [ -z "$IG_USERNAME" ] || [ -z "$IG_PASSWORD" ]; then
    echo "IG_USERNAME / IG_PASSWORD missing in .env"; exit 2
fi

rm -rf "$OUT_DIR"; mkdir -p "$OUT_DIR"

echo "Running CloakBrowser login for $IG_USERNAME ..."
docker run --rm --network wg_net \
    -e IG_USERNAME="$IG_USERNAME" \
    -e IG_PASSWORD="$IG_PASSWORD" \
    -e IG_TOTP_SECRET="$IG_TOTP_SECRET" \
    -e OUTPUT_DIR=/output \
    -v "$PROJECT_DIR/refresh_cookies.py:/refresh_cookies.py:ro" \
    -v "$OUT_DIR:/output" \
    cloakhq/cloakbrowser python /refresh_cookies.py
RC=$?

if [ "$RC" -ne 0 ] || [ ! -f "$OUT_DIR/cookies_new.txt" ]; then
    echo "Refresh failed (rc=$RC)"
    notify "Instagram cookie refresh FAILED (rc=$RC). Check .cookie_refresh_out/login_debug.png"
    exit "$RC"
fi

echo "Login succeeded — swapping cookies into place"
# Back up then replace
cp cookies1.txt "cookies1.txt.bak.$(date +%s)" 2>/dev/null || true
cp "$OUT_DIR/cookies_new.txt" cookies1.txt
cp "$OUT_DIR/cobalt_cookies_new.json" cobalt_cookies.json

# Rebuild instaloader session from the fresh cookie
.venv/bin/python - "$IG_USERNAME" << 'PYEOF'
import sys, http.cookiejar, os, instaloader
username = sys.argv[1]
L = instaloader.Instaloader(quiet=True)
L.context._session.proxies = {"http": "http://127.0.0.1:3128", "https": "http://127.0.0.1:3128"}
cj = http.cookiejar.MozillaCookieJar("cookies1.txt"); cj.load(ignore_discard=True, ignore_expires=True)
for c in cj:
    L.context._session.cookies.set(c.name, c.value, domain=c.domain)
who = L.test_login()
if who:
    L.context.username = who
    path = os.path.expanduser(f"~/.config/instaloader/session-{who.lower()}")
    L.save_session_to_file(filename=path)
    print(f"instaloader session saved for {who} -> {path}")
else:
    print("WARNING: instaloader test_login returned None after refresh")
PYEOF

# Cobalt is currently cookie-free; only restart if it's configured with cookies.
if docker inspect cobalt --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null | grep -q COOKIE_PATH; then
    echo "Restarting Cobalt to reload cookies"
    docker restart cobalt >/dev/null 2>&1 || true
fi

echo "Cookie refresh complete."
notify "Instagram cookies refreshed successfully for $IG_USERNAME ✅"
