"""
CloakBrowser proxy for image editing via duck.ai.
Multi-turn: sends edit prompt, waits for response. If duck.ai responds with text
(confirmation/question), stores as needs_reply in Redis and polls for user reply.
When reply arrives, sends it to same session and captures the final image.
Base64 images are uploaded to tmpfiles.org.
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

IMAGE_URL = os.environ.get("IMAGE_URL", "")
EDIT_PROMPT = os.environ.get("EDIT_PROMPT", "edit this image")
REQUEST_ID = os.environ.get("REQUEST_ID", "unknown")

PROXIES = [
    "http://purevpn0s8946341:8RXxgcU2MBumt8@px043005.pointtoserver.com:10780",
    "http://purevpn0s12153504:1LTpwxbCJbEdXo@px043005.pointtoserver.com:10780",
    "http://purevpn0s8946341:8RXxgcU2MBumt8@px031901.pointtoserver.com:10780",
    "http://1351:IBd1Fk5CuUNZ@p101.squidproxies.com:9088",
    "http://llewellynashleybowen:rNXaRJfNPN233zw@136.179.19.164:3128",
]


def get_random_proxy():
    return random.choice(PROXIES)


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


def upload_to_tmpfiles(image_bytes, filename="image.png"):
    """Upload to tmpfiles.org with retry, fallback to file.io."""
    for attempt in range(3):
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
                print(f"[+] Uploaded to tmpfiles: {direct}")
                return direct
            print(f"[!] tmpfiles response (attempt {attempt+1}): {data}")
        except Exception as e:
            print(f"[!] tmpfiles upload failed (attempt {attempt+1}): {e}")
        if attempt < 2:
            time.sleep(2)

    # Fallback: file.io
    try:
        print("[*] Falling back to file.io...")
        r = requests.post(
            "https://file.io",
            files={"file": (filename, image_bytes, "image/png")},
            timeout=30,
        )
        data = r.json()
        if data.get("link"):
            print(f"[+] Uploaded to file.io: {data['link']}")
            return data["link"]
        print(f"[!] file.io response: {data}")
    except Exception as e:
        print(f"[!] file.io upload failed: {e}")

    return None


def fix_tmpfiles_url(url):
    """Convert tmpfiles.org page URL to direct download URL."""
    if "tmpfiles.org/" in url and "/dl/" not in url:
        url = url.replace("tmpfiles.org/", "tmpfiles.org/dl/")
        print(f"[*] Fixed tmpfiles URL to: {url}")
    return url


def download_image(url):
    """Download image from URL, return bytes."""
    url = fix_tmpfiles_url(url)
    try:
        print(f"[*] Downloading image: {url[:100]}")
        r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        ct = r.headers.get("content-type", "")
        if r.status_code == 200 and len(r.content) > 1000 and "image" in ct:
            print(f"[+] Downloaded: {len(r.content)} bytes, type={ct}")
            return r.content
        if r.status_code == 200 and len(r.content) > 5000:
            header = r.content[:8]
            if header[:4] == b'\xff\xd8\xff\xe0' or header[:4] == b'\xff\xd8\xff\xe1' or header[:8] == b'\x89PNG\r\n\x1a\n':
                print(f"[+] Downloaded (content-type wrong but data is image): {len(r.content)} bytes")
                return r.content
        print(f"[!] Download failed: status={r.status_code}, size={len(r.content)}, ct={ct}")
        return None
    except Exception as e:
        print(f"[!] Download error: {e}")
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
                const skip = ['favicon', 'icon', 'avatar', 'logo', '.svg'];
                if (w > 50 && h > 50 && !skip.some(k => src.toLowerCase().includes(k))) {
                    results.push({ type: 'url', data: src, width: w, height: h });
                }
            });

            document.querySelectorAll('a[href]').forEach(a => {
                const href = a.href;
                if (/\\.(png|jpg|jpeg|webp|gif)(\\?|$)/i.test(href)) {
                    results.push({ type: 'url', data: href, width: 0, height: 0 });
                }
            });

            return results;
        }
    """)


def get_last_response_text(page):
    """Extract the last assistant message text from the page."""
    try:
        return page.evaluate("""
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
    except Exception:
        return ""


def clean_text(text):
    """Remove noise from duck.ai text responses."""
    for noise in ["GPT-5 mini", "Fast", "Tools", "Hide Reasoning",
                  "Related Searches", "All chats are private", "AI can make mistakes",
                  "Reasoning"]:
        text = text.replace(noise, "").strip()
    return text.strip('\n').strip()


def find_chat_input(page):
    """Find the chat input textarea on the page."""
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
                print(f"[+] Found input: {sel}")
                return el.first
        except Exception:
            pass

    try:
        count = page.evaluate("() => document.querySelectorAll('textarea').length")
        for i in range(count):
            ta = page.locator('textarea').nth(i)
            if ta.is_visible():
                print(f"[+] Using textarea #{i}")
                return ta
    except Exception:
        pass

    return None


def send_message(page, message):
    """Type and send a message in the chat input."""
    chat_input = find_chat_input(page)
    if not chat_input:
        return False
    chat_input.click()
    time.sleep(0.5)
    chat_input.fill(message)
    time.sleep(1)
    page.keyboard.press("Enter")
    print(f"[+] Sent message: {message[:80]}")
    return True


def craft_edit_prompt(prompt):
    """Craft a direct edit prompt with CRITICAL suffix to reduce follow-up questions."""
    prompt = prompt.strip()

    CRITICAL_SUFFIX = (
        " CRITICAL API CONSTRAINT: Do not reply with text. Do not ask for clarification, "
        "preferences, style, or color choices. If any details are unspecified, make a creative "
        "decision yourself and execute the image edit immediately. Your entire response must "
        "only be the final edited image."
    )

    direct_starters = [
        "edit", "change", "modify", "transform", "convert", "turn",
        "make it", "add", "remove", "replace", "apply", "enhance",
        "make the", "change the", "add a", "remove the",
    ]
    if any(prompt.lower().startswith(s) for s in direct_starters):
        return f"{prompt}.{CRITICAL_SUFFIX}"

    return f"Edit this image: {prompt}.{CRITICAL_SUFFIX}"


def upload_image_to_page(page, image_bytes):
    """Try to upload image via file input or attachment button."""
    ext = "png"
    content_type = "image/png"
    header = image_bytes[:8]
    if header[:4] == b'\xff\xd8\xff\xe0' or header[:4] == b'\xff\xd8\xff\xe1':
        ext = "jpg"
        content_type = "image/jpeg"
    elif header[:8] == b'\x89PNG\r\n\x1a\n':
        ext = "png"
        content_type = "image/png"
    elif header[:4] == b'RIFF' and image_bytes[8:12] == b'WEBP':
        ext = "webp"
        content_type = "image/webp"
    elif header[:4] == b'GIF8':
        ext = "gif"
        content_type = "image/gif"

    tmp_path = f"/tmp/ddg_edit_input.{ext}"
    with open(tmp_path, "wb") as f:
        f.write(image_bytes)
    print(f"[*] Saved input image: {tmp_path} ({len(image_bytes)} bytes, {ext})")

    # Method 1: Try file input
    try:
        file_inputs = page.locator('input[type="file"]')
        if file_inputs.count() > 0:
            print("[+] Found file input, uploading...")
            file_inputs.first.set_input_files(tmp_path)
            time.sleep(3)
            return True
    except Exception as e:
        print(f"[*] File input method failed: {e}")

    # Method 2: Try attachment button -> file input
    try:
        attach_selectors = [
            'button[aria-label*="attach" i]',
            'button[aria-label*="upload" i]',
            'button[aria-label*="image" i]',
            'button[aria-label*="file" i]',
            'button[data-testid*="attach" i]',
            'button[data-testid*="upload" i]',
            '[class*="attach" i]',
            '[class*="upload" i]',
            'button:has(svg[class*="attach" i])',
        ]
        for sel in attach_selectors:
            btn = page.locator(sel)
            if btn.count() > 0 and btn.first.is_visible():
                print(f"[+] Clicking attachment: {sel}")
                btn.first.click()
                time.sleep(2)
                file_inputs = page.locator('input[type="file"]')
                if file_inputs.count() > 0:
                    print("[+] File input appeared after attachment click")
                    file_inputs.first.set_input_files(tmp_path)
                    time.sleep(3)
                    return True
    except Exception as e:
        print(f"[*] Attachment button method failed: {e}")

    # Method 3: Inject file input
    try:
        print("[*] Trying injected file input...")
        page.evaluate("""
            () => {
                const input = document.createElement('input');
                input.type = 'file';
                input.id = '__ddg_upload';
                input.accept = 'image/*';
                input.style.display = 'none';
                document.body.appendChild(input);
            }
        """)
        injected = page.locator('#__ddg_upload')
        if injected.count() > 0:
            injected.first.set_input_files(tmp_path)
            time.sleep(1)
            page.evaluate("""
                () => {
                    const input = document.getElementById('__ddg_upload');
                    if (input) {
                        input.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                }
            """)
            time.sleep(3)
            return True
    except Exception as e:
        print(f"[*] Injected input method failed: {e}")

    return False


def process_images(captured_images, extracted_images, pre_existing_urls):
    """Process captured and extracted images, upload to tmpfiles.org."""
    tmp_urls = []

    # PRIORITY 1: Network-intercepted images (new only)
    new_captured = [img for img in captured_images if img["url"] not in pre_existing_urls]
    if new_captured:
        print(f"[*] Processing {len(new_captured)} network-intercepted image(s)...")
        for idx, img in enumerate(new_captured):
            ct = img["content_type"]
            ext = "png"
            if "jpeg" in ct or "jpg" in ct: ext = "jpg"
            elif "webp" in ct: ext = "webp"
            elif "gif" in ct: ext = "gif"
            url = upload_to_tmpfiles(img["body"], f"ddg_edit_{idx}.{ext}")
            if url:
                tmp_urls.append(url)

    # PRIORITY 2: DOM-extracted new images
    if not tmp_urls:
        new_dom = [img for img in extracted_images
                   if img.get("type") != "url" or img["data"] not in pre_existing_urls]
        if new_dom:
            print(f"[*] Processing {len(new_dom)} DOM image(s)...")
            for idx, img in enumerate(new_dom):
                img_data = img.get("data", "")
                img_type = img.get("type", "")
                try:
                    if img_type == "blob_bytes" and isinstance(img_data, list):
                        img_bytes = bytes(img_data)
                        url = upload_to_tmpfiles(img_bytes, f"ddg_edit_{idx}.png")
                        if url:
                            tmp_urls.append(url)
                    elif isinstance(img_data, str) and img_data.startswith("data:image/"):
                        header, b64 = img_data.split(",", 1)
                        ext = "png" if "png" in header else "jpg"
                        img_bytes = base64.b64decode(b64)
                        url = upload_to_tmpfiles(img_bytes, f"ddg_edit_{idx}.{ext}")
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
                            url = upload_to_tmpfiles(dl.content, f"ddg_edit_{idx}.{ext}")
                            if url:
                                tmp_urls.append(url)
                except Exception as e:
                    print(f"[!] Failed to process image {idx}: {e}")

    return tmp_urls


def wait_for_images(page, captured_images, pre_existing_urls, timeout=90):
    """Wait for images to appear and stabilize. Returns (images, text_response)."""
    last_count = 0
    stable = 0
    text_response = ""

    for i in range(int(timeout / 2.5)):
        time.sleep(2.5)

        dom_images = extract_images_from_page(page)
        new_dom = [img for img in dom_images
                   if img.get("type") != "url" or img["data"] not in pre_existing_urls]
        new_cap = [img for img in captured_images if img["url"] not in pre_existing_urls]
        total = len(new_dom) + len(new_cap)

        if total > 0:
            if total == last_count:
                stable += 1
                if stable >= 4:
                    print(f"[+] Images stable at {(i+1)*2.5}s: {total} images")
                    return dom_images, ""
            else:
                stable = 0
                last_count = total
                if i % 2 == 0:
                    print(f"[*] Growing: {total} images")
        else:
            elapsed = (i + 1) * 2.5
            if elapsed >= 15:
                text = get_last_response_text(page)
                if text and len(text) > 10:
                    print(f"[*] Got text response ({len(text)} chars)")
                    text_response = text
                    return [], text_response

            if elapsed >= timeout:
                print(f"[*] Timeout after {timeout}s")
                break

    return extract_images_from_page(page), text_response


def poll_for_reply(request_id, timeout=120):
    """Poll Redis for user reply. Returns reply message or None."""
    print(f"[*] Polling for reply (up to {timeout}s)...")
    redis_set(f"chat:{REQUEST_ID}", {"status": "needs_reply", "request_id": request_id}, ttl=300)

    for i in range(int(timeout / 3)):
        time.sleep(3)
        data = redis_get(f"reply:{request_id}")
        if data and data.get("message"):
            print(f"[+] Got reply: {data['message'][:80]}")
            return data["message"]
        elapsed = (i + 1) * 3
        if elapsed % 30 == 0:
            print(f"[*] Still waiting for reply... ({elapsed}s)")

    print("[*] Reply timeout")
    return None


def send_edit_via_browser(image_url, edit_prompt):
    proxy_url = get_random_proxy()
    print(f"[*] Proxy: {proxy_url[:40]}...")
    print(f"[*] Image URL: {image_url[:100]}")
    print(f"[*] Edit prompt: {edit_prompt[:80]}")

    # Download the image first
    image_bytes = download_image(image_url)
    if not image_bytes:
        return {"error": "Failed to download image from URL", "status": "error"}

    print("[*] Launching CloakBrowser...")
    browser = launch(headless=True)
    page = browser.new_page()

    # Network-level image capture
    captured_images = []
    pre_existing_image_urls = set()

    def on_response(response):
        try:
            ct = response.headers.get("content-type", "")
            url = response.url
            if ct.startswith("image/") and response.status == 200:
                cl = int(response.headers.get("content-length", "0"))
                if cl > 5000 or cl == 0:
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

    # Snapshot existing images before uploading
    pre_images = extract_images_from_page(page)
    for img in pre_images:
        if img.get("type") == "url":
            pre_existing_image_urls.add(img["data"])

    # Upload the image to duck.ai
    print("[*] Uploading image to duck.ai...")
    uploaded = upload_image_to_page(page, image_bytes)

    if not uploaded:
        print("[!] Direct upload failed, sending URL in prompt text")
        crafted = craft_edit_prompt(edit_prompt)
        final_message = f"{crafted}\n\nImage URL: {image_url}"
    else:
        print("[+] Image uploaded successfully")
        crafted = craft_edit_prompt(edit_prompt)
        final_message = crafted

    # Send message
    if not send_message(page, final_message):
        browser.close()
        return {"error": "Could not find chat input", "status": "error"}

    # Wait for response
    print(f"[*] Waiting up to 90s for edited image...")
    dom_images, text_response = wait_for_images(page, captured_images, pre_existing_image_urls, timeout=90)

    # If we got text (duck.ai asking a question), do multi-turn
    if text_response and not dom_images:
        print("[*] duck.ai responded with text — entering multi-turn mode")

        clean = clean_text(text_response)

        redis_set(f"chat:{REQUEST_ID}", {
            "status": "needs_reply",
            "text": clean,
            "request_id": REQUEST_ID,
        }, ttl=300)

        reply = poll_for_reply(REQUEST_ID, timeout=120)
        if not reply:
            browser.close()
            return {
                "status": "needs_reply",
                "text": clean,
                "request_id": REQUEST_ID,
                "error": "No reply received within 120s. Send a reply via /api/edit/reply.",
            }

        if not send_message(page, reply):
            browser.close()
            return {"error": "Could not send reply to duck.ai", "status": "error"}

        print(f"[*] Waiting up to 90s for images after reply...")
        dom_images, _ = wait_for_images(page, captured_images, pre_existing_image_urls, timeout=90)

    browser.close()

    # Process images
    tmp_urls = process_images(captured_images, dom_images, pre_existing_image_urls)

    result = {"status": "done", "type": "image", "proxy": proxy_url[:40]}

    if tmp_urls:
        result["images"] = tmp_urls
    elif text_response:
        clean = clean_text(text_response)
        if clean:
            result["response"] = clean
            result["type"] = "text"
    else:
        result["error"] = "No edited images generated"

    return result


def main():
    redis_set(f"chat:{REQUEST_ID}", {"status": "processing"}, ttl=300)

    if not IMAGE_URL:
        result = {"error": "IMAGE_URL is required", "status": "error"}
    else:
        result = send_edit_via_browser(IMAGE_URL, EDIT_PROMPT)

    result["status"] = "done" if result.get("status") == "done" else "error"
    redis_set(f"chat:{REQUEST_ID}", result, ttl=300)
    print(f"[+] Stored result for request {REQUEST_ID}")
    print(f"[*] Result: {json.dumps(result)[:500]}")


if __name__ == "__main__":
    main()
