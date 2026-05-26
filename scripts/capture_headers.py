"""
CloakBrowser-powered header capture for DuckDuckGo AI Chat.
Visits duck.ai, triggers a chat request, intercepts headers,
and uploads them to Upstash Redis.
"""
import json
import os
import time
import requests
from cloakbrowser import launch

UPSTASH_URL = os.environ.get("UPSTASH_REDIS_REST_URL")
UPSTASH_TOKEN = os.environ.get("UPSTASH_REDIS_REST_TOKEN")
DDG_URL = "https://duck.ai"
CHAT_ENDPOINT = "https://duck.ai/duckchat/v1/chat"

CAPTURE_HEADERS = [
    "x-fe-version",
    "x-fe-signals",
    "x-vqd-hash-1",
    "x-ddg-journey-id",
]

captured = {}
capture_done = False


def on_request(request):
    """Intercept requests and capture DDG chat headers."""
    global captured, capture_done
    if capture_done:
        return
    # Capture from any request that has our target headers (page load or chat)
    headers = request.headers
    for key in CAPTURE_HEADERS:
        if key not in captured:
            val = headers.get(key.lower())
            if val:
                captured[key] = val
                print(f"[+] Captured {key} from {request.url[:80]}")
    if all(k in captured for k in CAPTURE_HEADERS):
        print(f"[+] Captured all {len(CAPTURE_HEADERS)} headers")
        capture_done = True


def upload_to_redis(headers_dict):
    """Store captured headers in Upstash Redis with 240s TTL."""
    if not UPSTASH_URL or not UPSTASH_TOKEN:
        print("[!] UPSTASH_REDIS_REST_URL or UPSTASH_REDIS_REST_TOKEN not set")
        return False

    data = json.dumps(headers_dict)
    # Upstash REST: POST /pipeline with ["SET", "key", "value", "EX", ttl]
    r = requests.post(
        f"{UPSTASH_URL}/pipeline",
        headers={
            "Authorization": f"Bearer {UPSTASH_TOKEN}",
            "Content-Type": "application/json",
        },
        json=[["SET", "ddg_headers", data, "EX", 240]],
        timeout=10,
    )
    if r.status_code == 200:
        result = r.json()
        if result and result[0].get("result") == "OK":
            print(f"[+] Uploaded headers to Redis (TTL=240s)")
            return True
        print(f"[!] Unexpected Redis response: {r.text}")
        return False
    else:
        print(f"[!] Redis upload failed: {r.status_code} {r.text}")
        return False


def main():
    global captured, capture_done

    print("[*] Launching CloakBrowser...")
    browser = launch(headless=True)
    page = browser.new_page()
    page.on("request", on_request)

    print("[*] Navigating to duck.ai...")
    page.goto(DDG_URL, wait_until="networkidle", timeout=60000)

    # Step 1: Dismiss any consent/start overlay
    print("[*] Looking for consent buttons...")
    time.sleep(2)
    try:
        # Try common consent button patterns
        for selector in [
            'button:has-text("Start chatting")',
            'button:has-text("Get Started")',
            'button:has-text("Accept")',
            'button:has-text("Continue")',
            'button:has-text("I agree")',
            'button:has-text("Try it")',
            '[data-testid="start"]',
            '.onboarding button',
        ]:
            btn = page.locator(selector)
            if btn.count() > 0 and btn.first.is_visible():
                print(f"[+] Clicking: {selector}")
                btn.first.click()
                time.sleep(2)
                break
    except Exception as e:
        print(f"[*] No consent button found or already dismissed: {e}")

    # Step 2: Wait for textarea to become enabled
    print("[*] Waiting for textarea to become enabled...")
    try:
        page.wait_for_selector("textarea:not([disabled])", timeout=15000)
        print("[+] Textarea is enabled")
    except Exception:
        print("[!] Textarea still disabled, trying anyway...")

    # Step 3: Type and send a message
    try:
        textarea = page.locator("textarea").first
        textarea.click()
        time.sleep(0.5)
        textarea.fill("hi")
        time.sleep(0.5)

        # Try to find and click send button
        send_btn = page.locator('[aria-label*="send" i]')
        if send_btn.count() == 0:
            send_btn = page.locator('button[type="submit"]')
        if send_btn.count() > 0 and send_btn.first.is_visible():
            send_btn.first.click()
        else:
            textarea.press("Enter")

        print("[*] Chat request sent, waiting for header capture...")
        timeout = time.time() + 20
        while not capture_done and time.time() < timeout:
            time.sleep(0.5)

    except Exception as e:
        print(f"[!] Browser interaction error: {e}")

    browser.close()

    if captured:
        print(f"[+] Captured headers: {list(captured.keys())}")
        captured["accept"] = "text/event-stream"
        captured["Content-Type"] = "application/json"
        success = upload_to_redis(captured)
        if not success:
            print("[!] Headers (not uploaded):")
            print(json.dumps(captured, indent=2))
            exit(1)
    else:
        print("[!] Failed to capture any headers")
        exit(1)


if __name__ == "__main__":
    main()
