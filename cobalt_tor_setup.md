# Cobalt Setup — Status & Findings (Updated 2026-04-06)

## Server
- **Host:** Oracle Cloud Free Tier, Ubuntu ARM aarch64
- **Project dir:** `~/telebots_projects/ReelDownloaderTranslatorBot`
- **Python venv:** `.venv`
- **Docker version:** 28.1.1
- **Cobalt version:** 11.5

---

## Current Status: Instagram via Cobalt Does NOT Work

### Why
Instagram requires **valid session cookies** for all API endpoints from datacenter IPs. All three cookie files (`cookies1-3.txt`) and the instaloader session are expired ("login_required").

### Key Findings from Investigation

1. **`ALL_PROXY` with SOCKS5 is NOT supported by Cobalt.** Cobalt uses Node.js `undici`'s `EnvHttpProxyAgent` which only handles `HTTP_PROXY`/`HTTPS_PROXY` with HTTP(S) proxies. The `ALL_PROXY=socks5://...` config was silently ignored.

2. **Tor exit nodes are blocked by Instagram.** Even with working Tor routing, Instagram returns 302 (redirect to login) for API calls. YouTube and other services work fine through Tor.

3. **Cobalt DOES support Instagram cookies** via `COOKIE_PATH` env var pointing to a JSON file:
   ```json
   {
       "instagram": ["csrftoken=...; sessionid=...; ds_user_id=...; ..."]
   }
   ```

4. **Tor has native HTTP CONNECT support** via `HTTPTunnelPort` (since Tor 0.3.2). No need for Privoxy — Tor can serve as an HTTPS proxy directly. Set `HTTPS_PROXY=http://172.17.0.1:9080` in containers.

---

## Current Container Setup

```bash
docker run -d --name cobalt --restart unless-stopped --init \
  -p 127.0.0.1:9000:9000 \
  -e API_URL=http://localhost:9000 \
  -e HTTPS_PROXY=http://172.17.0.1:9080 \
  -e COOKIE_PATH=/cookies.json \
  -v /home/ubuntu/telebots_projects/ReelDownloaderTranslatorBot/cobalt_cookies.json:/cookies.json \
  ghcr.io/imputnet/cobalt:11
```

- Cookies file: `cobalt_cookies.json` (mounted into container)
- HTTPS traffic routed through Tor via `HTTPTunnelPort` (no Privoxy needed)
- YouTube downloads verified working through Tor

---

## To Fix Instagram: Get Fresh Cookies

### Option 1: Export from browser
1. Log into Instagram in a browser
2. Export cookies in Netscape format
3. Convert to Cobalt JSON format:
   ```python
   python3 -c "
   cookies = []
   with open('cookies_new.txt') as f:
       for line in f:
           if line.startswith('#') or not line.strip(): continue
           parts = line.strip().split('\t')
           if len(parts) >= 7: cookies.append(f'{parts[5]}={parts[6]}')
   import json
   print(json.dumps({'instagram': ['; '.join(cookies)]}, indent=4))
   " > cobalt_cookies.json
   ```
4. Restart Cobalt: `docker restart cobalt`

### Option 2: Use residential HTTP proxy (no cookies needed)
```bash
docker stop cobalt && docker rm cobalt
docker run -d --name cobalt --restart unless-stopped --init \
  -p 127.0.0.1:9000:9000 \
  -e API_URL=http://localhost:9000 \
  -e HTTP_PROXY=http://user:pass@proxy-host:port \
  -e HTTPS_PROXY=http://user:pass@proxy-host:port \
  ghcr.io/imputnet/cobalt:11
```

---

## Infrastructure In Place

### Tor
- **SOCKS5:** `172.17.0.1:9050` (for tools that support SOCKS5 natively)
- **HTTP CONNECT (HTTPTunnelPort):** `172.17.0.1:9080` (for `HTTPS_PROXY` env var)
- `sudo systemctl enable tor` (starts on boot)
- Privoxy removed — Tor's native `HTTPTunnelPort` replaces it

### torrc additions (`/etc/tor/torrc`):
```
SocksPolicy accept 172.17.0.0/16
SocksPort 172.17.0.1:9050
HTTPTunnelPort 172.17.0.1:9080
```

### Verified Working
- Docker → Tor HTTPTunnelPort → YouTube (Cobalt downloads successfully)
- Docker → Tor HTTPTunnelPort → Instagram page (HTTP 200, but API blocked without cookies)

---

## Bot Fallback Chain

```
1. Cobalt local        ← needs fresh cookies for Instagram
2. yt-dlp desktop      (uses cookie pool)
3. yt-dlp mobile       (uses cookie pool)
4. Cobalt public mirrors
5. Instaloader         (session expired — needs refresh)
6. HikerAPI            (paid, residential proxies)
7. gallery-dl          (uses cookie pool)
```

---

## Troubleshooting

### Verify Cobalt cookies loaded
```bash
docker logs cobalt | grep cookie
# Should show: [✓] cookies loaded successfully!
```

### Test Cobalt Instagram
```bash
curl -s -X POST http://localhost:9000/ \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://www.instagram.com/reel/DWyXtR0Eyi_/","alwaysProxy":true}' | python3 -m json.tool
```

### Test Cobalt YouTube (should always work)
```bash
curl -s -X POST http://localhost:9000/ \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://www.youtube.com/watch?v=dQw4w9WgXcQ","alwaysProxy":true}' | python3 -m json.tool
```

### Verify Tor chain
```bash
docker run --rm curlimages/curl curl -x http://172.17.0.1:9080 \
  -s -o /dev/null -w "%{http_code}" https://www.instagram.com/
```

---

## Relevant Code
- **`downloader.py`** — `download_instagram_cobalt_local()` handles the Cobalt API call
- **`config.py`** — `COBALT_LOCAL_URL` env var enables/disables Cobalt local
- **`bot.py`** — `instagram_cookie_health_loop()` pings Cobalt every 6h
