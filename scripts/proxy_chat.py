"""
Full CloakBrowser proxy: interact with duck.ai directly via browser.
Triggered by workflow_dispatch with message + request_id inputs.
No manual API calls — the browser handles all anti-bot headers natively.
"""
import json
import os
import time
import requests
from cloakbrowser import launch

UPSTASH_URL = os.environ["UPSTASH_REDIS_REST_URL"]
UPSTASH_TOKEN = os.environ["UPSTASH_REDIS_REST_TOKEN"]
DDG_URL = "https://duck.ai"

MESSAGE = os.environ.get("CHAT_MESSAGE", "hello")
REQUEST_ID = os.environ.get("REQUEST_ID", "unknown")


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


def send_chat_via_browser(message):
    """Use CloakBrowser to type message into duck.ai and read the response."""
    print("[*] Launching CloakBrowser...")
    browser = launch(headless=True)
    page = browser.new_page()

    print("[*] Navigating to duck.ai...")
    page.goto(DDG_URL, wait_until="networkidle", timeout=60000)

    # Dismiss consent overlays
    time.sleep(2)
    for sel in [
        'button:has-text("Continue")',
        'button:has-text("Start chatting")',
        'button:has-text("Get Started")',
        'button:has-text("I Agree")',
        'button:has-text("Accept")',
    ]:
        try:
            btn = page.locator(sel)
            if btn.count() > 0 and btn.first.is_visible():
                print(f"[+] Clicking: {sel}")
                btn.first.click()
                time.sleep(2)
        except Exception:
            pass

    # Find and fill the chat input
    print(f"[*] Typing message: {message[:50]}...")
    input_found = False
    for sel in [
        'textarea',
        'textarea[placeholder]',
        'div[contenteditable="true"]',
        '#chat-input',
        'input[type="text"]',
    ]:
        try:
            el = page.locator(sel)
            if el.count() > 0 and el.first.is_visible():
                print(f"[+] Found input: {sel}")
                el.first.click()
                el.first.fill(message)
                input_found = True
                break
        except Exception:
            pass

    if not input_found:
        print("[!] Could not find chat input field")
        # Try to get the page HTML for debugging
        html = page.content()
        print(f"[*] Page title: {page.title()}")
        print(f"[*] Page URL: {page.url}")
        browser.close()
        return {"error": "Could not find chat input", "title": page.title(), "url": page.url}

    # Submit the message (Enter key)
    time.sleep(1)
    print("[*] Pressing Enter to submit...")
    page.keyboard.press("Enter")

    # Wait for response to appear and stabilize
    print("[*] Waiting for AI response...")
    time.sleep(15)  # Give DDG time to generate response

    # Try to find and read the response
    response_text = ""

    # Method 1: Look for message bubbles / response containers
    for sel in [
        '[data-testid="message-content"]',
        '.message-content',
        '[class*="response"]',
        '[class*="assistant"]',
        '[class*="bot-message"]',
        '[class*="ai-message"]',
        '[role="article"]',
        '.chat-message',
        '[data-message-author-role="assistant"]',
    ]:
        try:
            el = page.locator(sel)
            if el.count() > 0:
                texts = []
                for i in range(el.count()):
                    t = el.nth(i).inner_text().strip()
                    if t and t != message:
                        texts.append(t)
                if texts:
                    # Take the last message (the AI response)
                    response_text = texts[-1]
                    print(f"[+] Got response from selector: {sel}")
                    break
        except Exception:
            pass

    # Method 2: If no specific selector works, try getting all visible text after the input
    if not response_text:
        print("[*] Trying broad text extraction...")
        try:
            # Get all divs that appeared after we sent the message
            all_text = page.evaluate("""
                () => {
                    const msgs = document.querySelectorAll('div[class*="message"], div[class*="chat"], article, [data-testid]');
                    return Array.from(msgs).map(el => el.innerText.trim()).filter(t => t.length > 20);
                }
            """)
            if all_text:
                response_text = all_text[-1]
                print(f"[+] Got response from broad extraction: {response_text[:80]}")
        except Exception as e:
            print(f"[*] Broad extraction failed: {e}")

    # Method 3: Get full page text and try to extract the response
    if not response_text:
        print("[*] Trying full page text extraction...")
        try:
            full_text = page.evaluate("() => document.body.innerText")
            # The response should be after our message
            if message in full_text:
                parts = full_text.split(message, 1)
                if len(parts) > 1:
                    response_text = parts[1].strip()
                    # Clean up - take only until the next UI element
                    for stop in ["\nSend", "\nType a message", "\nNew Chat", "\nPowered by"]:
                        if stop in response_text:
                            response_text = response_text[:response_text.index(stop)].strip()
                    print(f"[+] Got response from page split: {response_text[:80]}")
        except Exception as e:
            print(f"[*] Page text extraction failed: {e}")

    # Take a screenshot for debugging
    try:
        page.screenshot(path="/tmp/ddg_response.png", full_page=True)
        print("[+] Saved screenshot to /tmp/ddg_response.png")
    except Exception:
        pass

    browser.close()

    if not response_text or len(response_text) < 5:
        return {
            "error": "Could not extract response from page",
            "attempted_text": response_text[:200] if response_text else "",
        }

    return {"status": "success", "model": "gpt-5-mini", "response": response_text}


def main():
    # Mark request as processing
    redis_set(f"chat:{REQUEST_ID}", {"status": "processing"}, ttl=120)

    # Send chat via browser
    result = send_chat_via_browser(MESSAGE)

    # Store response in Redis
    result["status"] = "done" if result.get("status") == "success" else "error"
    redis_set(f"chat:{REQUEST_ID}", result, ttl=120)
    print(f"[+] Stored result for request {REQUEST_ID}")
    print(f"[*] Result: {json.dumps(result)[:200]}")


if __name__ == "__main__":
    main()
