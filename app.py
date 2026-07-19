"""
Screenshot Tool — Streamlit version
====================================
Paste URLs directly in the app, adjust settings in the sidebar, click Run.

RUN LOCALLY (first-time setup, run once in your terminal):
    pip install -r requirements.txt
    playwright install chromium
    streamlit run app.py

DEPLOY TO STREAMLIT COMMUNITY CLOUD (free, public link):
    See README.md — Chromium installs itself automatically on first boot,
    no terminal access needed.
"""

import asyncio
import ipaddress
import os

# Force Playwright to use its lightweight headless shell binary.
# The full chrome binary requires libgtk/libcups which conflict with
# Streamlit Cloud's mixed Debian trixie + legacy repo setup.
# Headless shell works perfectly for screenshots and avoids those deps.
os.environ['PLAYWRIGHT_CHROMIUM_USE_HEADLESS_NEW'] = '0'

import re
import csv
import socket
import subprocess
import sys
import time
import shutil
import zipfile
from urllib.parse import urlparse

import streamlit as st
from playwright.async_api import async_playwright

# ==============================================================
# AUTO-INSTALL CHROMIUM (needed on Streamlit Community Cloud,
# which has no terminal to run "playwright install" manually).
# Runs once per container start; cached so reruns don't repeat it.
# ==============================================================
@st.cache_resource
def ensure_chromium_installed():
    try:
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=True, capture_output=True, text=True,
        )
    except subprocess.CalledProcessError as e:
        st.error(f"Failed to install Chromium: {e.stderr}")
    return True


ensure_chromium_installed()

# ==============================================================
# PAGE CONFIG
# ==============================================================
st.set_page_config(page_title="Screenshot Tool", page_icon="📸", layout="wide")
st.title("📸 Screenshot Tool")
st.caption("Paste URLs, adjust settings, and download a zip of full-page screenshots.")

# ==============================================================
# SAFETY LAYER — needed once this app is on the public internet
# ==============================================================
# Hard caps so one visitor can't overload the free server.
MAX_CONCURRENT_TABS_ALLOWED = 10

# --- Optional password gate ---------------------------------
# Set APP_PASSWORD in Hugging Face Space "Settings > Variables and secrets".
# If no password secret is set (e.g. running locally), the gate is skipped.
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")

if APP_PASSWORD:
    if "authed" not in st.session_state:
        st.session_state.authed = False
    if not st.session_state.authed:
        pw = st.text_input("Password", type="password")
        if pw:
            if pw == APP_PASSWORD:
                st.session_state.authed = True
                st.rerun()
            else:
                st.error("Wrong password.")
        st.stop()


def is_private_or_internal(url: str) -> bool:
    """Block localhost / private network / cloud-metadata targets."""
    try:
        host = urlparse(url).hostname
        if not host:
            return True
        if host.lower() in ("localhost", "metadata.google.internal"):
            return True
        ip = socket.gethostbyname(host)
        ip_obj = ipaddress.ip_address(ip)
        return ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_link_local or ip_obj.is_reserved
    except Exception:
        # If it can't be resolved/parsed, treat it as unsafe rather than assume it's fine.
        return True

# ==============================================================
# SIDEBAR — SETTINGS (mirrors the old 'Settings' sheet tab)
# ==============================================================
with st.sidebar:
    st.header("Settings")

    FORMAT = st.selectbox("Format", ["PNG", "JPEG"], index=0)
    JPEG_QUALITY = 80
    if FORMAT == "JPEG":
        JPEG_QUALITY = st.slider("JPEG Quality", 1, 100, 80)

    VIEWPORT_WIDTH = st.number_input("Viewport Width (px)", 320, 3840, 1440, step=10)
    CONCURRENT_TABS = st.slider("Concurrent Tabs", 1, MAX_CONCURRENT_TABS_ALLOWED, 5)
    PAGE_LOAD_TIMEOUT_S = st.number_input("Page Load Timeout (s)", 5, 120, 30)
    EXTRA_WAIT = st.number_input("Extra Wait After Load (s)", 0.0, 30.0, 2.0, step=0.5)
    DELAY_BETWEEN_BATCHES = st.number_input("Delay Between Batches (s)", 0.0, 30.0, 2.0, step=0.5)
    MAX_RETRIES = st.slider("Max Retries", 1, 10, 3)

    AUTO_SCROLL = st.checkbox("Auto-Scroll for Lazy Content", value=True)
    DISMISS_COOKIES = st.checkbox("Dismiss Cookie Banners", value=True)

    MAX_SCREENSHOT_HEIGHT = 16384
    PAGE_LOAD_TIMEOUT = int(PAGE_LOAD_TIMEOUT_S * 1000)

# ==============================================================
# MAIN — URL INPUT (pasted directly, no Google Sheet needed)
# ==============================================================
st.subheader("URLs")
st.caption(
    "One per line. Optionally add a custom filename after a comma, e.g.\n"
    "`https://binaytara.org/research-grants, research-grants-page`"
)
url_text = st.text_area(
    "Paste URLs here",
    height=200,
    placeholder="https://binaytara.org/research-grants\nhttps://binaytara.org/conferences/2025, conferences-2025",
)

run_clicked = st.button("▶ Run Screenshots", type="primary")

# ==============================================================
# HELPERS
# ==============================================================
COOKIE_SELECTORS = [
    '#onetrust-accept-btn-handler', '.cc-btn.cc-dismiss',
    'button[id*="cookie-accept"]', 'button[id*="cookieAccept"]',
    'button[aria-label*="Accept"]', 'button[aria-label*="Accept all"]',
    'button[aria-label*="accept cookies"]',
    '[id*="consent"] button[class*="accept"]',
    '[class*="consent"] button[class*="accept"]',
    '#accept-cookies', '.accept-cookies',
    '.cookie-notice button', '[class*="gdpr"] button',
]


def parse_urls(raw_text):
    """Parse pasted textarea content into a list of {url, custom_name} dicts."""
    urls_list = []
    for line in raw_text.splitlines():
        line = line.strip()
        if not line:
            continue
        if "," in line:
            url_part, name_part = line.split(",", 1)
            url = url_part.strip()
            custom_name = name_part.strip() or None
        else:
            url = line
            custom_name = None
        if url.lower() in ("url", "urls", "link", "links", "page", "pages"):
            continue
        if not url.startswith("http://") and not url.startswith("https://"):
            url = "https://" + url
        urls_list.append({"url": url, "custom_name": custom_name})
    return urls_list


def url_to_filename(url, custom_name, fmt, used_filenames):
    ext = "jpg" if fmt == "JPEG" else "png"
    if custom_name:
        base = re.sub(r'[^\w\-.]', '-', custom_name)
    else:
        parsed = urlparse(url)
        domain = parsed.netloc
        if domain.startswith("www."):
            domain = domain[4:]
        path = parsed.path.strip('/').replace('/', '-')
        query = parsed.query
        if query:
            query_clean = re.sub(r'[^\w\-.]', '-', query)[:50]
            path = f"{path}--{query_clean}" if path else query_clean
        fragment = parsed.fragment
        if fragment:
            frag_clean = re.sub(r'[^\w\-.]', '-', fragment)[:30]
            path = f"{path}--hash-{frag_clean}" if path else frag_clean
        base = f"{domain}-{path}" if path else domain
        base = re.sub(r'[\\/:*?"<>|]', '-', base)
        base = re.sub(r'-+', '-', base).strip('-')

    if not base:
        base = "unnamed-page"
    if len(base) > 180:
        base = base[:180]

    candidate = f"{base}.{ext}"
    counter = 1
    while candidate in used_filenames:
        candidate = f"{base}-{counter}.{ext}"
        counter += 1
    used_filenames.add(candidate)
    return candidate


async def auto_scroll_page(page):
    try:
        await asyncio.wait_for(
            page.evaluate("""
                async () => {
                    await new Promise((resolve) => {
                        let totalHeight = 0;
                        const distance = 500;
                        const maxScroll = 100000;
                        const timer = setInterval(() => {
                            const scrollHeight = document.body.scrollHeight;
                            window.scrollBy(0, distance);
                            totalHeight += distance;
                            if (totalHeight >= scrollHeight || totalHeight >= maxScroll) {
                                clearInterval(timer);
                                window.scrollTo(0, 0);
                                resolve();
                            }
                        }, 100);
                    });
                }
            """),
            timeout=30
        )
    except asyncio.TimeoutError:
        await page.evaluate("window.scrollTo(0, 0)")
    await page.wait_for_timeout(1000)


async def dismiss_cookies(page):
    for selector in COOKIE_SELECTORS:
        try:
            btn = page.locator(selector).first
            if await btn.is_visible(timeout=300):
                await btn.click(timeout=1000)
                await page.wait_for_timeout(500)
                return
        except Exception:
            continue


async def take_screenshot(context, url_info, semaphore, results, images_dir, used_filenames,
                           fmt, jpeg_quality, viewport_width, page_load_timeout, extra_wait,
                           auto_scroll, dismiss_cookies_flag, max_retries, max_height,
                           progress_state, progress_bar, status_text):
    async with semaphore:
        url = url_info["url"]
        filename = url_to_filename(url, url_info["custom_name"], fmt, used_filenames)
        filepath = os.path.join(images_dir, filename)
        page = await context.new_page()
        page.on("dialog", lambda dialog: asyncio.ensure_future(dialog.dismiss()))

        try:
            for attempt in range(max_retries):
                try:
                    wait_for = "networkidle" if attempt == 0 else "domcontentloaded"
                    response = await page.goto(url, timeout=page_load_timeout, wait_until=wait_for)

                    if response and response.status >= 400:
                        raise Exception(f"HTTP {response.status}")

                    content_type = response.headers.get("content-type", "") if response else ""
                    if "application/pdf" in content_type:
                        raise Exception("PDF file, not a webpage")
                    if "application/octet-stream" in content_type:
                        raise Exception("Binary download, not a webpage")

                    if dismiss_cookies_flag:
                        await dismiss_cookies(page)
                    if auto_scroll:
                        await auto_scroll_page(page)
                    if extra_wait > 0:
                        await page.wait_for_timeout(int(extra_wait * 1000))

                    await page.evaluate("window.scrollTo(0, 0)")
                    await page.wait_for_timeout(300)

                    page_height = await page.evaluate("document.body.scrollHeight")
                    opts = {"path": filepath, "full_page": True}
                    if page_height > max_height:
                        opts["full_page"] = False
                        opts["clip"] = {"x": 0, "y": 0, "width": viewport_width, "height": max_height}

                    if fmt == "JPEG":
                        opts["type"] = "jpeg"
                        opts["quality"] = jpeg_quality
                    else:
                        opts["type"] = "png"

                    await page.screenshot(**opts)

                    progress_state["done"] += 1
                    results.append({"url": url, "filename": filename, "status": "SUCCESS", "error": ""})
                    break

                except Exception as e:
                    err = str(e)[:150]
                    if attempt < max_retries - 1:
                        await asyncio.sleep(2)
                    else:
                        progress_state["done"] += 1
                        progress_state["failed"] += 1
                        results.append({"url": url, "filename": "", "status": "FAILED", "error": err})
        finally:
            try:
                await page.close()
            except Exception:
                pass
            frac = progress_state["done"] / progress_state["total"]
            progress_bar.progress(frac)
            status_text.text(f"{progress_state['done']}/{progress_state['total']} done "
                              f"({progress_state['failed']} failed)")


async def run_all(urls_list, images_dir, settings, progress_bar, status_text):
    results = []
    used_filenames = set()
    progress_state = {"done": 0, "total": len(urls_list), "failed": 0}
    semaphore = asyncio.Semaphore(settings["concurrent_tabs"])

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu',
                  '--disable-extensions', '--disable-background-networking']
        )
        context = await browser.new_context(
            viewport={"width": settings["viewport_width"], "height": 900},
            device_scale_factor=1,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            java_script_enabled=True,
            bypass_csp=True,
        )
        await context.route("**/*.{mp4,webm,ogg,avi,mov}", lambda route: route.abort())

        batch_size = settings["concurrent_tabs"]
        for i in range(0, len(urls_list), batch_size):
            batch = urls_list[i:i + batch_size]
            tasks = [
                take_screenshot(
                    context, info, semaphore, results, images_dir, used_filenames,
                    settings["format"], settings["jpeg_quality"], settings["viewport_width"],
                    settings["page_load_timeout"], settings["extra_wait"], settings["auto_scroll"],
                    settings["dismiss_cookies"], settings["max_retries"], settings["max_height"],
                    progress_state, progress_bar, status_text
                )
                for info in batch
            ]
            await asyncio.gather(*tasks, return_exceptions=True)

            if i + batch_size < len(urls_list) and settings["delay_between_batches"] > 0:
                await asyncio.sleep(settings["delay_between_batches"])

        await browser.close()
    return results


# ==============================================================
# RUN
# ==============================================================
if run_clicked:
    urls_list = parse_urls(url_text)

    if not urls_list:
        st.error("No valid URLs found. Paste at least one URL above.")
    else:
        blocked = [u["url"] for u in urls_list if is_private_or_internal(u["url"])]
        if blocked:
            st.error("These URLs point to a private/internal address and were blocked for "
                      "safety:\n" + "\n".join(blocked))
        else:
            st.info(f"Starting {len(urls_list)} screenshots...")

            output_dir = "/tmp/screenshot_tool_output"
            images_dir = os.path.join(output_dir, "images")
            if os.path.exists(output_dir):
                shutil.rmtree(output_dir)
            os.makedirs(images_dir)

            settings = {
                "format": FORMAT,
                "jpeg_quality": JPEG_QUALITY,
                "viewport_width": VIEWPORT_WIDTH,
                "concurrent_tabs": CONCURRENT_TABS,
                "page_load_timeout": PAGE_LOAD_TIMEOUT,
                "extra_wait": EXTRA_WAIT,
                "delay_between_batches": DELAY_BETWEEN_BATCHES,
                "auto_scroll": AUTO_SCROLL,
                "dismiss_cookies": DISMISS_COOKIES,
                "max_retries": MAX_RETRIES,
                "max_height": MAX_SCREENSHOT_HEIGHT,
            }

            progress_bar = st.progress(0)
            status_text = st.empty()

            start_time = time.time()
            results = asyncio.run(run_all(urls_list, images_dir, settings, progress_bar, status_text))
            elapsed = time.time() - start_time

            success = sum(1 for r in results if r["status"] == "SUCCESS")
            failed = sum(1 for r in results if r["status"] == "FAILED")

            st.success(f"Done in {elapsed:.1f}s — {success} succeeded, {failed} failed.")

            # Manifest + errors CSVs
            manifest_path = os.path.join(output_dir, "manifest.csv")
            with open(manifest_path, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=["url", "filename", "status", "error"])
                w.writeheader()
                for r in results:
                    w.writerow(r)

            error_list = [r for r in results if r["status"] == "FAILED"]
            if error_list:
                errors_path = os.path.join(output_dir, "errors.csv")
                with open(errors_path, "w", newline="", encoding="utf-8") as f:
                    w = csv.DictWriter(f, fieldnames=["url", "error"])
                    w.writeheader()
                    for r in error_list:
                        w.writerow({"url": r["url"], "error": r["error"]})

            # Results table
            st.subheader("Results")
            st.dataframe(results, use_container_width=True)

            # Zip it up
            ts = time.strftime('%Y%m%d_%H%M%S')
            zip_path = f"/tmp/screenshots_{ts}.zip"
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                for fname in os.listdir(images_dir):
                    zf.write(os.path.join(images_dir, fname), f"images/{fname}")
                zf.write(manifest_path, "manifest.csv")
                if error_list:
                    zf.write(errors_path, "errors.csv")

            with open(zip_path, "rb") as f:
                st.download_button(
                    "⬇ Download all screenshots (.zip)",
                    data=f,
                    file_name=f"screenshots_{ts}.zip",
                    mime="application/zip",
                )
