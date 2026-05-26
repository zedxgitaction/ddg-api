"""
CloakBrowser-powered header capture for DuckDuckGo AI Chat.
Visits duck.ai, captures VQD/hash headers from page requests,
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

# All headers we need
CAPTURE_HEADERS = [
    "x-fe-version",
    "x-fe-signals",
    "x-vqd-hash-1",
    "x-ddg-journey-id",
]

captured = {}


def on_request(request):
    """Intercept ALL requests and capture DDG headers."""
    global captured
    headers = request.headers
    for key in CAPTURE_HEADERS:
        if key not in captured:
            val = headers.get(key.lower())
            if val:
                captured[key] = val
                print(f"[+] Captured {key}")


def on_response(response):
    """Check response headers for VQD info."""
    global captured
    headers = response.headers
    for key in ["x-vqd-hash-1", "x-vqd-4"]:
        if key not in captured:
            val = headers.get(key.lower()) or headers.get(key)
            if val:
                captured[key] = val
                print(f"[+] Captured {key} from response header")


def upload_to_redis(headers_dict):
    """Store captured headers in Upstash Redis with 240s TTL."""
    if not UPSTASH_URL or not UPSTASH_TOKEN:
        print("[!] UPSTASH_REDIS_REST_URL or UPSTASH_REDIS_REST_TOKEN not set")
        return False

    data = json.dumps(headers_dict)
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
    global captured

    print("[*] Launching CloakBrowser...")
    browser = launch(headless=True)
    page = browser.new_page()
    page.on("request", on_request)
    page.on("response", on_response)

    print("[*] Navigating to duck.ai...")
    page.goto(DDG_URL, wait_until="networkidle", timeout=60000)

    # Dismiss consent overlay
    print("[*] Looking for consent buttons...")
    time.sleep(2)
    try:
        for selector in [
            'button:has-text("Continue")',
            'button:has-text("Start chatting")',
            'button:has-text("Get Started")',
            'button:has-text("Accept")',
            'button:has-text("I agree")',
            'button:has-text("Try it")',
        ]:
            btn = page.locator(selector)
            if btn.count() > 0 and btn.first.is_visible():
                print(f"[+] Clicking: {selector}")
                btn.first.click()
                time.sleep(2)
                break
    except Exception as e:
        print(f"[*] No consent button: {e}")

    # Wait for page to stabilize
    time.sleep(3)

    # Check if we got all headers from page load
    missing = [h for h in CAPTURE_HEADERS if h not in captured]
    if missing:
        print(f"[*] Missing headers from page load: {missing}")
        print("[*] Sending a trigger message to capture remaining headers...")
        try:
            textarea = page.locator("textarea").first
            textarea.click()
            time.sleep(0.5)
            textarea.fill("hi")
            time.sleep(0.5)
            send_btn = page.locator('[aria-label*="send" i]')
            if send_btn.count() == 0:
                send_btn = page.locator('button[type="submit"]')
            if send_btn.count() > 0 and send_btn.first.is_visible():
                send_btn.first.click()
            else:
                textarea.press("Enter")
            time.sleep(5)
        except Exception as e:
            print(f"[!] Trigger message error: {e}")

    browser.close()

    if captured:
        print(f"[+] Final captured headers: {list(captured.keys())}")
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
