"""
CloakBrowser-powered header capture for DuckDuckGo AI Chat.
ONLY captures from page load — NEVER sends a chat message.
The VQD hash is single-use, so we must not consume it during capture.
"""
import json
import os
import time
import base64
import requests
from cloakbrowser import launch

UPSTASH_URL = os.environ.get("UPSTASH_REDIS_REST_URL")
UPSTASH_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN")
DDG_URL = "https://duck.ai"

captured = {}


def on_response(response):
    """Capture VQD hash from response headers."""
    global captured
    for key in ["x-vqd-hash-1", "x-vqd-4"]:
        val = response.headers.get(key.lower()) or response.headers.get(key)
        if val and key not in captured:
            captured[key] = val
            print(f"[+] Captured {key} from response: {response.url[:60]}")


def upload_to_redis(data):
    """Store data in Upstash Redis with 240s TTL."""
    if not UPSTASH_URL or not UPSTASH_TOKEN:
        print("[!] Missing Upstash credentials")
        return False
    r = requests.post(
        f"{UPSTASH_URL}/pipeline",
        headers={
            "Authorization": f"Bearer {UPSTASH_TOKEN}",
            "Content-Type": "application/json",
        },
        json=[["SET", "ddg_headers", json.dumps(data), "EX", 240]],
        timeout=10,
    )
    if r.status_code == 200:
        result = r.json()
        if result and result[0].get("result") == "OK":
            print("[+] Uploaded to Redis (TTL=240s)")
            return True
    print(f"[!] Redis failed: {r.status_code} {r.text}")
    return False


def main():
    global captured

    print("[*] Launching CloakBrowser...")
    browser = launch(headless=True)
    page = browser.new_page()
    page.on("response", on_response)

    print("[*] Navigating to duck.ai...")
    page.goto(DDG_URL, wait_until="networkidle", timeout=60000)

    # Dismiss consent overlay
    time.sleep(2)
    try:
        for sel in [
            'button:has-text("Continue")',
            'button:has-text("Start chatting")',
            'button:has-text("Get Started")',
        ]:
            btn = page.locator(sel)
            if btn.count() > 0 and btn.first.is_visible():
                print(f"[+] Clicking: {sel}")
                btn.first.click()
                time.sleep(3)
                break
    except Exception as e:
        print(f"[*] No consent: {e}")

    # Wait for all page-load requests to finish
    print("[*] Waiting for page requests to settle...")
    time.sleep(5)

    # Extract fe-version from the page's embedded scripts
    fe_version = page.evaluate("""() => {
        const scripts = document.querySelectorAll('script[src]');
        for (const s of scripts) {
            const match = s.src.match(/serp_[\\d_]+-[a-f0-9]+/);
            if (match) return match[0];
        }
        const allScripts = document.querySelectorAll('script');
        for (const s of allScripts) {
            const match = s.textContent.match(/serp_[\\d_]+[_-][a-f0-9]{20,}/);
            if (match) return match[0];
        }
        return null;
    }""")
    if fe_version:
        captured["x-fe-version"] = fe_version
        print(f"[+] Extracted fe-version: {fe_version[:50]}...")

    # Generate fe-signals (timing data the browser would send)
    now_ms = int(time.time() * 1000)
    signals = {
        "start": now_ms,
        "events": [{"name": "startNewChat_free", "delta": 84}],
        "end": now_ms + 100
    }
    captured["x-fe-signals"] = base64.b64encode(
        json.dumps(signals).encode()
    ).decode()

    # Capture cookies
    cookies = page.context.cookies()
    if cookies:
        captured["cookies"] = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
        print(f"[+] Captured {len(cookies)} cookies")

    browser.close()

    # Validate we have what we need
    required = ["x-vqd-hash-1", "x-fe-version"]
    missing = [h for h in required if h not in captured]
    if missing:
        print(f"[!] Missing required headers: {missing}")
        exit(1)

    print(f"[+] All headers: {list(captured.keys())}")
    success = upload_to_redis(captured)
    if not success:
        print(json.dumps(captured, indent=2))
        exit(1)


if __name__ == "__main__":
    main()
