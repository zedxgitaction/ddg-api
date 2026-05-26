"""
Full CloakBrowser proxy: use duck.ai's own chat UI via browser.
Intercepts the chat API response directly from network — no header guessing.
"""
import json
import os
import time
import re
import requests
from cloakbrowser import launch

UPSTASH_URL = os.environ["UPSTASH_REDIS_REST_URL"]
UPSTASH_TOKEN = os.environ["UPSTASH_REDIS_REST_TOKEN"]
DDG_URL = "https://duck.ai"

MESSAGE = os.environ.get("CHAT_MESSAGE", "hello")
REQUEST_ID = os.environ.get("REQUEST_ID", "unknown")

chat_response = {"text": "", "done": False}


def redis_set(key, value, ttl=120):
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


def on_response(response):
    """Intercept DDG chat API response."""
    global chat_response
    url = response.url
    if "duckchat" in url and "/chat" in url:
        print(f"[+] Intercepted chat response: {url[:80]}")
        try:
            body = response.text()
            full_text = ""
            for line in body.split("\n"):
                if not line.startswith("data: ") or "[DONE]" in line:
                    continue
                data = line[6:]
                try:
                    j = json.loads(data)
                    if j.get("message"):
                        full_text += j["message"]
                except json.JSONDecodeError:
                    pass
            if full_text:
                chat_response["text"] = full_text.strip()
                chat_response["done"] = True
                print(f"[+] Captured response: {full_text[:80]}...")
        except Exception as e:
            print(f"[!] Error reading response: {e}")


def send_chat_via_browser(message):
    """Use CloakBrowser to chat on duck.ai by intercepting the API response."""
    global chat_response
    chat_response = {"text": "", "done": False}

    print("[*] Launching CloakBrowser...")
    browser = launch(headless=True)
    page = browser.new_page()
    page.on("response", on_response)

    print("[*] Navigating to duck.ai...")
    page.goto(DDG_URL, wait_until="domcontentloaded", timeout=60000)
    time.sleep(5)

    # Step 1: Dismiss overlays
    for _ in range(3):
        dismissed = False
        for sel in [
            'button:has-text("Continue")',
            'button:has-text("I Agree")',
            'button:has-text("Accept")',
            'button:has-text("Got It")',
            'button:has-text("Start chatting")',
            'button:has-text("Get Started")',
        ]:
            try:
                btn = page.locator(sel)
                if btn.count() > 0 and btn.first.is_visible():
                    print(f"[+] Clicking: {sel}")
                    btn.first.click()
                    time.sleep(2)
                    dismissed = True
                    break
            except Exception:
                pass
        if not dismissed:
            break

    time.sleep(3)
    page.screenshot(path="/tmp/ddg_chat_ui.png")

    # Step 2: Find the chat textarea (NOT the search bar)
    # duck.ai: search bar is in the header, chat input is in the chat area
    chat_input = None

    # Try specific chat input selectors
    for sel in [
        'textarea#chat-input',
        'textarea[name="chat-input"]',
        'textarea[aria-label*="chat" i]',
        'textarea[aria-label*="message" i]',
        'textarea[placeholder*="Ask" i]',
        'textarea[placeholder*="message" i]',
        'textarea[placeholder*="Type" i]',
        'div[role="textbox"][contenteditable="true"]',
    ]:
        try:
            el = page.locator(sel)
            if el.count() > 0 and el.first.is_visible():
                chat_input = el.first
                print(f"[+] Found chat input: {sel}")
                break
        except Exception:
            pass

    # Fallback: find textarea closest to bottom of viewport
    if not chat_input:
        print("[*] Fallback: finding textarea by position...")
        try:
            info = page.evaluate("""
                () => {
                    const textareas = document.querySelectorAll('textarea');
                    return Array.from(textareas).map((ta, i) => ({
                        index: i,
                        id: ta.id,
                        placeholder: ta.placeholder,
                        ariaLabel: ta.getAttribute('aria-label'),
                        rect: ta.getBoundingClientRect(),
                        visible: ta.offsetParent !== null
                    })).filter(t => t.visible);
                }
            """)
            print(f"[*] Visible textareas: {json.dumps(info)}")
            # Pick the one with the largest y (bottom-most)
            if info:
                best = max(info, key=lambda t: t['rect']['y'])
                idx = best['index']
                chat_input = page.locator('textarea').nth(idx)
                print(f"[+] Selected textarea #{idx} at y={best['rect']['y']}")
        except Exception as e:
            print(f"[*] Textarea detection failed: {e}")

    if not chat_input:
        print("[!] Could not find chat input")
        browser.close()
        return {"error": "Could not find chat input"}

    # Step 3: Type and submit
    print(f"[*] Typing: {message[:50]}")
    chat_input.click()
    time.sleep(0.5)
    chat_input.fill(message)
    time.sleep(1)

    print("[*] Submitting (Enter)...")
    page.keyboard.press("Enter")

    # Step 4: Wait for response to be intercepted
    print("[*] Waiting for chat API response...")
    for i in range(20):  # Up to 50 seconds
        time.sleep(2.5)
        if chat_response["done"]:
            break
        if i % 4 == 3:
            print(f"[*] Still waiting... ({(i+1)*2.5}s)")

    page.screenshot(path="/tmp/ddg_final.png")

    browser.close()

    if chat_response["done"] and chat_response["text"]:
        return {"status": "success", "model": "gpt-5-mini", "response": chat_response["text"]}

    return {"error": "No chat API response intercepted", "partial": chat_response["text"][:200]}


def main():
    redis_set(f"chat:{REQUEST_ID}", {"status": "processing"}, ttl=120)

    result = send_chat_via_browser(MESSAGE)

    result["status"] = "done" if result.get("status") == "success" else "error"
    redis_set(f"chat:{REQUEST_ID}", result, ttl=120)
    print(f"[+] Stored result for request {REQUEST_ID}")
    print(f"[*] Result: {json.dumps(result)[:300]}")


if __name__ == "__main__":
    main()
