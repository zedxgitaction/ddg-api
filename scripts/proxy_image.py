"""
CloakBrowser proxy: dedicated image generation via duck.ai sidebar.
Clicks Image tab in sidebar -> types prompt -> submits -> captures generated image.
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

PROMPT = os.environ.get("IMAGE_PROMPT", "a cute cat")
REQUEST_ID = os.environ.get("REQUEST_ID", "unknown")


def redis_set(key, value, ttl=300):
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
    try:
        r = requests.get(
            f"{UPSTASH_URL}/get/{key}",
            headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"},
            timeout=10,
        )
        data = r.json()
        if data.get("result"):
            return json.loads(data["result"])
    except Exception:
        pass
    return None


def redis_set_raw(key, value, ttl=300):
    r = requests.post(
        f"{UPSTASH_URL}/pipeline",
        headers={
            "Authorization": f"Bearer {UPSTASH_TOKEN}",
            "Content-Type": "application/json",
        },
        json=[["SET", key, value, "EX", ttl]],
        timeout=10,
    )
    return r.status_code == 200


def upload_to_tmpfiles(content, filename="image.png"):
    for attempt in range(3):
        try:
            r = requests.post(
                "https://tmpfiles.org/api/v1/upload",
                files={"file": (filename, content)},
                timeout=30,
            )
            if r.status_code == 200:
                data = r.json()
                if data.get("data", {}).get("url"):
                    url = data["data"]["url"].replace("tmpfiles.org/", "tmpfiles.org/dl/")
                    print(f"[+] Uploaded: {url}")
                    return url
        except Exception as e:
            print(f"[!] Upload attempt {attempt + 1} failed: {e}")
        time.sleep(2)

    # Fallback to 0x0.st
    try:
        r = requests.post("https://0x0.st", files={"file": (filename, content)}, timeout=30)
        if r.status_code == 200:
            url = r.text.strip()
            print(f"[+] Uploaded to 0x0.st: {url}")
            return url
    except Exception as e:
        print(f"[!] 0x0.st fallback failed: {e}")

    # Fallback to file.io
    try:
        r = requests.post("https://file.io", files={"file": (filename, content)}, timeout=30)
        if r.status_code == 200:
            data = r.json()
            if data.get("link"):
                print(f"[+] Uploaded to file.io: {data['link']}")
                return data["link"]
    except Exception as e:
        print(f"[!] file.io fallback failed: {e}")

    return None


def is_gif(filename, content=None):
    """Check if file is a GIF by extension or content (magic bytes)."""
    if filename.endswith('.gif'):
        return True
    if content and len(content) > 4 and content[:3] == b'GIF':
        return True
    return False


def find_chat_input(page):
    """Find the chat input textarea on duck.ai."""
    selectors = [
        'textarea[placeholder*="Ask" i]',
        'textarea[placeholder*="Type" i]',
        'textarea[placeholder*="Message" i]',
        'textarea[placeholder*="chat" i]',
        'textarea[data-testid*="chat" i]',
        'textarea[data-testid*="input" i]',
        'textarea',
    ]
    for sel in selectors:
        try:
            el = page.locator(sel)
            if el.count() > 0:
                for i in range(el.count()):
                    if el.nth(i).is_visible():
                        print(f"[+] Found input: {sel} (index {i})")
                        return el.nth(i)
        except Exception:
            pass
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


def dismiss_overlays(page):
    """Dismiss cookie/onboarding overlays."""
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


def extract_images_from_page(page):
    """Extract base64 images from canvas, data-URLs, blob, and img tags."""
    return page.evaluate("""
        async () => {
            const results = [];

            document.querySelectorAll('img[src^="data:"]').forEach(img => {
                const src = img.src;
                if (src.length > 5000) {
                    results.push({ type: 'base64', data: src, width: img.naturalWidth, height: img.naturalHeight });
                }
            });

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

            document.querySelectorAll('[style*="data:image"]').forEach(el => {
                const style = el.getAttribute('style') || '';
                const match = style.match(/url\\((data:image\\/[^)]+)\\)/);
                if (match && match[1].length > 5000) {
                    results.push({ type: 'base64', data: match[1], width: 0, height: 0 });
                }
            });

            document.querySelectorAll('img[src^="http"]').forEach(img => {
                const src = img.src;
                const w = img.naturalWidth || img.width;
                const h = img.naturalHeight || img.height;
                const skip = ['favicon', 'icon', 'avatar', 'logo', '.svg', '.gif'];
                if (w > 50 && h > 50 && !skip.some(k => src.toLowerCase().includes(k))) {
                    results.push({ type: 'url', data: src, width: w, height: h });
                }
            });

            document.querySelectorAll('a[href]').forEach(a => {
                const href = a.href || '';
                if (href.match(/\\.(png|jpg|jpeg|webp)(\\?|$)/i) && href.startsWith('http')) {
                    results.push({ type: 'url', data: href, width: 0, height: 0 });
                }
            });

            return results;
        }
    """)


def send_image_request_via_browser(prompt):
    print(f"[*] Image prompt: {prompt[:80]}")

    # Mark as processing in Redis
    redis_set(f"result:{REQUEST_ID}", {"status": "processing"}, 600)

    print("[*] Launching CloakBrowser (direct, no proxy)...")
    result = _try_image_request(prompt)
    if result and result.get("status") == "done":
        redis_set(f"result:{REQUEST_ID}", result, 600)
    return result


def _try_image_request(prompt):
    # Track network image responses
    captured_images = []
    pre_existing_urls = set()

    browser = launch(
        headless=True,
    )
    context = browser.new_context(
        viewport={"width": 1280, "height": 1024},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    )
    page = context.new_page()

    def on_response(response):
        try:
            ct = response.headers.get("content-type", "")
            url = response.url
            if "image" in ct and "gif" not in ct and response.status == 200:
                skip = ["favicon", "icon", "avatar", "logo", ".svg", "data:"]
                if not any(k in url.lower() for k in skip):
                    print(f"[*] Captured image response: {url[:100]}")
                    try:
                        body = response.body()
                        if len(body) > 1000:
                            captured_images.append({
                                "url": url,
                                "content_type": ct,
                                "body": body,
                            })
                    except Exception:
                        pass
        except Exception:
            pass

    page.on("response", on_response)

    try:
        # Navigate to duck.ai
        print("[*] Navigating to duck.ai...")
        page.goto(DDG_URL, wait_until="networkidle", timeout=30000)
        time.sleep(3)

        # Capture pre-existing images
        pre_imgs = extract_images_from_page(page)
        for img in pre_imgs:
            if img.get("type") == "url":
                pre_existing_urls.add(img["data"])

        # Dismiss overlays
        dismiss_overlays(page)

        # ── Step 1: Click on Image/Imagine mode in sidebar ──
        print("[*] Looking for Image/Imagine mode button...")
        clicked_image_mode = click_any(page, [
            'button:has-text("Imagine")',
            'button:has-text("Image")',
            '[data-testid*="imagine" i]',
            '[data-testid*="image" i]',
            'a:has-text("Imagine")',
            'a:has-text("Image")',
            'div[role="button"]:has-text("Imagine")',
            'div[role="button"]:has-text("Image")',
            # Sidebar navigation items
            'nav button:has-text("Imagine")',
            'nav a:has-text("Imagine")',
            'nav button:has-text("Image")',
            'nav a:has-text("Image")',
            # Try generic sidebar icons/buttons
            'aside button:has-text("Imagine")',
            'aside a:has-text("Imagine")',
        ])

        if not clicked_image_mode:
            # Try clicking on the sidebar menu items by position
            print("[*] Trying sidebar icon clicks...")
            sidebar_items = page.locator('nav button, nav a, aside button, aside a, [class*="sidebar"] button, [class*="sidebar"] a')
            count = sidebar_items.count()
            print(f"[*] Found {count} sidebar items")
            for i in range(count):
                try:
                    item = sidebar_items.nth(i)
                    text = item.inner_text().strip().lower()
                    print(f"[*] Sidebar item {i}: '{text}'")
                    if "imag" in text or "image" in text or "draw" in text or "generat" in text:
                        print(f"[+] Clicking sidebar item: '{text}'")
                        item.click()
                        clicked_image_mode = True
                        time.sleep(2)
                        break
                except Exception:
                    pass

        if not clicked_image_mode:
            print("[!] Could not find Image mode button, proceeding with default chat mode")

        time.sleep(2)

        # ── Step 2: Find input and type prompt ──
        chat_input = find_chat_input(page)
        if not chat_input:
            browser.close()
            return {"error": "Could not find chat input"}

        print(f"[*] Typing prompt...")
        chat_input.click()
        time.sleep(0.5)
        chat_input.fill(prompt)
        time.sleep(1)

        # ── Step 3: Submit ──
        print("[*] Submitting...")
        submitted = click_any(page, [
            'button[aria-label*="Send" i]',
            'button[aria-label*="Ask" i]',
            'button[aria-label*="Submit" i]',
            'button[data-testid*="send" i]',
            'button[data-testid*="submit" i]',
            'button[type="submit"]',
            'button:has(svg[aria-hidden="true"])',
        ])
        if not submitted:
            page.keyboard.press("Enter")
            time.sleep(0.5)
            try:
                val = chat_input.input_value()
                if val and len(val) > 10:
                    print("[*] Enter didn't work, trying again...")
                    chat_input.press("Enter")
                    time.sleep(0.5)
            except Exception:
                pass

        # ── Step 4: Wait for image response ──
        print("[*] Waiting up to 90s for image response...")
        got_image = False
        stable_count = 0
        last_image_count = 0
        last_text = ""

        for tick in range(90):
            time.sleep(2)

            # Check network captured images
            new_captured = [img for img in captured_images if img["url"] not in pre_existing_urls]
            if new_captured and not got_image:
                print(f"[+] {len(new_captured)} new network images captured!")
                got_image = True

            # Check DOM images
            dom_images = extract_images_from_page(page)
            new_dom = [img for img in dom_images if img.get("type") != "url" or img["data"] not in pre_existing_urls]
            current_count = len(new_captured) + len(new_dom)

            # Check text response
            try:
                texts = page.locator('[data-testid*="message"], [class*="message-content"], .prose, article, [class*="markdown"]').all_inner_texts()
                current_text = " ".join(texts).strip()
            except Exception:
                current_text = ""

            # Check for completion
            if current_count > 0:
                if current_count == last_image_count and current_text == last_text:
                    stable_count += 1
                else:
                    stable_count = 0
                    last_image_count = current_count
                    last_text = current_text
                    got_image = True

                if stable_count >= 5:
                    print(f"[+] Response stabilized with {current_count} image(s)")
                    break
            elif current_text and current_text != last_text:
                if len(current_text) > 50 and current_text == last_text:
                    stable_count += 1
                    if stable_count >= 8:
                        print("[+] Text response stabilized")
                        break
                else:
                    stable_count = 0
                    last_text = current_text

            if tick % 10 == 0 and tick > 0:
                print(f"[*] Still waiting... ({tick * 2}s, images: {current_count})")

        # ── Step 5: Collect and upload images ──
        tmp_urls = []

        # Upload network-intercepted images (skip GIF — loading animation)
        new_captured = [img for img in captured_images if img["url"] not in pre_existing_urls]
        if new_captured:
            print(f"[*] Processing {len(new_captured)} network image(s)...")
            for idx, img in enumerate(new_captured):
                ct = img["content_type"]
                ext = "png"
                if "jpeg" in ct or "jpg" in ct: ext = "jpg"
                elif "webp" in ct: ext = "webp"
                if is_gif(f"ddg_image_{idx}.{ext}", img["body"]):
                    print(f"[*] Skipping GIF (loading animation): {img['url'][:80]}")
                    continue
                url = upload_to_tmpfiles(img["body"], f"ddg_image_{idx}.{ext}")
                if url:
                    tmp_urls.append(url)

        # Upload DOM images (skip already uploaded URLs)
        uploaded_urls = set()
        new_dom = [img for img in dom_images if img.get("type") != "url" or img["data"] not in pre_existing_urls]

        for idx, img in enumerate(new_dom):
            img_data = img.get("data", "")
            img_type = img.get("type", "")
            try:
                if img_type == "blob_bytes" and isinstance(img_data, list):
                    img_bytes = bytes(img_data)
                    if is_gif(f"ddg_image_{idx}.png", img_bytes):
                        print(f"[*] Skipping GIF blob (loading animation)")
                        continue
                    url = upload_to_tmpfiles(img_bytes, f"ddg_image_{idx}.png")
                    if url:
                        tmp_urls.append(url)
                elif isinstance(img_data, str) and img_data.startswith("data:image/"):
                    header, b64 = img_data.split(",", 1)
                    ext = "png" if "png" in header else "jpg"
                    img_bytes = base64.b64decode(b64)
                    if is_gif(f"ddg_image_{idx}.{ext}", img_bytes):
                        print(f"[*] Skipping GIF data URL (loading animation)")
                        continue
                    url = upload_to_tmpfiles(img_bytes, f"ddg_image_{idx}.{ext}")
                    if url:
                        tmp_urls.append(url)
                elif img_type == "url" and isinstance(img_data, str) and img_data.startswith("http"):
                    if img_data not in uploaded_urls and img_data not in pre_existing_urls:
                        uploaded_urls.add(img_data)
                        print(f"[*] Downloading: {img_data[:100]}")
                        dl = requests.get(img_data, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
                        if dl.status_code == 200 and len(dl.content) > 1000:
                            if is_gif(img_data.split("/")[-1], dl.content):
                                print(f"[*] Skipping external GIF: {img_data[:80]}")
                                continue
                            ct = dl.headers.get("content-type", "")
                            ext = "png"
                            if "jpeg" in ct or "jpg" in ct: ext = "jpg"
                            elif "webp" in ct: ext = "webp"
                            url = upload_to_tmpfiles(dl.content, f"ddg_image_{idx}.{ext}")
                            if url:
                                tmp_urls.append(url)
            except Exception as e:
                print(f"[!] Failed to process image {idx}: {e}")

        # If no images found, take screenshot as fallback
        if not tmp_urls:
            print("[!] No images found, taking screenshot...")
            screenshot = page.screenshot(full_page=True)
            url = upload_to_tmpfiles(screenshot, "ddg_image_screenshot.png")
            if url:
                tmp_urls.append(url)
                result_data = {
                    "status": "done",
                    "model": "gpt-5-mini",
                    "images": tmp_urls,
                    "type": "screenshot",
                    "note": "duck.ai did not generate an image. This is a screenshot of the conversation.",
                }
            else:
                result_data = {
                    "status": "error",
                    "error": "No images captured and screenshot upload failed",
                }
        else:
            result_data = {
                "status": "done",
                "model": "gpt-5-mini",
                "images": tmp_urls,
                "type": "image",
            }

        redis_set(f"result:{REQUEST_ID}", result_data, 600)
        print(f"[+] Done! Result: {json.dumps(result_data, indent=2)}")
        return result_data

    except Exception as e:
        print(f"[!] Error: {e}")
        return {"status": "error", "error": str(e)}
    finally:
        try:
            browser.close()
        except Exception:
            pass


if __name__ == "__main__":
    print(f"[*] DDG Image Gen — Request: {REQUEST_ID}")
    print(f"[*] Prompt: {PROMPT}")
    result = send_image_request_via_browser(PROMPT)
    print(f"[*] Final result: {json.dumps(result)}")
