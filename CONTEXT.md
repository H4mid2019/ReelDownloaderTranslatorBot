# ReelDownloaderTranslatorBot — Context Document

**Purpose:** Single source of truth for resuming work on this project in a future
chat. Captures architecture, configuration, infrastructure, and known gotchas
that aren't obvious from reading the code.

**Repo:** `H4mid2019/ReelDownloaderTranslatorBot` · main branch · GitHub.
**Server:** Oracle Cloud Free Tier, Ubuntu ARM aarch64. Running as `ubuntu` user.
**Project root:** `/home/ubuntu/telebots_projects/ReelDownloaderTranslatorBot`
**Python:** 3.12, venv at `.venv/`. `python-telegram-bot`, `yt-dlp`, `instaloader`,
`gallery-dl`, `groq`, `openai`, `requests`.

---

## 1. What the bot does

A Telegram bot with three feature surfaces:

1. **Media downloader** — accepts Instagram (reels, posts, IGTV, carousels),
   X/Twitter, YouTube URLs in any text/caption. Downloads the media, transcribes
   audio, and translates non-English content to English.
2. **Truth Social monitor** — background loop that polls
   `trumpstruth.org` RSS for new Trump posts, classifies via Groq if they relate
   to Iran, and sends Persian-translated alerts to a supergroup.
3. **Stats & health** — admin commands and cron-driven monitors covering cookie
   expiration, WireGuard tunnel health, and per-method download statistics.

---

## 2. Telegram commands

All commands are registered in [bot.py](bot.py#L1627):

| Command | Description | Auth |
|---------|-------------|------|
| `/start` | Greeting | Public |
| `/help` | Usage help | Public |
| `/chatid` | Returns current chat ID — used to discover supergroup IDs after migration | Public |
| `/d <url>` | Manual download path using Groq (primary AI) | Public |
| `/dl <url>` | Manual download path using Google AI Studio fallback | Public |
| `/db <url>` | Detailed brief — transcript (verbatim, source language) + summary/highlights/takeaways (in `RESPONSE_LANGUAGE`) | Public |
| `/dbs <url>` | Same as `/db` PLUS visual / vocal / text sentiment cues. Uses `GOOGLE_AI_MODEL_SENTIMENT` (Pro). Works for Instagram, X, YouTube | Public |
| `/setcookie <sessionid>` | Update Instagram session ID at runtime + persist to .env | Admin only |
| `/clearcache` | Wipe AI cache (`ai_cache` table) | Admin only |
| `/report <range>` | Per-method download stats. Range: `30s` `12h` `1d` `7d` `1m` (=30d) `3M` (=months) | Admin only |

Plain text/caption messages with a supported URL are auto-handled by
`handle_text_message` — no command needed.

---

## 3. Code layout

```
bot.py                    Telegram entry point, command handlers, message router
downloader.py             ALL download methods + the fallback-chain orchestrator
config.py                 .env loader; all env-var-derived constants
cache.py                  SQLite AI result cache (transcripts, translations)
stats.py                  Per-download stats logger + /report formatter
cookie_health.py          Standalone cron — probes each cookie file; auto-refreshes on expiry
diagnose.py               Standalone CLI — runs every method against a URL
refresh_cookies.py        Runs INSIDE cloakbrowser Docker — logs in + extracts cookies
refresh_cookies_run.sh    Host wrapper — runs the container, swaps cookies, notifies
transcriber.py            Whisper (Groq) + Gemini fallback
translator.py             Groq LLM + Gemini fallback for any-→-English
truth_monitor.py          Background asyncio loop polling trumpstruth.org RSS
video_brief.py            Gemini brief + sentiment (/db, /dbs); hybrid-language output
youtube_summarizer.py     YouTube oEmbed + Gemini summary
test_*.py                 Manual test scripts (not pytest)
```

---

## 4. The download fallback chain

In [downloader.py:download_video](downloader.py#L1080).

### Instagram `/p/` posts (photos, sliders, mixed):
1. `gallery-dl` × `cookies1.txt` (caption capture works here)
2. `gallery-dl` × `cookies2.txt`
3. `gallery-dl` × `cookies3.txt`
4. **Cobalt local** (Docker, exits via residential IP)
5. **instaloader** (uses session file at `~/.config/instaloader/session-<lowercased_username>`)
6. **Cobalt public mirrors** (mostly dead since Nov 2024)
7. **HikerAPI** (paid, last resort)

### Instagram `/reel/` `/tv/`, X/Twitter, YouTube:
1. **yt-dlp Desktop** (curl-cffi impersonation chrome-131)
2. **yt-dlp Mobile**
3. **Cobalt local**
4. **Cobalt public mirrors**
5. **instaloader**
6. **HikerAPI** (paid)
7. **gallery-dl**

### Cookie/session rotation
On any `login`/`401`/`403` error, `rotate_cookie_file()` and `rotate_session_id()`
shift the active credential to the next entry in the pool. Configured via
`INSTAGRAM_COOKIES_FILES` (comma-separated) and `INSTAGRAM_SESSION_IDS`.

---

## 5. Infrastructure

### WireGuard residential proxy
The Oracle datacenter IP is blocked by Instagram. To get a residential exit,
WireGuard tunnels traffic through the user's home router (RT-AX86U).

```
Oracle server (130.61.180.78) ←─── WireGuard tunnel ───→ Home router (RT-AX86U)
   wg0 = 10.99.0.1                                       wgc5 = 10.99.0.2
                                                          exits as 151.251.106.23 (home IP)
```

- Server is the WG **server** (the router connects outbound — home is behind CGNAT)
- WG config: `/etc/wireguard/wg0.conf`. Listen port 51820/UDP. Service:
  `wg-quick@wg0` (enabled at boot)
- **Selective routing:** packets from Docker network `wg_net` (172.30.0.0/16)
  get `fwmark 0x1` → routed via `wgtable` → exits through `wg0`. SSH and
  everything else use the normal Oracle interface.
- Router has manual iptables rules (NOT persisted across router reboot — see
  [wireguard_router_fix.md](wireguard_router_fix.md))
- Monitor: `/home/ubuntu/wg_monitor.sh` runs every 5 min via cron, sends
  Telegram alert if forwarding breaks. Also self-heals `AllowedIPs` drift.

### Docker containers (relevant ones)
| Container | Image | Purpose |
|-----------|-------|---------|
| `cobalt` | `ghcr.io/imputnet/cobalt:11` | Self-hosted Cobalt API (`127.0.0.1:9000`). On `wg_net`. **COOKIE-FREE** (no `COOKIE_PATH`) — public content works via residential IP; stale cookies were *hurting* it, so we run it cookieless |
| `wg_proxy` | `kalaksi/tinyproxy` | HTTP proxy on `127.0.0.1:3128` for yt-dlp / gallery-dl / instaloader. On `wg_net` |
| `cloakhq/cloakbrowser` | (image, not a running container) | Anti-detect Chromium for cookie refresh. Run one-shot on `wg_net` (home IP) by `refresh_cookies_run.sh`. ARM64 ✓, ~568MB, bundles all libs |

### Tor (legacy, may be unused)
- `tor` service active on `172.17.0.1:9050` (SOCKS5)
- `HTTPTunnelPort` on `172.17.0.1:9080` (HTTP CONNECT — replaces Privoxy)
- Was used before WireGuard for a residential IP; Tor exit nodes are blocked by
  Instagram, so the WG tunnel superseded it. Kept active for other use cases.

### Cron jobs
```
*/5 * * * * /home/ubuntu/wg_monitor.sh
0 * * * * cd <project> && .venv/bin/python cookie_health.py >> cookie_health.log 2>&1
```

### Systemd unit
`/etc/systemd/system/insta-reel-bot.service` — runs `bot.py`.
Logs to `output.logs` (project dir). Restart: `sudo systemctl restart insta-reel-bot`.

---

## 6. External services & API keys

All keys in `.env` (gitignored). Get current values with `grep KEY= .env`.

| Service | Env var | Purpose |
|---------|---------|---------|
| Telegram Bot API | `TELEGRAM_BOT_TOKEN` | Bot identity |
| Groq | `GROQ_API_KEY` | Whisper STT + LLM translation (`/d`) |
| Google AI Studio | `GEMINI_API_KEY` | Fallback transcription/translation (`/dl`), summaries, video briefs |
| HikerAPI | `HIKERAPI_KEY` | Paid Instagram fallback. **Base URL:** `api.hikerapi.com/v1/media/by/url` (NOT `hikerapi.com/api/...` — that returns 404) |

### Other notable env vars
| Var | Default | Purpose |
|-----|---------|---------|
| `RESPONSE_LANGUAGE` | `fa` | UI/analysis language for briefs + YouTube summaries. `/db` `/dbs` labels + summary/highlights/takeaways/sentiment all render in this language (transcript stays source-language) |
| `GOOGLE_AI_MODEL` | `gemini-2.5-flash-lite` | Default Gemini model |
| `GOOGLE_AI_MODEL_SENTIMENT` | `gemini-2.5-pro` | Model for `/dbs` (Lite drops sentiment fields; Pro honors the schema) |
| `RESIDENTIAL_PROXY` | `http://127.0.0.1:3128` | tinyproxy → home IP, used by yt-dlp/gallery-dl/instaloader |
| `IG_USERNAME` / `IG_PASSWORD` / `IG_TOTP_SECRET` | — | Credentials for the cookie-refresh login (account `h4mid2026`). TOTP is a base32 seed |
| `AUTO_REFRESH_COOKIES` | `true` | When `cookies1.txt` expires, auto-run CloakBrowser refresh |
| `AUTO_REFRESH_COOLDOWN_SEC` | `21600` (6h) | Min gap between auto-refresh attempts |

### Telegram chat IDs
- `ADMIN_CHAT_ID = 619904882` — Hamid's personal chat (admin commands, alerts)
- `TRUTH_ALERT_CHAT_ID = -1003954813646` — supergroup "اتاق خبر" (Newsroom).
  **This was migrated from a regular group; the ID changes when this happens.**
  If you see `Group migrated to supergroup` errors, update this var.

---

## 7. Cookie / session credentials

**Active account:** `h4mid2026` (ds_user_id `38432121443`). NOT `MiddleEastPoliticalAnalyst`
(that's stale in `INSTAGRAM_USERNAME` — historical, ignore it). `INSTALOADER_SESSION_USER=h4mid2026`.

| File / var | Purpose | Notes |
|------------|---------|-------|
| `cookies1.txt` | Primary Netscape cookie (account `h4mid2026`) | The one the auto-refresh regenerates. Proven for media fetch |
| `cookies2.txt`, `cookies3.txt` | Secondary cookies | Currently degraded (pass profile check, fail media fetch). Different/old sessions — not auto-refreshable |
| `cobalt_cookies.json` | Cobalt JSON format `{"instagram": ["k=v; ..."]}` | **Currently UNUSED** — Cobalt runs cookie-free. Kept in sync by refresh for easy switch-back |
| `~/.config/instaloader/session-h4mid2026` | Native instaloader session | Filename is **lowercase**. Rebuilt by the refresh from `cookies1.txt` |
| `INSTAGRAM_SESSION_IDS` | Comma-separated raw session IDs (alternative) | Bot writes a temp Netscape cookie file from this |

**Refresh — now automated** (CloakBrowser, see §9). Manual run:
```bash
./refresh_cookies_run.sh   # logs in h4mid2026 via home IP, swaps cookies1.txt + cobalt json + instaloader session
```
First login from a new fingerprint triggers a one-time IG **checkpoint** — clear it once in the
Instagram app ("Was this you?"), then logins from the same residential IP + fingerprint are trusted.

---

## 8. SQLite database (`ai_cache.db`)

| Table | Purpose |
|-------|---------|
| `ai_cache` | AI results cache (transcripts, translations). Keyed by post ID + content hash. TTL via `CACHE_TTL_DAYS` (default 30) |
| `download_stats` | One row per method invocation. Used by `/report`. Columns: `ts`, `platform`, `url_type` (reel/post/igtv/tweet/youtube), `method`, `success`, `duration_ms`, `error` |

Maintenance:
- `/clearcache` — wipes `ai_cache`
- `sqlite3 ai_cache.db "DELETE FROM download_stats; VACUUM;"` — wipe stats

---

## 9. Background monitors

### Cookie health + auto-refresh (`cookie_health.py`)
Hourly cron. For each cookie file, sends one HTTPS request via `wg_proxy` to
`https://www.instagram.com/api/v1/users/web_profile_info/?username=instagram`.
Classifies into states: `alive`, `expired`, `checkpoint`, `rate_limited`,
`unknown_<code>`, `error_<exception>`. State persisted in
`cookie_health_state.json`. **Telegram alert only on state CHANGE.**

**Auto-refresh:** when `cookies1.txt` is `expired`, it runs `refresh_cookies_run.sh`
automatically (CloakBrowser login → fresh cookies). Guards: only on `expired`
(never `rate_limited`/`checkpoint`), only `cookies1.txt` (the account we have
creds for), 6h cooldown lock (`.last_auto_refresh`) to prevent re-login loops.
Disable with `AUTO_REFRESH_COOKIES=false`.

### Cookie refresh pipeline (`refresh_cookies.py` + `refresh_cookies_run.sh`)
`refresh_cookies_run.sh` (host) runs `cloakhq/cloakbrowser` one-shot on `wg_net`
(home IP) executing `refresh_cookies.py` inside it. That script: opens IG login,
fills user/pass (handles both `username/password` and `email/pass` field
variants), generates the TOTP code (RFC 6238, stdlib), submits, extracts the
fresh cookies → writes Netscape + Cobalt-JSON to a mounted dir. The wrapper then
backs up the old cookie, swaps the new one into `cookies1.txt` + `cobalt_cookies.json`,
rebuilds the instaloader session, restarts Cobalt only if it's cookie-configured,
and Telegram-notifies. Auto-rollback: if login fails, the old cookie stays.

### WireGuard tunnel monitor (`wg_monitor.sh`)
Every 5 min. Calls `curl --interface wg0 ifconfig.me` and verifies the result
differs from server's direct IP. Self-heals `AllowedIPs` drift; alerts on
forwarding failure with copy-paste router commands.

### Truth Social monitor (`truth_monitor.py`, in-process asyncio)
Started by `bot.py` `post_init`. Polls RSS, dedupes via `last_truth_id.txt`,
classifies relevance to Iran via Groq, translates to Persian, posts to
`TRUTH_ALERT_CHAT_ID` supergroup.

### Instagram cookie health (in-process, separate from above)
Bot also has `instagram_cookie_health_loop` (in `bot.py`) that pings Cobalt
every few hours. Different from the standalone `cookie_health.py` script.

---

## 10. Known gotchas / footguns

1. **`AllowedIPs` drift on WG reload.** `/etc/wireguard/wg0.conf` must have
   `AllowedIPs = 0.0.0.0/0` (not `10.99.0.2/32`). Reverting causes "internet
   traffic doesn't reach home" without ping breaking. Self-heal is in place.
2. **Cobalt v7 mirrors are dead** (Nov 2024). Public mirror fallback step is
   essentially a no-op now. Not removed because it's harmless.
3. **HikerAPI base URL pitfall.** Use `api.hikerapi.com/v1/...` NOT `hikerapi.com/api/v1/...`.
   The wrong URL silently returns 404 and was burning the chain straight to
   "all methods failed" — fixed but easy to regress.
4. **instaloader session file is lowercased.** `~/.config/instaloader/session-<user>`
   uses lowercased username regardless of how `INSTALOADER_SESSION_USER` is set.
5. **Telegram supergroup migration changes chat ID.** When a group is upgraded
   to supergroup, all `chat_id`s change. Bot will throw
   `Group migrated to supergroup. New chat id: -100xxxxx`. Update `.env`.
6. **Local `import time` inside `download_video()` causes F823 UnboundLocalError.**
   Module-level `import time` exists; never re-import inside the function.
   Caught by ruff F823.
7. **Cookie monitor false positive on transient `error_ReadTimeout`.** Brief
   network hiccups trigger an alert. Recovers automatically next cycle.
8. **The bot must be restarted after code changes.** Running process keeps the
   loaded module in memory. `sudo systemctl restart insta-reel-bot`.
9. **Router iptables don't persist** across reboots (stock Asus firmware has no
   `firewall-start` hook). Manual re-apply needed — see
   [wireguard_router_fix.md](wireguard_router_fix.md).
10. **Tor exit nodes are blocked by Instagram.** Don't try to route Instagram
    requests via Tor — use the WG tunnel instead.
11. **Cobalt runs COOKIE-FREE on purpose.** Stale cookies in `cobalt_cookies.json`
    actively *broke* Cobalt (it sends a dead sessionid → IG rejects, vs. a clean
    anonymous request succeeding via residential IP). Public content works
    cookieless. Don't re-add `COOKIE_PATH` unless you need private content AND
    have fresh cookies. Private content is still covered by gallery-dl/instaloader
    later in the chain.
12. **Automated login triggers a one-time IG checkpoint** per new device/fingerprint.
    `refresh_cookies_run.sh` will report `REFRESH FAILED` / the new session shows
    `checkpoint_required`. Clear it once in the Instagram app, then it's trusted.
13. **`/dbs` needs the Pro model.** `gemini-2.5-flash-lite` silently omits the
    sentiment fields even with a strict JSON schema. `GOOGLE_AI_MODEL_SENTIMENT`
    defaults to `gemini-2.5-pro`. There's also a sentiment-only retry fallback.
14. **Account/username mismatch.** The working cookie is `h4mid2026` but
    `INSTAGRAM_USERNAME` still says `MiddleEastPoliticalAnalyst`. The refresh +
    instaloader use `IG_USERNAME` / `INSTALOADER_SESSION_USER` = `h4mid2026`.

---

## 11. Diagnostic tools

| Tool | Purpose |
|------|---------|
| `.venv/bin/python diagnose.py --proxy-only` | WG / proxy health |
| `.venv/bin/python diagnose.py "<URL>"` | Full pipeline trace per URL — shows which methods would work |
| `.venv/bin/python diagnose.py "<URL>" --include-paid` | Same, but tests HikerAPI (BILLED) |
| `.venv/bin/python diagnose.py "<URL>" --report out.md` | Save markdown report |
| `.venv/bin/python cookie_health.py` | Manual cookie probe (also writes state file) |
| `/home/ubuntu/wg_monitor.sh` | Manual WG tunnel check |
| `sudo wg show wg0` | Live WG status, handshake, transfer counters |
| `curl --interface wg0 ifconfig.me` | Confirm wg0 exits via home IP |
| `curl -x http://127.0.0.1:3128 ifconfig.me` | Confirm tinyproxy works |
| `journalctl -u insta-reel-bot -n 50` | Bot service logs |
| `tail -f output.logs` | Live bot stdout |

---

## 12. Recovery procedures

### "My bot is down"
```bash
sudo systemctl status insta-reel-bot
sudo systemctl restart insta-reel-bot
journalctl -u insta-reel-bot -n 50
```

### "Telegram alert: WG tunnel forwarding DOWN"
SSH into the home router from your home LAN:
```bash
iptables -I FORWARD 6 -i wgc5 -o eth0 -j ACCEPT
iptables -t nat -A POSTROUTING -s 10.99.0.0/24 -o eth0 -j MASQUERADE
```
Within 5 min the monitor will confirm "restored".

### "Cookies expired" / "HikerAPI bleeding"
First try the automated refresh:
```bash
./refresh_cookies_run.sh
```
If it reports a checkpoint, clear it once in the Instagram app, then re-run.
(The hourly `cookie_health.py` does this automatically when `cookies1.txt` expires.)
Manual fallback: log in via a browser, export Netscape cookies → replace `cookies1.txt`.
Note Cobalt is cookie-free so it's unaffected by cookie expiry.

### "HikerAPI balance dropping fast"
Means too many requests are reaching the last fallback. Run:
```bash
.venv/bin/python diagnose.py "<failing URL>"
```
to see which earlier methods are dying. Usually = expired cookies.

### "Lost SSH access to Oracle server"
Recovery path: Oracle Cloud Console → Compute → Instance → Console Connection
(serial). Can also use OCI Run Command (executes scripts as root via Oracle
Cloud Agent — must be active: `sudo snap start oracle-cloud-agent`).

---

## 13. Important files referenced from elsewhere

- [wireguard_router_fix.md](wireguard_router_fix.md) — full WG troubleshooting
- [cobalt_tor_setup.md](cobalt_tor_setup.md) — Cobalt + Tor history
- [truth_monitor_context.md](truth_monitor_context.md) — Truth Social feature spec
- [README.md](README.md) — original user-facing readme
- `output.logs` — bot stdout/stderr (gitignored)
- `cookie_health.log` — cookie monitor log (gitignored)
- `wg_safety_restore.sh` — emergency rollback script for WG (in `~/`)
- `iptables_backup_*.bak` — iptables backups in `~/`

---

## 14. Recent context / history

- **2026-04-06** — Set up WG tunnel for residential IP (Cobalt was unable to
  download Instagram from datacenter IP). Fixed HikerAPI base URL. Added Cobalt
  cookie support. Added `wg_monitor.sh` + Telegram alerts.
- **2026-04-21** — Added tinyproxy (`wg_proxy`) so yt-dlp/gallery-dl/instaloader
  can also use the residential exit. Wired `proxy` env into all three.
- **2026-04-25** — Telegram group "اتاق خبر" was upgraded to supergroup, breaking
  truth alerts. Fixed by updating `TRUTH_ALERT_CHAT_ID` in `.env`.
- **2026-04-27** — Added `stats.py` + `/report` command, `cookie_health.py`
  cron monitor, `diagnose.py` per-URL pipeline tester. Reordered Instagram
  fallback chain (yt-dlp now tried first for reels — captures captions).
  Expanded `/p/` chain to include instaloader and HikerAPI fallbacks.
- **2026-04-28** — Cookie monitor caught first transient `error_ReadTimeout`
  (false positive — network blip).
- **2026-04-30** — `/dbs` command: detailed brief + visual/vocal/text sentiment.
  Added `GOOGLE_AI_MODEL_SENTIMENT` (Pro) because Lite drops sentiment fields;
  added a sentiment-only retry. `/db` + `/dbs` extended to YouTube.
- **2026-05-01** — Switched brief output to **hybrid language**: transcript stays
  verbatim source-language, everything else (summary/highlights/takeaways/sentiment
  + structural labels) renders in `RESPONSE_LANGUAGE` (Persian). Localized labels.
- **2026-05-23** — Fixed Cobalt + instaloader: `cobalt_cookies.json` was stale
  (old sessionid); discovered the working account is `h4mid2026` not
  `MiddleEastPoliticalAnalyst`; rebuilt instaloader session + updated
  `INSTALOADER_SESSION_USER`.
- **2026-05-24** — Switched Cobalt to **cookie-free** (stale cookies were hurting
  it; residential IP handles public content). Built CloakBrowser-based cookie
  **auto-refresh** (`refresh_cookies.py` + `refresh_cookies_run.sh`) wired into
  `cookie_health.py` (refresh-on-expiry, 6h cooldown). Confirmed automated login
  triggers a one-time device checkpoint that must be cleared manually once.
