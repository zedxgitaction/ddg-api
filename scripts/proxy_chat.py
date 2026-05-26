"""
Full CloakBrowser proxy: interact with duck.ai chat directly via browser.
Triggered by workflow_dispatch with message + request_id inputs.
Uses DOM extraction (works for text AND images) with network intercept backup.
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


def click_any(page, selectors):
    """Click the first visible matching selector."""
    for sel in selectors:
        try:
            btn = page.locator(sel)
            if btn.count() > 0 and btn.first.is_visible():
                print(f"[+] Clicking: {sel}")
                btn.first.click()
                time.sleep(2)
                return True
        except Exception:
            pass
    return False


def extract_response(page, message):
    """Extract AI response from the page DOM — works for text AND images."""
    result = page.evaluate("""
        (userMsg) => {
            // Strategy 1: Find all message containers
            // duck.ai uses various structures — try them all
            const selectors = [
                '[data-message-author-role="assistant"]',
                '[data-testid*="message"]',
                '.chat-message',
                '[class*="Message"]',
                '[class*="message"]',
                '[class*="response"]',
                '[class*="assistant"]',
                'article',
                '[role="article"]',
            ];

            for (const sel of selectors) {
                const els = document.querySelectorAll(sel);
                if (els.length > 0) {
                    const last = els[els.length - 1];
                    const text = last.innerText.trim();
                    const images = last.querySelectorAll('img');
                    const imgSrcs = Array.from(images).map(img => img.src).filter(s => s && !s.includes('data:'));

                    if (text && text.length > 10 && !text.includes(userMsg.substring(0, 20))) {
                        return { type: 'text', content: text, images: imgSrcs };
                    }
                    if (imgSrcs.length > 0) {
                        return { type: 'image', content: text || '', images: imgSrcs };
                    }
                }
            }

            // Strategy 2: Find the last substantial text block after user message
            const body = document.body.innerText;
            const msgIdx = body.indexOf(userMsg);
            if (msgIdx >= 0) {
                const after = body.substring(msgIdx + userMsg.length).trim();
                // Find images on page that might be generated
                const allImgs = document.querySelectorAll('img[src*="duckduckgo"], img[src*="blob"], img[src*="generated"]');
                const imgSrcs = Array.from(allImgs).map(img => img.src);
                if (after.length > 10) {
                    return { type: 'text', content: after, images: imgSrcs };
                }
            }

            // Strategy 3: Just grab all images and last text
            const allImgs = document.querySelectorAll('img');
            const imgSrcs = Array.from(allImgs)
                .map(img => img.src)
                .filter(s => s && !s.startsWith('data:') && !s.includes('logo') && !s.includes('icon') && s.length > 50);

            return { type: 'raw', content: body.substring(0, 2000), images: imgSrcs };
        }
    """, message)
    return result


def send_chat_via_browser(message):
    """Use CloakBrowser to chat on duck.ai directly."""
    print("[*] Launching CloakBrowser...")
    browser = launch(headless=True)
    page = browser.new_page()

    print("[*] Navigating to duck.ai...")
    page.goto(DDG_URL, wait_until="domcontentloaded", timeout=60000)
    time.sleep(5)

    # Step 1: Dismiss overlays
    for _ in range(3):
        dismissed = click_any(page, [
            'button:has-text("Continue")',
            'button:has-text("I Agree")',
            'button:has-text("Accept")',
            'button:has-text("Got It")',
            'button:has-text("Start chatting")',
            'button:has-text("Get Started")',
        ])
        if not dismissed:
            break
        time.sleep(1)

    time.sleep(3)

    # Step 2: Find chat input
    chat_input = None
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

    if not chat_input:
        print("[*] Fallback: finding textarea by position...")
        try:
            info = page.evaluate("""
                () => {
                    const textareas = document.querySelectorAll('textarea');
                    return Array.from(textareas).map((ta, i) => ({
                        index: i,
                        placeholder: ta.placeholder,
                        rect: ta.getBoundingClientRect(),
                        visible: ta.offsetParent !== null
                    })).filter(t => t.visible);
                }
            """)
            print(f"[*] Visible textareas: {json.dumps(info)}")
            if info:
                best = max(info, key=lambda t: t['rect']['y'])
                chat_input = page.locator('textarea').nth(best['index'])
                print(f"[+] Selected textarea #{best['index']}")
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

    # Step 4: Wait for response to appear and stabilize
    print("[*] Waiting for response...")
    last_result = None
    stable_count = 0

    for i in range(20):  # Up to 50 seconds
        time.sleep(2.5)
        current = extract_response(page, message)

        if current and current.get("content"):
            content = current["content"]
            # Stop words that indicate UI text, not AI response
            stop_words = ["How It Works", "Send", "New Chat", "Type a message", "Got It!"]
            for sw in stop_words:
                content = content.replace(sw, "").strip()

            current["content"] = content

            if len(content) > 10 or current.get("images"):
                if last_result and current.get("content") == last_result.get("content") and current.get("images") == last_result.get("images"):
                    stable_count += 1
                    if stable_count >= 2:
                        print(f"[+] Response stabilized at {(i+1)*2.5}s")
                        break
                else:
                    stable_count = 0
                    last_result = current
                    if i % 2 == 0:
                        print(f"[*] Response growing... ({len(content)} chars, {len(current.get('images', []))} images)")

    page.screenshot(path="/tmp/ddg_final.png")

    browser.close()

    if not last_result or (not last_result.get("content") and not last_result.get("images")):
        return {"error": "No response extracted"}

    # Build result
    result = {"status": "success", "model": "gpt-5-mini"}

    if last_result.get("images"):
        result["images"] = last_result["images"]
        result["type"] = "image"

    if last_result.get("content"):
        text = last_result["content"].strip()
        # Clean up: remove our original message if it appears in the response
        if message in text:
            text = text.split(message, 1)[-1].strip()
        result["response"] = text

    if not result.get("response") and not result.get("images"):
        return {"error": "Could not extract meaningful response"}

    return result


def main():
    redis_set(f"chat:{REQUEST_ID}", {"status": "processing"}, ttl=120)

    result = send_chat_via_browser(MESSAGE)

    result["status"] = "done" if result.get("status") == "success" else "error"
    redis_set(f"chat:{REQUEST_ID}", result, ttl=120)
    print(f"[+] Stored result for request {REQUEST_ID}")
    print(f"[*] Result: {json.dumps(result)[:300]}")


if __name__ == "__main__":
    main()
