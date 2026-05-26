"""
Full CloakBrowser proxy: capture headers AND send DDG chat request.
Triggered by workflow_dispatch with message + request_id inputs.
Stores response in Upstash Redis for Vercel API to retrieve.
"""
import json
import os
import time
import uuid
import base64
import requests
from cloakbrowser import launch

UPSTASH_URL = os.environ["UPSTASH_REDIS_REST_URL"]
UPSTASH_TOKEN = os.environ["UPSTASH_REDIS_REST_TOKEN"]
DDG_CHAT_URL = "https://duck.ai/duckchat/v1/chat"
DDG_PAGE_URL = "https://duck.ai"

# From workflow_dispatch inputs
MESSAGE = os.environ.get("CHAT_MESSAGE", "hello")
REQUEST_ID = os.environ.get("REQUEST_ID", "unknown")

captured = {}
cookies_list = []


def redis_set(key, value, ttl=120):
    """Store value in Upstash Redis with TTL."""
    r = requests.post(
        f"{UPSTASH_URL}/pipeline",
        headers={
            "Authorization": f"Bearer {UPSTASH_TOKEN}",
            "Content-Type": "application/json",
        },
        json=[["SET", key, json.dumps(value) if isinstance(value, dict) else value, "EX", ttl]],
        timeout=10,
    )
    return r.status_code == 200


def redis_get(key):
    """Get value from Upstash Redis."""
    r = requests.get(
        f"{UPSTASH_URL}/get/{key}",
        headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"},
        timeout=10,
    )
    data = r.json()
    if data.get("result"):
        try:
            return json.loads(data["result"])
        except (json.JSONDecodeError, TypeError):
            return data["result"]
    return None


def on_response(response):
    """Capture VQD hash from response headers."""
    global captured
    for key in ["x-vqd-hash-1", "x-vqd-4"]:
        val = response.headers.get(key.lower()) or response.headers.get(key)
        if val and key not in captured:
            captured[key] = val
            print(f"[+] Captured {key}")


def on_request(request):
    """Capture fe-version from outgoing requests."""
    global captured
    headers = request.headers
    for key in ["x-fe-version", "x-fe-signals"]:
        val = headers.get(key.lower())
        if val and key not in captured:
            captured[key] = val
            print(f"[+] Captured {key}")


def capture_headers():
    """Use CloakBrowser to capture fresh DDG headers."""
    global captured, cookies_list

    print("[*] Launching CloakBrowser...")
    browser = launch(headless=True)
    page = browser.new_page()
    page.on("request", on_request)
    page.on("response", on_response)

    print("[*] Navigating to duck.ai...")
    page.goto(DDG_PAGE_URL, wait_until="networkidle", timeout=60000)

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

    time.sleep(5)
    cookies_list = page.context.cookies()
    print(f"[+] Captured {len(cookies_list)} cookies")

    # Extract fe-version from page HTML if not captured
    if "x-fe-version" not in captured:
        try:
            html = page.content()
            import re
            match = re.search(r'serp_\d{8}_\d{6}_ET-[a-f0-9]+', html)
            if match:
                captured["x-fe-version"] = match.group(0)
                print(f"[+] Extracted fe-version from HTML")
        except Exception as e:
            print(f"[*] HTML extraction failed: {e}")

    browser.close()

    # Generate fe-signals if not captured
    if "x-fe-signals" not in captured:
        now = int(time.time() * 1000)
        signals = {
            "start": now,
            "events": [{"name": "startNewChat_free", "delta": 84}],
            "end": now + 100,
        }
        captured["x-fe-signals"] = base64.b64encode(
            json.dumps(signals).encode()
        ).decode()
        print("[+] Generated x-fe-signals")


def send_chat(message):
    """Send chat request to DDG using captured headers."""
    if not captured.get("x-vqd-hash-1") or not captured.get("x-fe-version"):
        print("[!] Missing required headers")
        return {"error": "Missing required headers", "have": list(captured.keys())}

    cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies_list) if cookies_list else ""

    now = int(time.time() * 1000)
    signals = {
        "start": now,
        "events": [{"name": "startNewChat_free", "delta": 84}],
        "end": now + 100,
    }
    fe_signals = base64.b64encode(json.dumps(signals).encode()).decode()

    payload = {
        "model": "gpt-5-mini",
        "metadata": {
            "toolChoice": {
                "NewsSearch": False,
                "VideosSearch": False,
                "LocalSearch": False,
                "WeatherForecast": False,
            },
            "x-vqd-hash-1": captured["x-vqd-hash-1"],
        },
        "messages": [{"role": "user", "content": message}],
    }

    headers = {
        "Content-Type": "application/json",
        "accept": "text/event-stream",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
        "x-fe-version": captured["x-fe-version"],
        "x-fe-signals": fe_signals,
        "x-ddg-journey-id": uuid.uuid4().hex,
        "Origin": "https://duck.ai",
        "Referer": "https://duck.ai/",
    }
    if cookie_str:
        headers["Cookie"] = cookie_str

    print(f"[*] Sending chat to DDG: {message[:50]}...")
    try:
        ddg_res = requests.post(DDG_CHAT_URL, headers=headers, json=payload, timeout=60, stream=True)
    except requests.RequestException as e:
        return {"error": f"Request failed: {e}"}

    if not ddg_res.ok:
        err_text = ddg_res.text[:500]
        print(f"[!] DDG returned {ddg_res.status}: {err_text}")
        return {
            "error": f"DDG returned {ddg_res.status}",
            "detail": err_text,
            "status_code": ddg_res.status,
        }

    # Parse SSE response
    full_text = ""
    raw = ddg_res.text
    for line in raw.split("\n"):
        if not line.startswith("data: ") or "[DONE]" in line:
            continue
        try:
            j = json.loads(line[6:])
            if j.get("message"):
                full_text += j["message"]
        except json.JSONDecodeError:
            pass

    if not full_text:
        for line in raw.split("\n"):
            if not line.startswith("data: ") or "[DONE]" in line:
                continue
            try:
                j = json.loads(line[6:])
                parts = j.get("messages", [{}])[0].get("parts", [])
                for p in parts:
                    if p.get("text"):
                        full_text += p["text"]
            except (json.JSONDecodeError, IndexError, KeyError):
                pass

    result = full_text.strip() or "No response text extracted"
    print(f"[+] Got response: {result[:80]}...")
    return {"status": "success", "model": "gpt-5-mini", "response": result}


def main():
    # Mark request as processing
    redis_set(f"chat:{REQUEST_ID}", {"status": "processing"}, ttl=120)

    # Step 1: Capture headers
    capture_headers()
    if not captured:
        redis_set(f"chat:{REQUEST_ID}", {"status": "error", "error": "Failed to capture headers"}, ttl=120)
        exit(1)

    # Step 2: Send chat
    result = send_chat(MESSAGE)

    # Step 3: Store response in Redis
    result["status"] = "done" if result.get("status") == "success" else "error"
    redis_set(f"chat:{REQUEST_ID}", result, ttl=120)
    print(f"[+] Stored result for request {REQUEST_ID}")


if __name__ == "__main__":
    main()
