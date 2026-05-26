"""
CloakBrowser proxy: interact with duck.ai chat directly via browser.
For images: intercepts network responses + extracts from DOM, uploads to tmpfiles.org.
For text: extracts assistant reply from DOM.
Image requests return ONLY images (no text). Prompts are crafted to avoid DDG follow-up questions.
"""
import json
import os
import time
import base64
import random
import requests
from cloakbrowser import launch

UPSTASH_URL = os.environ["UPSTASH_REDIS_REST_URL"]
UPSTASH_TOKEN = os.environ["UPSTASH_REDIS_REST_TOKEN"]
DDG_URL = "https://duck.ai"

MESSAGE = os.environ.get("CHAT_MESSAGE", "hello")
REQUEST_ID = os.environ.get("REQUEST_ID", "unknown")

# Proxies for rotation
PROXIES = [
    "http://purevpn0s8946341:8RXxgcU2MBumt8@px043005.pointtoserver.com:10780",
    "http://purevpn0s12153504:1LTpwxbCJbEdXo@px043005.pointtoserver.com:10780",
    "http://purevpn0s8946341:8RXxgcU2MBumt8@px031901.pointtoserver.com:10780",
    "http://1351:IBd1Fk5CuUNZ@p101.squidproxies.com:9088",
    "http://llewellynashleybowen:rNXaRJfNPN233zw@136.179.19.164:3128",
]


def get_random_proxy():
    return random.choice(PROXIES)


def redis_set(key, value, ttl=180):
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


def upload_to_tmpfiles(image_bytes, filename="image.png"):
    """Upload image bytes to tmpfiles.org, return the direct download URL."""
    try:
        r = requests.post(
            "https://tmpfiles.org/api/v1/upload",
            files={"file": (filename, image_bytes, "image/png")},
            timeout=30,
        )
        data = r.json()
        if data.get("status") == "success" and data.get("data", {}).get("url"):
            url = data["data"]["url"]
            direct = url.replace("tmpfiles.org/", "tmpfiles.org/dl/")
            print(f"[+] Uploaded: {direct}")
            return direct
        print(f"[!] tmpfiles response: {data}")
        return None
    except Exception as e:
        print(f"[!] tmpfiles upload failed: {e}")
        return None


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


def extract_images_from_page(page):
    """Extract base64 images from canvas, data-URLs, blob, and <img> tags."""
    return page.evaluate("""
        async () => {
            const results = [];

            // 1. <img> tags with data: URLs
            document.querySelectorAll('img[src^="data:"]').forEach(img => {
                const src = img.src;
                if (src.length > 5000) {
                    results.push({ type: 'base64', data: src, width: img.naturalWidth, height: img.naturalHeight });
                }
            });

            // 2. <img> tags with blob: URLs
            for (const img of document.querySelectorAll('img[src^="blob:"]')) {
                try {
                    const resp = await fetch(img.src);
                    const buf = await resp.arrayBuffer();
                    const arr = Array.from(new Uint8Array(buf));
                    if (arr.length > 5000) {
                        results.push({ type: 'blob_bytes', data: arr, width: img.naturalWidth, height: img.naturalHeight });
                    }
                } catch(e) {}
            }

            // 3. Background images that are data: URLs
            document.querySelectorAll('[style*="data:image"]').forEach(el => {
                const style = el.getAttribute('style') || '';
                const match = style.match(/url\\((data:image\\/[^)]+)\\)/);
                if (match && match[1].length > 5000) {
                    results.push({ type: 'base64', data: match[1], width: 0, height: 0 });
                }
            });

            // 4. Regular <img> tags with http/https URLs
            document.querySelectorAll('img[src^="http"]').forEach(img => {
                const src = img.src;
                const w = img.naturalWidth || img.width;
                const h = img.naturalHeight || img.height;
                const skip = ['favicon', 'icon', 'avatar', 'logo', '.svg'];
                if (w > 50 && h > 50 && !skip.some(k => src.toLowerCase().includes(k))) {
                    results.push({ type: 'url', data: src, width: w, height: h });
                }
            });

            // 5. <a> tags linking directly to image files
            document.querySelectorAll('a[href]').forEach(a => {
                const href = a.href;
                if (/\\.(png|jpg|jpeg|webp|gif)(\\?|$)/i.test(href)) {
                    results.push({ type: 'url', data: href, width: 0, height: 0 });
                }
            });

            return results;
        }
    """)


def is_image_request(message):
    """Detect if message is asking for image generation."""
    msg = message.lower()
    keywords = [
        "image", "picture", "photo", "draw", "generate", "illustration",
        "render", "create a", "make a", "design", "paint", "sketch",
        "artwork", "art of", "poster", "wallpaper", "3d model",
        "convert", "edit this", "turn this into"
    ]
    return any(w in msg for w in keywords)


def craft_image_prompt(message):
    """
    Craft a direct prompt for image generation that avoids DDG follow-up questions.
    DDG tends to ask clarifying questions instead of generating images. This forces
    a direct image generation response.
    """
    msg = message.strip()

    # If message is already a direct instruction like "make an image of X", keep it
    direct_starters = [
        "make an image", "generate an image", "create an image",
        "draw", "paint", "sketch", "render", "design",
        "create a picture", "make a picture", "generate a picture",
        "create artwork", "make artwork",
    ]
    if any(msg.lower().startswith(s) for s in direct_starters):
        return msg

    # Otherwise, wrap it as a direct image generation request
    # This avoids DDG asking "what style?" or "what dimensions?"
    return f"Generate an image of: {msg}. Create this image now without asking questions."


def send_chat_via_browser(message):
    img_request = is_image_request(message)
    proxy_url = get_random_proxy()

    print(f"[*] Image request: {img_request}")
    print(f"[*] Proxy: {proxy_url[:40]}...")
    print("[*] Launching CloakBrowser...")

    # TODO: CloakBrowser proxy support - uncomment when verified
    # browser = launch(headless=True, proxy={"server": proxy_url})
    browser = launch(headless=True)
    page = browser.new_page()

    # Network-level image capture
    captured_images = []

    def on_response(response):
        try:
            ct = response.headers.get("content-type", "")
            url = response.url
            if ct.startswith("image/") and response.status == 200:
                cl = int(response.headers.get("content-length", "0"))
                if cl > 5000 or cl == 0:
                    print(f"[+] Intercepted image: {url[:80]} ({ct}, {cl}b)")
                    body = response.body()
                    if len(body) > 5000:
                        captured_images.append({"url": url, "body": body, "content_type": ct})
        except Exception:
            pass

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
                print(f"[+] Found input: {sel}")
                break
        except Exception:
            pass

    if not chat_input:
        print("[*] Fallback textarea search...")
        try:
            count = page.evaluate("() => document.querySelectorAll('textarea').length")
            for i in range(count):
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

    # Craft prompt to avoid DDG follow-up questions
    final_prompt = craft_image_prompt(message) if img_request else message

    print(f"[*] Typing: {final_prompt[:80]}")
    chat_input.click()
    time.sleep(0.5)
    chat_input.fill(final_prompt)
    time.sleep(1)
    print("[*] Submitting...")
    page.keyboard.press("Enter")

    # Wait for response
    wait_time = 90 if img_request else 30
    print(f"[*] Waiting up to {wait_time}s...")

    last_text = ""
    last_image_count = 0
    stable_count = 0

    for i in range(int(wait_time / 2.5)):
        time.sleep(2.5)

        images = extract_images_from_page(page)
        img_count = len(images)

        if img_request:
            # For image requests: only care about images, ignore text
            if img_count > 0:
                if img_count == last_image_count:
                    stable_count += 1
                    if stable_count >= 4:
                        print(f"[+] Images stable at {(i+1)*2.5}s: {img_count} images")
                        break
                else:
                    stable_count = 0
                    last_image_count = img_count
                    if i % 2 == 0:
                        print(f"[*] Growing: {img_count} images")
            else:
                # Keep waiting for images
                if (i + 1) * 2.5 >= 60:
                    print(f"[*] No images after 60s, giving up")
                    break
        else:
            # For text requests: care about text stability
            text = page.evaluate("""
                () => {
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
                            if (text && text.length > 5) return text;
                        }
                    }
                    return '';
                }
            """)
            if text:
                if text == last_text:
                    stable_count += 1
                    if stable_count >= 4:
                        print(f"[+] Text stable at {(i+1)*2.5}s: {len(text)} chars")
                        break
                else:
                    stable_count = 0
                    last_text = text
                    if i % 2 == 0:
                        print(f"[*] Growing: {len(text)} chars")

    # Extract final images
    final_images = extract_images_from_page(page)

    # Screenshot for fallback
    screenshot_bytes = None
    try:
        page.screenshot(path="/tmp/ddg_final.png", full_page=True)
        with open("/tmp/ddg_final.png", "rb") as f:
            screenshot_bytes = f.read()
        print(f"[*] Screenshot: {len(screenshot_bytes)} bytes")
    except Exception as e:
        print(f"[!] Screenshot failed: {e}")
    browser.close()

    # Build result
    result = {"status": "success", "model": "gpt-5-mini", "proxy": proxy_url[:40]}

    # Upload images to tmpfiles.org
    tmp_urls = []

    # PRIORITY 1: Network-intercepted images
    if captured_images:
        print(f"[*] Processing {len(captured_images)} network-intercepted image(s)...")
        for idx, img in enumerate(captured_images):
            ct = img["content_type"]
            ext = "png"
            if "jpeg" in ct or "jpg" in ct:
                ext = "jpg"
            elif "webp" in ct:
                ext = "webp"
            elif "gif" in ct:
                ext = "gif"
            url = upload_to_tmpfiles(img["body"], f"ddg_image_{idx}.{ext}")
            if url:
                tmp_urls.append(url)

    # PRIORITY 2: DOM-extracted images
    if not tmp_urls and final_images:
        print(f"[*] No network images, falling back to {len(final_images)} DOM image(s)...")
        for idx, img in enumerate(final_images):
            img_data = img.get("data", "")
            img_type = img.get("type", "")
            try:
                if img_type == "blob_bytes" and isinstance(img_data, list):
                    img_bytes = bytes(img_data)
                    url = upload_to_tmpfiles(img_bytes, f"ddg_image_{idx}.png")
                    if url:
                        tmp_urls.append(url)
                elif isinstance(img_data, str) and img_data.startswith("data:image/"):
                    header, b64 = img_data.split(",", 1)
                    ext = "png" if "png" in header else "jpg"
                    img_bytes = base64.b64decode(b64)
                    url = upload_to_tmpfiles(img_bytes, f"ddg_image_{idx}.{ext}")
                    if url:
                        tmp_urls.append(url)
                elif img_type == "url" and isinstance(img_data, str) and img_data.startswith("http"):
                    print(f"[*] Downloading: {img_data[:100]}")
                    dl = requests.get(img_data, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
                    if dl.status_code == 200 and len(dl.content) > 1000:
                        ct = dl.headers.get("content-type", "")
                        ext = "png"
                        if "jpeg" in ct or "jpg" in ct: ext = "jpg"
                        elif "webp" in ct: ext = "webp"
                        elif "gif" in ct: ext = "gif"
                        url = upload_to_tmpfiles(dl.content, f"ddg_image_{idx}.{ext}")
                        if url:
                            tmp_urls.append(url)
            except Exception as e:
                print(f"[!] Failed to process image {idx}: {e}")

    if tmp_urls:
        result["images"] = tmp_urls
        result["type"] = "image"

    # Fallback: screenshot if image request but no images extracted
    if img_request and not result.get("images") and screenshot_bytes and len(screenshot_bytes) > 5000:
        print("[*] No DOM images found, uploading screenshot as fallback...")
        url = upload_to_tmpfiles(screenshot_bytes, "ddg_screenshot.png")
        if url:
            result["images"] = [url]
            result["type"] = "image"

    # For image requests: return ONLY images, no text
    if img_request:
        if result.get("images"):
            # Strip any text — only images
            result.pop("response", None)
        else:
            result["error"] = "No images generated"
    else:
        # Text requests: extract final text
        if last_text:
            text = last_text.strip()
            for noise in ["GPT-5 mini", "Fast", "Tools", "Hide Reasoning",
                          "Related Searches", "All chats are private", "AI can make mistakes",
                          "Reasoning"]:
                text = text.replace(noise, "").strip()
            text = text.strip('\n').strip()
            if text:
                result["response"] = text

    if not result.get("response") and not result.get("images"):
        return {"error": "No response extracted"}

    return result


def main():
    redis_set(f"chat:{REQUEST_ID}", {"status": "processing"}, ttl=180)

    result = send_chat_via_browser(MESSAGE)

    result["status"] = "done" if result.get("status") == "success" else "error"
    redis_set(f"chat:{REQUEST_ID}", result, ttl=180)
    print(f"[+] Stored result for request {REQUEST_ID}")
    print(f"[*] Result: {json.dumps(result)[:500]}")


if __name__ == "__main__":
    main()
