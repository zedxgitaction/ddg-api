"""
CloakBrowser proxy: multi-turn image editing via duck.ai.
Downloads image from URL, attaches to duck.ai chat, sends edit prompt.
If duck.ai responds with text (follow-up question), stores as needs_reply in Redis.
Script stays alive, polls for user reply, sends it to same duck.ai session.
Extracts base64 images, uploads to tmpfiles.org.
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
        # Fallback: check if content looks like image despite wrong content-type
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


def craft_edit_prompt(prompt):
    """Craft a direct edit prompt with CRITICAL suffix to avoid follow-up questions."""
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
    """Try to upload image via file input or drag-and-drop."""
    # Detect actual format from magic bytes
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

    # Method 3: Inject file input and trigger
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


def get_assistant_text(page):
    """Extract the last assistant message text from duck.ai page."""
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
    """Find and return the chat input element."""
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

    # Fallback
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


def send_text_to_input(page, msg):
    """Type text into the chat input and press Enter (with button fallback)."""
    chat_input = find_chat_input(page)
    if not chat_input:
        print("[!] Could not find chat input")
        return False

    try:
        chat_input.click()
        time.sleep(0.3)
        chat_input.fill(msg)
        time.sleep(0.5)

        # Try clicking submit button first
        submitted = click_any(page, [
            'button[aria-label*="Send" i]',
            'button[aria-label*="Ask" i]',
            'button[aria-label*="Submit" i]',
            'button[data-testid*="send" i]',
            'button[data-testid*="submit" i]',
            'button[type="submit"]',
        ])
        if not submitted:
            chat_input.press("Enter")
            time.sleep(0.5)
            try:
                val = chat_input.input_value()
                if val and len(val) > 10:
                    print("[*] Enter didn't work, trying again...")
                    chat_input.press("Enter")
                    time.sleep(0.5)
            except Exception:
                pass

        print(f"[+] Sent message: {msg[:80]}")
        return True
    except Exception as e:
        print(f"[!] Failed to send message: {e}")
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


def upload_captured_images(captured_images, pre_existing_urls):
    """Upload network-intercepted images to tmpfiles.org. Returns list of URLs."""
    tmp_urls = []
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
    return tmp_urls


def upload_dom_images(images, pre_existing_urls):
    """Upload DOM-extracted images to tmpfiles.org. Returns list of URLs."""
    tmp_urls = []
    new_images = [img for img in images
                  if img.get("type") != "url" or img["data"] not in pre_existing_urls]

    for idx, img in enumerate(new_images):
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


def poll_for_reply(request_id, timeout=120, poll_interval=3):
    """Poll Redis for user reply. Returns reply message or None."""
    print(f"[*] Polling for user reply (up to {timeout}s)...")
    for i in range(int(timeout / poll_interval)):
        time.sleep(poll_interval)
        reply_data = redis_get(f"reply:{request_id}")
        if reply_data and reply_data.get("message"):
            print(f"[+] Got user reply: {reply_data['message'][:80]}")
            return reply_data["message"]
        elapsed = (i + 1) * poll_interval
        if elapsed % 15 == 0:
            print(f"[*] Still waiting for reply... ({elapsed}s)")

    print(f"[*] No reply received after {timeout}s")
    return None


def send_edit_via_browser(image_url, edit_prompt):
    proxy_url = get_random_proxy()
    print(f"[*] Proxy: {proxy_url[:40]}...")
    print(f"[*] Image URL: {image_url[:100]}")
    print(f"[*] Edit prompt: {edit_prompt[:80]}")

    # Download the image first
    image_bytes = download_image(image_url)
    if not image_bytes:
        return {"error": "Failed to download image from URL"}

    print("[*] Launching CloakBrowser...")
    browser = launch(headless=True)
    page = browser.new_page()

    # Network-level image capture
    captured_images = []
    pre_existing_urls = set()

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

    dismiss_overlays(page)

    # Snapshot existing images before uploading
    pre_images = extract_images_from_page(page)
    for img in pre_images:
        if img.get("type") == "url":
            pre_existing_urls.add(img["data"])

    # Upload the image to duck.ai
    print("[*] Uploading image to duck.ai...")
    uploaded = upload_image_to_page(page, image_bytes)

    if not uploaded:
        # Fallback: send image URL in the prompt text
        print("[!] Direct upload failed, sending URL in prompt text")
        crafted = craft_edit_prompt(edit_prompt)
        final_message = f"{crafted}\n\nImage URL: {image_url}"
    else:
        print("[+] Image uploaded successfully")
        crafted = craft_edit_prompt(edit_prompt)
        final_message = crafted

    # Send the edit prompt
    chat_input = find_chat_input(page)
    if not chat_input:
        browser.close()
        return {"error": "Could not find chat input"}

    chat_input.click()
    time.sleep(0.5)
    chat_input.fill(final_message)
    time.sleep(1)
    print("[*] Submitting edit prompt...")

    # Try clicking the submit/ask button first
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
        chat_input.press("Enter")
        time.sleep(0.5)
        try:
            val = chat_input.input_value()
            if val and len(val) > 10:
                print("[*] Enter didn't work, trying again...")
                chat_input.press("Enter")
                time.sleep(0.5)
        except Exception:
            pass

    # Wait for response
    wait_time = 90
    print(f"[*] Waiting up to {wait_time}s for edited image...")

    last_image_count = 0
    stable_count = 0
    got_images = False
    got_text = False
    last_text = ""
    final_dom_images = []

    for i in range(int(wait_time / 2.5)):
        time.sleep(2.5)
        elapsed = (i + 1) * 2.5

        dom_images = extract_images_from_page(page)
        new_dom = [img for img in dom_images
                   if img.get("type") != "url" or img["data"] not in pre_existing_urls]
        new_captured = [img for img in captured_images if img["url"] not in pre_existing_urls]
        total_new = len(new_dom) + len(new_captured)

        if total_new > 0:
            got_images = True
            final_dom_images = dom_images
            if total_new == last_image_count:
                stable_count += 1
                if stable_count >= 4:
                    print(f"[+] Images stable at {elapsed}s: {total_new} new images")
                    break
            else:
                stable_count = 0
                last_image_count = total_new
                if i % 2 == 0:
                    print(f"[*] Growing: {total_new} new images")
        else:
            # Check for text response (follow-up question)
            text = get_assistant_text(page)
            if text and text != last_text:
                last_text = text
                got_text = True
                stable_count = 0
                print(f"[*] Got text response ({len(text)} chars)")
            elif text and text == last_text:
                stable_count += 1
                if stable_count >= 4:
                    print(f"[+] Text stable at {elapsed}s")
                    break

            if elapsed >= wait_time:
                print(f"[*] Timeout after {wait_time}s")
                break

    # --- Build result ---
    result = {"status": "success", "type": "image", "proxy": proxy_url[:40]}

    if got_images:
        # Upload images
        tmp_urls = upload_captured_images(captured_images, pre_existing_urls)
        if not tmp_urls and final_dom_images:
            tmp_urls = upload_dom_images(final_dom_images, pre_existing_urls)
        if tmp_urls:
            result["images"] = tmp_urls
            browser.close()
            return result
        else:
            result["error"] = "Images found but upload failed"
            browser.close()
            return result

    if got_text:
        # duck.ai sent text instead of images -- needs user reply
        clean = clean_text(last_text)
        if clean:
            browser.close()

            # Store needs_reply
            needs_reply = {"status": "needs_reply", "text": clean, "request_id": REQUEST_ID}
            redis_set(f"chat:{REQUEST_ID}", needs_reply, ttl=300)
            print(f"[+] Stored needs_reply: {clean[:100]}")

            # Wait for user reply
            reply = poll_for_reply(REQUEST_ID, timeout=120)
            if reply:
                return handle_reply(REQUEST_ID, reply, proxy_url, image_url)
            else:
                return {"status": "timeout", "error": "No reply received within 120s", "text": clean}

    # Fallback: take screenshot of the page
    print("[!] No images/text found — taking screenshot as fallback")
    try:
        screenshot_bytes = page.screenshot(type="png", full_page=True)
        if screenshot_bytes and len(screenshot_bytes) > 5000:
            url = upload_to_tmpfiles(screenshot_bytes, "ddg_edit_screenshot.png")
            if url:
                result["images"] = [url]
                result["type"] = "screenshot"
                result["note"] = "duck.ai did not generate an edited image. This is a screenshot of the conversation."
                browser.close()
                return result
    except Exception as e:
        print(f"[!] Screenshot failed: {e}")

    browser.close()
    result["error"] = "No edited images generated"
    return result


def handle_reply(request_id, reply_message, proxy_url, original_image_url):
    """Handle a user reply by launching a new browser session."""
    print(f"[*] Handling reply: {reply_message[:80]}")
    redis_set(f"chat:{request_id}", {"status": "processing_reply"}, ttl=300)

    # Download the image again for the new session
    image_bytes = download_image(original_image_url)
    if not image_bytes:
        return {"error": "Failed to re-download image for reply session"}

    browser = launch(headless=True)
    page = browser.new_page()

    captured_images = []
    pre_existing_urls = set()

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

    print("[*] Navigating to duck.ai for reply...")
    page.goto(DDG_URL, wait_until="domcontentloaded", timeout=60000)
    time.sleep(5)
    dismiss_overlays(page)

    # Track pre-existing images
    pre_images = extract_images_from_page(page)
    for img in pre_images:
        if img.get("type") == "url":
            pre_existing_urls.add(img["data"])

    # Upload image again
    print("[*] Re-uploading image for reply session...")
    uploaded = upload_image_to_page(page, image_bytes)

    # Send the reply message
    if not send_text_to_input(page, reply_message):
        browser.close()
        return {"error": "Failed to send reply to duck.ai"}

    # Wait for images
    timeout = 90
    print(f"[*] Waiting up to {timeout}s for images after reply...")

    last_count = 0
    stable = 0
    got_images = False
    final_dom_images = []

    for i in range(int(timeout / 2.5)):
        time.sleep(2.5)
        elapsed = (i + 1) * 2.5

        dom_images = extract_images_from_page(page)
        new_dom = [img for img in dom_images
                   if img.get("type") != "url" or img["data"] not in pre_existing_urls]
        new_captured = [img for img in captured_images if img["url"] not in pre_existing_urls]
        total = len(new_dom) + len(new_captured)

        if total > 0:
            got_images = True
            final_dom_images = dom_images
            if total == last_count:
                stable += 1
                if stable >= 4:
                    print(f"[+] Images stable at {elapsed}s: {total} images")
                    break
            else:
                stable = 0
                last_count = total
                if i % 2 == 0:
                    print(f"[*] Growing: {total} images")
        else:
            if elapsed >= timeout:
                print(f"[*] No images after {timeout}s")
                break

    result = {"status": "success", "type": "image", "proxy": proxy_url[:40]}

    if got_images:
        tmp_urls = upload_captured_images(captured_images, pre_existing_urls)
        if not tmp_urls and final_dom_images:
            tmp_urls = upload_dom_images(final_dom_images, pre_existing_urls)
        if tmp_urls:
            result["images"] = tmp_urls
            browser.close()
            return result
        else:
            result["error"] = "Images found but upload failed"
    else:
        # Fallback: take screenshot
        print("[!] No images after reply — taking screenshot as fallback")
        try:
            screenshot_bytes = page.screenshot(type="png", full_page=True)
            if screenshot_bytes and len(screenshot_bytes) > 5000:
                url = upload_to_tmpfiles(screenshot_bytes, "ddg_edit_screenshot.png")
                if url:
                    result["images"] = [url]
                    result["type"] = "screenshot"
                    result["note"] = "duck.ai did not generate an edited image after reply. This is a screenshot of the conversation."
                    browser.close()
                    return result
        except Exception as e:
            print(f"[!] Screenshot failed: {e}")
        result["error"] = "No images generated after reply"

    browser.close()
    return result


def main():
    redis_set(f"chat:{REQUEST_ID}", {"status": "processing"}, ttl=300)

    if not IMAGE_URL:
        result = {"error": "IMAGE_URL is required", "status": "error"}
    else:
        result = send_edit_via_browser(IMAGE_URL, EDIT_PROMPT)

    if result.get("status") not in ("needs_reply", "timeout"):
        result["status"] = "done" if result.get("status") == "success" else "error"

    redis_set(f"chat:{REQUEST_ID}", result, ttl=300)
    print(f"[+] Stored result for request {REQUEST_ID}")
    print(f"[*] Result: {json.dumps(result)[:500]}")


if __name__ == "__main__":
    main()
