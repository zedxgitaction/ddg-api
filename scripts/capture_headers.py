"""
CloakBrowser header capture for DuckDuckGo AI Chat.
Captures VQD hash, fe-version from page load WITHOUT consuming the hash.
"""
import base64
import json
import os
import time
import requests
from cloakbrowser import launch

UPSTASH_URL = os.environ.get("UPSTASH_REDIS_REST_URL")
UPSTASH_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN")
DDG_URL = "https://duck.ai"

captured = {}
cookies_list = []


def on_response(response):
    """Capture VQD hash from response headers."""
    global captured
    for key in ["x-vqd-hash-1", "x-vqd-4"]:
        val = response.headers.get(key.lower()) or response.headers.get(key)
        if val and key not in captured:
            captured[key] = val
            print(f"[+] Captured {key} from response: {response.url[:60]}")


def on_request(request):
    """Capture fe-version from outgoing requests."""
    global captured
    headers = request.headers
    for key in ["x-fe-version", "x-fe-signals"]:
        val = headers.get(key.lower())
        if val and key not in captured:
            captured[key] = val
            print(f"[+] Captured {key}")


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
    print(f"[!] Redis upload failed: {r.status_code} {r.text}")
    return False


def main():
    global captured, cookies_list

    print("[*] Launching CloakBrowser...")
    browser = launch(headless=True)
    page = browser.new_page()
    page.on("request", on_request)
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

    # Wait for page to stabilize
    print("[*] Waiting for page requests to settle...")
    time.sleep(5)

    # Capture cookies
    cookies_list = page.context.cookies()
    print(f"[+] Captured {len(cookies_list)} cookies")

    # Extract fe-version from page HTML if not captured from requests
    if "x-fe-version" not in captured:
        try:
            html = page.content()
            import re
            match = re.search(r'serp_\d{8}_\d{6}_ET-[a-f0-9]+', html)
            if match:
                captured["x-fe-version"] = match.group(0)
                print(f"[+] Extracted fe-version from HTML: {match.group(0)[:40]}...")
        except Exception as e:
            print(f"[*] HTML extraction failed: {e}")

    browser.close()

    if not captured:
        print("[!] Failed to capture any headers")
        exit(1)

    # Generate fe-signals if not captured
    if "x-fe-signals" not in captured:
        now = int(time.time() * 1000)
        signals = {
            "start": now,
            "events": [{"name": "startNewChat_free", "delta": 84}],
            "end": now + 100
        }
        captured["x-fe-signals"] = base64.b64encode(
            json.dumps(signals).encode()
        ).decode()
        print("[+] Generated x-fe-signals")

    # Build store payload — headers only, no chat consumed
    store = {
        "x-fe-version": captured.get("x-fe-version", ""),
        "x-fe-signals": captured.get("x-fe-signals", ""),
        "x-vqd-hash-1": captured.get("x-vqd-hash-1", ""),
    }
    if cookies_list:
        store["cookies"] = "; ".join(f"{c['name']}={c['value']}" for c in cookies_list)

    print(f"[+] Storing: {list(store.keys())}")
    success = upload_to_redis(store)
    if not success:
        print(json.dumps(store, indent=2))
        exit(1)


if __name__ == "__main__":
    main()
