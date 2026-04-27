"""
Diagnostic tool for the download pipeline.

Usage:
    .venv/bin/python diagnose.py <url>                  # full pipeline trace
    .venv/bin/python diagnose.py <url> --include-paid   # also test HikerAPI (costs money)
    .venv/bin/python diagnose.py --proxy-only           # just check proxy/tunnel
    .venv/bin/python diagnose.py <url> --report out.md  # save markdown report

Tests every download method independently and reports which ones work for the
given URL. Helps explain why fallback methods (especially HikerAPI) are being
called more than expected.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from typing import Optional

# Ensure project imports work
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (  # noqa: E402
    COBALT_LOCAL_URL,
    HIKERAPI_KEY,
    INSTAGRAM_COOKIES_FILES,
    RESIDENTIAL_PROXY,
    INSTALOADER_SESSION_USER,
    INSTALOADER_SESSION_FILE,
)
from downloader import (  # noqa: E402
    detect_platform,
    download_instagram_cobalt_local,
    download_instagram_hikerapi,
    download_instagram_post_cobalt,
    download_instagram_post_gallery_dl,
    download_instagram_post_instaloader,
    get_mobile_headers_options,
    get_yt_dlp_options,
    normalize_instagram_url,
)


@dataclass
class StepResult:
    name: str
    ok: bool
    duration_s: float
    detail: str
    skipped: bool = False


def classify_url(url: str) -> str:
    if "/reel" in url or "/reels" in url:
        return "reel"
    if "/tv/" in url:
        return "igtv"
    if "/p/" in url:
        return "post (photo/video/slider — undetermined from URL)"
    return "other"


def planned_order(url: str) -> list[str]:
    """Return the order of methods that the bot would actually try."""
    if "/p/" in url:
        cookies_count = len([f for f in INSTAGRAM_COOKIES_FILES if os.path.exists(f)])
        chain = [f"gallery-dl (cookies{i+1}.txt)" for i in range(cookies_count)]
        if COBALT_LOCAL_URL:
            chain.append("Cobalt local")
        chain.append("instaloader")
        chain.append("Cobalt public mirrors")
        if HIKERAPI_KEY:
            chain.append("HikerAPI (paid)")
        return chain
    chain = ["yt-dlp desktop", "yt-dlp mobile"]
    if COBALT_LOCAL_URL:
        chain.append("Cobalt local")
    chain += ["Cobalt public mirrors", "instaloader"]
    if HIKERAPI_KEY:
        chain.append("HikerAPI (paid)")
    chain.append("gallery-dl")
    return chain


def check_proxy() -> dict:
    """Check the WG proxy is up and exits via residential IP."""
    import requests

    out: dict = {"proxy_url": RESIDENTIAL_PROXY}
    try:
        out["server_ip"] = requests.get("http://ifconfig.me", timeout=5).text.strip()
    except Exception as e:
        out["server_ip"] = f"ERROR: {e}"
    try:
        r = requests.get(
            "http://ifconfig.me",
            proxies={"http": RESIDENTIAL_PROXY, "https": RESIDENTIAL_PROXY},
            timeout=10,
        )
        out["proxy_ip"] = r.text.strip()
    except Exception as e:
        out["proxy_ip"] = f"ERROR: {e}"

    out["proxy_works"] = (
        not out["proxy_ip"].startswith("ERROR")
        and out["proxy_ip"] != out["server_ip"]
    )
    return out


def time_step(fn, *a, **kw):
    """Returns (duration_seconds, result_or_exception)."""
    t0 = time.monotonic()
    try:
        result = fn(*a, **kw)
    except Exception as e:
        return time.monotonic() - t0, e
    return time.monotonic() - t0, result


def test_ytdlp(url: str, opts_fn, label: str) -> StepResult:
    import yt_dlp

    if "/p/" in url:
        return StepResult(label, False, 0.0, "skipped (post URL)", skipped=True)

    tmpdir = tempfile.mkdtemp(prefix="diag_yt_")
    try:
        opts = opts_fn(tmpdir, None)
        opts["simulate"] = True
        opts["skip_download"] = True
        t0 = time.monotonic()
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
        dur = time.monotonic() - t0
        if info:
            cap = (info.get("description") or "")[:80].replace("\n", " ")
            return StepResult(label, True, dur, f"caption: {cap!r}")
        return StepResult(label, False, dur, "no info returned")
    except Exception as e:
        return StepResult(label, False, time.monotonic() - t0, str(e)[:200])
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_cobalt_local(url: str) -> StepResult:
    if not COBALT_LOCAL_URL:
        return StepResult("Cobalt local", False, 0.0, "COBALT_LOCAL_URL not set", skipped=True)
    tmpdir = tempfile.mkdtemp(prefix="diag_cl_")
    try:
        dur, res = time_step(download_instagram_cobalt_local, url, tmpdir)
        if isinstance(res, Exception):
            return StepResult("Cobalt local", False, dur, str(res)[:200])
        if not res.error:
            cap = (res.caption or "(none)")[:80]
            return StepResult("Cobalt local", True, dur, f"file ok | caption: {cap!r}")
        return StepResult("Cobalt local", False, dur, res.error[:200])
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_cobalt_public(url: str) -> StepResult:
    tmpdir = tempfile.mkdtemp(prefix="diag_cp_")
    try:
        dur, res = time_step(download_instagram_post_cobalt, url, tmpdir)
        if isinstance(res, Exception):
            return StepResult("Cobalt public", False, dur, str(res)[:200])
        if not res.error:
            return StepResult("Cobalt public", True, dur, "file ok")
        return StepResult("Cobalt public", False, dur, res.error[:200])
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_gallery_dl(url: str, cookie_path: Optional[str], label: str) -> StepResult:
    tmpdir = tempfile.mkdtemp(prefix="diag_gdl_")
    try:
        dur, res = time_step(download_instagram_post_gallery_dl, url, tmpdir, cookie_path)
        if isinstance(res, Exception):
            return StepResult(label, False, dur, str(res)[:200])
        if not res.error:
            cap = (res.caption or "(none)")[:80]
            return StepResult(label, True, dur, f"file ok | caption: {cap!r}")
        return StepResult(label, False, dur, res.error[:200])
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_instaloader(url: str) -> StepResult:
    session_path = INSTALOADER_SESSION_FILE or (
        os.path.expanduser(f"~/.config/instaloader/session-{INSTALOADER_SESSION_USER.lower()}")
        if INSTALOADER_SESSION_USER else ""
    )
    if not session_path or not os.path.exists(session_path):
        return StepResult("instaloader", False, 0.0,
                          f"session file missing: {session_path}", skipped=True)
    tmpdir = tempfile.mkdtemp(prefix="diag_il_")
    try:
        dur, res = time_step(download_instagram_post_instaloader, url, tmpdir)
        if isinstance(res, Exception):
            return StepResult("instaloader", False, dur, str(res)[:200])
        if not res.error:
            return StepResult("instaloader", True, dur, "file ok")
        return StepResult("instaloader", False, dur, res.error[:200])
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_hikerapi(url: str) -> StepResult:
    if not HIKERAPI_KEY:
        return StepResult("HikerAPI", False, 0.0, "HIKERAPI_KEY not set", skipped=True)
    tmpdir = tempfile.mkdtemp(prefix="diag_hk_")
    try:
        dur, res = time_step(download_instagram_hikerapi, url, tmpdir)
        if isinstance(res, Exception):
            return StepResult("HikerAPI", False, dur, str(res)[:200])
        if not res.error:
            return StepResult("HikerAPI", True, dur, "file ok (BILLED)")
        return StepResult("HikerAPI", False, dur, res.error[:200])
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def render(report: dict, fh) -> None:
    p = lambda *a, **kw: print(*a, file=fh, **kw)  # noqa: E731

    p("# Pipeline diagnostic report\n")
    p(f"**URL:** `{report['url']}`")
    p(f"**Type:** {report['url_type']}")
    p(f"**Time:** {report['timestamp']}\n")

    p("## Proxy / tunnel\n")
    pr = report["proxy"]
    p(f"- Proxy URL: `{pr['proxy_url']}`")
    p(f"- Server direct IP: `{pr['server_ip']}`")
    p(f"- Through proxy IP: `{pr['proxy_ip']}`")
    p(f"- Proxy working: **{'YES' if pr['proxy_works'] else 'NO'}**\n")

    p("## Production fallback order\n")
    for i, m in enumerate(report["planned"], 1):
        p(f"{i}. {m}")
    p()

    p("## Per-method results\n")
    p("| # | Method | Result | Time | Detail |")
    p("|---|--------|--------|------|--------|")
    for i, r in enumerate(report["steps"], 1):
        if r.skipped:
            status = "skip"
        else:
            status = "OK" if r.ok else "FAIL"
        p(f"| {i} | {r.name} | **{status}** | {r.duration_s:.2f}s | {r.detail} |")
    p()

    # Summary
    real = [r for r in report["steps"] if not r.skipped]
    ok = [r for r in real if r.ok]
    fail = [r for r in real if not r.ok]
    p("## Summary\n")
    p(f"- Total methods tested: {len(real)}")
    p(f"- Working: {len(ok)} ({', '.join(r.name for r in ok) or 'none'})")
    p(f"- Failing: {len(fail)} ({', '.join(r.name for r in fail) or 'none'})")
    if not ok:
        p("\n**WARNING:** No method works for this URL — bot will fail to download it.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("url", nargs="?", help="Instagram URL to diagnose")
    ap.add_argument("--proxy-only", action="store_true",
                    help="Only check proxy/tunnel health")
    ap.add_argument("--include-paid", action="store_true",
                    help="Include HikerAPI test (costs money per call)")
    ap.add_argument("--report", help="Write markdown report to this file")
    args = ap.parse_args()

    if args.proxy_only:
        proxy = check_proxy()
        for k, v in proxy.items():
            print(f"{k}: {v}")
        return 0 if proxy["proxy_works"] else 1

    if not args.url:
        ap.error("URL is required (or use --proxy-only)")

    platform = detect_platform(args.url)
    if platform != "instagram":
        print(f"This tool is for Instagram URLs only. Detected: {platform}")
        return 2

    url = normalize_instagram_url(args.url)
    print(f"Diagnosing: {url}", flush=True)

    proxy = check_proxy()
    print(f"Proxy: {proxy['proxy_works']} ({proxy['proxy_ip']})", flush=True)

    is_post = "/p/" in url
    cookie_files = [f for f in INSTAGRAM_COOKIES_FILES if os.path.exists(f)]

    steps: list[StepResult] = []
    if is_post:
        # /p/ chain
        for cf in cookie_files:
            label = f"gallery-dl ({os.path.basename(cf)})"
            print(f"  testing {label}...", flush=True)
            steps.append(test_gallery_dl(url, cf, label))
        print("  testing Cobalt local...", flush=True)
        steps.append(test_cobalt_local(url))
        print("  testing instaloader...", flush=True)
        steps.append(test_instaloader(url))
        print("  testing Cobalt public...", flush=True)
        steps.append(test_cobalt_public(url))
        if args.include_paid:
            print("  testing HikerAPI (BILLED)...", flush=True)
            steps.append(test_hikerapi(url))
        else:
            steps.append(StepResult("HikerAPI", False, 0.0,
                                    "skipped (use --include-paid to test)",
                                    skipped=True))
    else:
        # Reel/IGTV chain
        print("  testing yt-dlp desktop...", flush=True)
        steps.append(test_ytdlp(url, get_yt_dlp_options, "yt-dlp desktop"))
        print("  testing yt-dlp mobile...", flush=True)
        steps.append(test_ytdlp(url, get_mobile_headers_options, "yt-dlp mobile"))
        print("  testing Cobalt local...", flush=True)
        steps.append(test_cobalt_local(url))
        print("  testing Cobalt public...", flush=True)
        steps.append(test_cobalt_public(url))
        print("  testing instaloader...", flush=True)
        steps.append(test_instaloader(url))
        if args.include_paid:
            print("  testing HikerAPI (BILLED)...", flush=True)
            steps.append(test_hikerapi(url))
        else:
            steps.append(StepResult("HikerAPI", False, 0.0,
                                    "skipped (use --include-paid to test)",
                                    skipped=True))
        for cf in cookie_files:
            label = f"gallery-dl ({os.path.basename(cf)})"
            print(f"  testing {label}...", flush=True)
            steps.append(test_gallery_dl(url, cf, label))

    report = {
        "url": url,
        "url_type": classify_url(url),
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "proxy": proxy,
        "planned": planned_order(url),
        "steps": steps,
    }

    render(report, sys.stdout)
    if args.report:
        with open(args.report, "w") as fh:
            render(report, fh)
        print(f"\nReport saved to {args.report}", file=sys.stderr)

    real = [r for r in steps if not r.skipped]
    return 0 if any(r.ok for r in real) else 1


if __name__ == "__main__":
    sys.exit(main())
