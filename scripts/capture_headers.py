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

# Headers we need to capture from the browser's chat request
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
    if CHAT_ENDPOINT in request.url and request.method == "POST":
        headers = request.headers
        for key in CAPTURE_HEADERS:
            val = headers.get(key.lower())
            if val:
                captured[key] = val
        if all(k in captured for k in CAPTURE_HEADERS):
            print(f"[+] Captured all {len(CAPTURE_HEADERS)} headers")
            capture_done = True


def upload_to_redis(headers_dict):
    """Store captured headers in Upstash Redis with 240s TTL."""
    if not UPSTASH_URL or not UPSTASH_TOKEN:
        print("[!] UPSTASH_REDIS_REST_URL or UPSTASH_REDIS_REST_TOKEN not set")
        return False

    payload = json.dumps(headers_dict)
    r = requests.post(
        f"{UPSTASH_URL}/set/ddg_headers/{payload}",
        headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"},
        params={"EX": "240"},
        timeout=10,
    )
    # Upstash SET returns "+OK" on success
    if r.status_code == 200:
        print(f"[+] Uploaded headers to Redis (TTL=240s)")
        return True
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
    page.goto(DDG_URL, wait_until="networkidle", timeout=30000)

    # Wait for the chat input to appear
    print("[*] Waiting for chat input...")
    time.sleep(3)

    # Find and interact with the chat input
    try:
        # duck.ai uses a textarea or input for chat
        textarea = page.locator("textarea")
        if textarea.count() == 0:
            textarea = page.locator('[contenteditable="true"]')
        if textarea.count() == 0:
            textarea = page.locator("input[type='text']")

        textarea.first.click()
        time.sleep(0.5)
        textarea.first.fill("hi")
        time.sleep(0.5)

        # Find and click the send button
        send_btn = page.locator('button[type="submit"]')
        if send_btn.count() == 0:
            send_btn = page.locator('[aria-label*="send" i]')
        if send_btn.count() == 0:
            # Try pressing Enter instead
            textarea.first.press("Enter")
        else:
            send_btn.first.click()

        print("[*] Chat request sent, waiting for header capture...")
        # Wait for the request to be intercepted
        timeout = time.time() + 15
        while not capture_done and time.time() < timeout:
            time.sleep(0.5)

    except Exception as e:
        print(f"[!] Browser interaction error: {e}")

    browser.close()

    if captured:
        print(f"[+] Captured headers: {list(captured.keys())}")
        # Add static headers
        captured["accept"] = "text/event-stream"
        captured["Content-Type"] = "application/json"
        success = upload_to_redis(captured)
        if not success:
            # Fallback: print to stdout for debugging
            print("[!] Headers (not uploaded):")
            print(json.dumps(captured, indent=2))
            exit(1)
    else:
        print("[!] Failed to capture any headers")
        exit(1)


if __name__ == "__main__":
    main()
