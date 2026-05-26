"""
Full CloakBrowser proxy: interact with duck.ai chat directly via browser.
Triggered by workflow_dispatch with message + request_id inputs.
Intercepts network requests for image URLs + DOM text extraction.
"""
import json
import os
import time
import base64
import requests
from cloakbrowser import launch

UPSTASH_URL = os.environ["UPSTASH_REDIS_REST_URL"]
UPSTASH_TOKEN = os.environ["UPSTASH_REDIS_REST_TOKEN"]
DDG_URL = "https://duck.ai"

MESSAGE = os.environ.get("CHAT_MESSAGE", "hello")
REQUEST_ID = os.environ.get("REQUEST_ID", "unknown")

# Collect image URLs from network
captured_images = []


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


def on_request(request):
    """Capture image URLs from outgoing requests."""
    url = request.url
    # DDG image URLs from their generation service
    if any(x in url for x in ["blob:", "generated", "image", "creativity", "img"]):
        if url not in captured_images and not url.startswith("data:"):
            captured_images.append(url)
            print(f"[+] Captured image URL: {url[:80]}")


def on_response(response):
    """Capture image URLs from responses."""
    url = response.url
    content_type = ""
    try:
        content_type = response.headers.get("content-type", "")
    except Exception:
        pass

    if "image" in content_type or any(x in url for x in ["generated", "creativity", "imgproxy"]):
        if url not in captured_images:
            captured_images.append(url)
            print(f"[+] Captured image from response: {url[:80]}")


def extract_images_from_dom(page):
    """Extract image URLs/blobs from the DOM."""
    return page.evaluate("""
        () => {
            const images = [];

            // Get all img elements
            document.querySelectorAll('img').forEach(img => {
                const src = img.src || img.getAttribute('src') || '';
                if (src && !src.startsWith('data:') && src.length > 20) {
                    // Filter out logos/icons/avatars
                    if (!src.includes('logo') && !src.includes('icon') && !src.includes('avatar')
                        && !src.includes('favicon') && !src.includes('badge')) {
                        images.push(src);
                    }
                }
            });

            // Check for canvas elements (DDG sometimes renders images to canvas)
            document.querySelectorAll('canvas').forEach((canvas, i) => {
                try {
                    const dataUrl = canvas.toDataURL('image/png');
                    if (dataUrl && dataUrl.length > 1000) {
                        images.push(dataUrl);
                    }
                } catch(e) {}
            });

            // Check background images
            document.querySelectorAll('[style*="background-image"]').forEach(el => {
                const style = el.getAttribute('style') || '';
                const match = style.match(/url\(['"]?([^'")\s]+)['"]?\)/);
                if (match && match[1] && match[1].length > 20) {
                    images.push(match[1]);
                }
            });

            return [...new Set(images)];
        }
    """)


def extract_text_response(page, message):
    """Extract text response from DOM."""
    return page.evaluate("""
        (userMsg) => {
            // Find all message-like containers
            const selectors = [
                '[data-message-author-role="assistant"]',
                '[data-testid*="message"]',
                '[class*="Message"]',
                '[class*="message"]',
                '[class*="response"]',
                'article',
            ];

            for (const sel of selectors) {
                const els = document.querySelectorAll(sel);
                if (els.length > 0) {
                    const text = els[els.length - 1].innerText.trim();
                    if (text && text.length > 5) {
                        return text;
                    }
                }
            }

            // Fallback: find text after user message
            const body = document.body.innerText;
            const msgIdx = body.indexOf(userMsg);
            if (msgIdx >= 0) {
                const after = body.substring(msgIdx + userMsg.length).trim();
                // Remove common UI noise
                const lines = after.split('\\n').filter(l => {
                    const t = l.trim();
                    return t && !['Got It!', 'How It Works', 'Send', 'New Chat',
                                 'Type a message', 'All chats are private', 'AI can make mistakes',
                                 'Fast', 'Tools', 'Hide Reasoning', 'Related Searches'].includes(t);
                });
                return lines.join('\\n').trim();
            }

            return '';
        }
    """, message)


def send_chat_via_browser(message):
    global captured_images
    captured_images = []

    is_image_request = any(w in message.lower() for w in [
        "image", "picture", "photo", "draw", "generate", "illustration",
        "render", "create a", "make a"
    ])

    print(f"[*] Image request detected: {is_image_request}")
    print("[*] Launching CloakBrowser...")
    browser = launch(headless=True)
    page = browser.new_page()
    page.on("request", on_request)
    page.on("response", on_response)

    print("[*] Navigating to duck.ai...")
    page.goto(DDG_URL, wait_until="domcontentloaded", timeout=60000)
    time.sleep(5)

    # Dismiss overlays
    for _ in range(3):
        if not click_any(page, [
            'button:has-text("Continue")',
            'button:has-text("I Agree")',
            'button:has-text("Accept")',
            'button:has-text("Got It")',
            'button:has-text("Start chatting")',
            'button:has-text("Get Started")',
        ]):
            break
        time.sleep(1)

    time.sleep(3)

    # Find chat input
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
        print("[*] Fallback textarea search...")
        try:
            info = page.evaluate("""
                () => document.querySelectorAll('textarea').length
            """)
            for i in range(info):
                ta = page.locator('textarea').nth(i)
                if ta.is_visible():
                    chat_input = ta
                    print(f"[+] Using textarea #{i}")
                    break
        except Exception:
            pass

    if not chat_input:
        browser.close()
        return {"error": "Could not find chat input"}

    # Type and submit
    print(f"[*] Typing: {message[:50]}")
    chat_input.click()
    time.sleep(0.5)
    chat_input.fill(message)
    time.sleep(1)
    print("[*] Submitting...")
    page.keyboard.press("Enter")

    # Wait for response (longer for image requests)
    wait_time = 60 if is_image_request else 30
    print(f"[*] Waiting up to {wait_time}s for response...")

    last_text = ""
    last_images = []
    stable_count = 0

    for i in range(int(wait_time / 2.5)):
        time.sleep(2.5)

        text = extract_text_response(page, message)
        dom_images = extract_images_from_dom(page)

        all_images = list(set(captured_images + dom_images))

        if text or all_images:
            if text == last_text and all_images == last_images:
                stable_count += 1
                if stable_count >= 3:  # 7.5s stable
                    print(f"[+] Response stabilized at {(i+1)*2.5}s")
                    break
            else:
                stable_count = 0
                last_text = text
                last_images = all_images
                if i % 2 == 0:
                    print(f"[*] Growing... text={len(text)} chars, images={len(all_images)}")

    page.screenshot(path="/tmp/ddg_final.png")
    browser.close()

    # Build result
    result = {"status": "success", "model": "gpt-5-mini"}

    # Clean text
    if last_text:
        text = last_text.strip()
        # Remove UI noise
        for noise in ["GPT-5 mini", "Fast", "Tools", "Hide Reasoning",
                       "Related Searches", "All chats are private", "AI can make mistakes"]:
            text = text.replace(noise, "").strip()
        # Remove leading/trailing newlines
        text = text.strip('\n').strip()
        if text:
            result["response"] = text

    # Add images
    if last_images:
        result["images"] = last_images
        result["type"] = "image"

    if not result.get("response") and not result.get("images"):
        return {"error": "No response extracted"}

    return result


def main():
    redis_set(f"chat:{REQUEST_ID}", {"status": "processing"}, ttl=180)

    result = send_chat_via_browser(MESSAGE)

    result["status"] = "done" if result.get("status") == "success" else "error"
    redis_set(f"chat:{REQUEST_ID}", result, ttl=180)
    print(f"[+] Stored result for request {REQUEST_ID}")
    print(f"[*] Result: {json.dumps(result)[:300]}")


if __name__ == "__main__":
    main()
