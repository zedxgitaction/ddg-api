"""
CloakBrowser proxy for image editing via duck.ai.
Downloads image from URL, attaches to duck.ai chat, sends edit prompt.
Extracts only the edited image (no text), uploads to tmpfiles.org.
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


def upload_to_tmpfiles(image_bytes, filename="image.png"):
    """Upload to tmpfiles.org with retry, fallback to 0x0.st."""
    import time as _time

    # Try tmpfiles.org first (with retries)
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
            _time.sleep(2)

    # Fallback: 0x0.st
    try:
        print("[*] Falling back to 0x0.st...")
        ext = filename.rsplit(".", 1)[-1] if "." in filename else "png"
        mime = f"image/{'jpeg' if ext in ('jpg', 'jpeg') else ext}"
        r = requests.post(
            "https://0x0.st",
            files={"file": (filename, image_bytes, mime)},
            timeout=30,
        )
        if r.status_code == 200 and r.text.strip().startswith("http"):
            url = r.text.strip()
            print(f"[+] Uploaded to 0x0.st: {url}")
            return url
        print(f"[!] 0x0.st response: {r.status_code} {r.text[:200]}")
    except Exception as e:
        print(f"[!] 0x0.st upload failed: {e}")

    # Fallback 2: file.io
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


def download_image(url):
    """Download image from URL, return bytes."""
    try:
        print(f"[*] Downloading image: {url[:100]}")
        r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200 and len(r.content) > 1000:
            print(f"[+] Downloaded: {len(r.content)} bytes")
            return r.content
        print(f"[!] Download failed: status={r.status_code}, size={len(r.content)}")
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


def craft_edit_prompt(prompt):
    """
    Craft a direct edit prompt that avoids DDG follow-up questions.
    DDG tends to ask clarifying questions instead of editing. This forces
    a direct edit response.
    """
    prompt = prompt.strip()

    # Critical constraint suffix — forces duck.ai to edit immediately without questions
    CRITICAL_SUFFIX = (
        " CRITICAL API CONSTRAINT: Do not reply with text. Do not ask for clarification, "
        "preferences, style, or color choices. If any details are unspecified, make a creative "
        "decision yourself and execute the image edit immediately. Your entire response must "
        "only be the final edited image."
    )

    # If prompt already includes clear instructions, keep it
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
    # Save image to /tmp for upload
    tmp_path = "/tmp/ddg_edit_input.png"
    with open(tmp_path, "wb") as f:
        f.write(image_bytes)

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

    # Method 2: Try attachment button → file input
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

                # Now look for file input that appeared
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
            # Trigger change event
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
    # Track images we already saw (to detect NEW edited images)
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

    # Snapshot existing images before uploading (to detect new ones later)
    pre_images = extract_images_from_page(page)
    for img in pre_images:
        if img.get("type") == "url":
            pre_existing_image_urls.add(img["data"])

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

    # Type and submit
    print(f"[*] Typing: {final_message[:80]}")
    chat_input.click()
    time.sleep(0.5)
    chat_input.fill(final_message)
    time.sleep(1)
    print("[*] Submitting...")
    page.keyboard.press("Enter")

    # Wait for response - images only
    # If DDG responds with text (follow-up question), send a follow-up prompt
    wait_time = 120
    print(f"[*] Waiting up to {wait_time}s for edited image...")

    last_image_count = 0
    stable_count = 0
    followup_sent = False
    followup2_sent = False

    def get_last_response_text():
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

    def send_followup(msg):
        try:
            ci = None
            for sel in [
                'textarea#chat-input',
                'textarea[name="chat-input"]',
                'textarea[aria-label*="chat" i]',
                'textarea[placeholder*="Ask" i]',
                'div[role="textbox"][contenteditable="true"]',
            ]:
                el = page.locator(sel)
                if el.count() > 0 and el.first.is_visible():
                    ci = el.first
                    break
            if not ci:
                ci = page.locator('textarea').first
            if ci:
                ci.click()
                time.sleep(0.3)
                ci.fill(msg)
                time.sleep(0.5)
                page.keyboard.press("Enter")
                print(f"[+] Sent followup: {msg}")
                return True
        except Exception as e:
            print(f"[!] Followup failed: {e}")
        return False

    for i in range(int(wait_time / 2.5)):
        time.sleep(2.5)

        images = extract_images_from_page(page)
        new_images = [img for img in images
                      if img.get("type") != "url" or img["data"] not in pre_existing_image_urls]
        total_new = len(new_images) + len(captured_images)

        if total_new > 0:
            if total_new == last_image_count:
                stable_count += 1
                if stable_count >= 4:
                    print(f"[+] Images stable at {(i+1)*2.5}s: {total_new} new images")
                    break
            else:
                stable_count = 0
                last_image_count = total_new
                if i % 2 == 0:
                    print(f"[*] Growing: {total_new} new images")
        else:
            elapsed = (i + 1) * 2.5

            # After 20s with no images, check if DDG sent a text follow-up question
            if elapsed >= 20 and not followup_sent:
                text = get_last_response_text()
                if text and len(text) > 10:
                    print(f"[*] DDG responded with text ({len(text)} chars) — sending followup")
                    followup_sent = True
                    send_followup("Do not reply with text. Generate and show only the final edited image now. No questions.")
                    continue

            # After 50s with no images, try one more followup
            if elapsed >= 50 and followup_sent and not followup2_sent:
                text = get_last_response_text()
                if text and len(text) > 10:
                    print(f"[*] Still no images after followup, trying again")
                    followup2_sent = True
                    send_followup("CRITICAL: No text. Output only the edited image. Execute the edit now.")
                    continue

            if elapsed >= 90:
                print(f"[*] No new images after 90s, giving up")
                break

    # Final extraction
    final_images = extract_images_from_page(page)
    new_final = [img for img in final_images
                 if img.get("type") != "url" or img["data"] not in pre_existing_image_urls]

    # Screenshot for fallback
    screenshot_bytes = None
    try:
        page.screenshot(path="/tmp/ddg_edit_final.png", full_page=True)
        with open("/tmp/ddg_edit_final.png", "rb") as f:
            screenshot_bytes = f.read()
        print(f"[*] Screenshot: {len(screenshot_bytes)} bytes")
    except Exception as e:
        print(f"[!] Screenshot failed: {e}")
    browser.close()

    # Build result - images only, no text
    result = {"status": "success", "type": "image", "proxy": proxy_url[:40]}

    tmp_urls = []

    # PRIORITY 1: Network-intercepted images (new ones only)
    new_captured = [img for img in captured_images
                    if img["url"] not in pre_existing_image_urls]
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
    if not tmp_urls and new_final:
        print(f"[*] No network images, falling back to {len(new_final)} DOM image(s)...")
        for idx, img in enumerate(new_final):
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

    if tmp_urls:
        result["images"] = tmp_urls

    # Fallback: screenshot
    if not result.get("images") and screenshot_bytes and len(screenshot_bytes) > 5000:
        print("[*] No images found, uploading screenshot as fallback...")
        url = upload_to_tmpfiles(screenshot_bytes, "ddg_edit_screenshot.png")
        if url:
            result["images"] = [url]

    if not result.get("images"):
        result["error"] = "No edited images generated"

    return result


def main():
    redis_set(f"chat:{REQUEST_ID}", {"status": "processing"}, ttl=300)

    if not IMAGE_URL:
        result = {"error": "IMAGE_URL is required", "status": "error"}
    else:
        result = send_edit_via_browser(IMAGE_URL, EDIT_PROMPT)

    result["status"] = "done" if result.get("status") == "success" else "error"
    redis_set(f"chat:{REQUEST_ID}", result, ttl=300)
    print(f"[+] Stored result for request {REQUEST_ID}")
    print(f"[*] Result: {json.dumps(result)[:500]}")


if __name__ == "__main__":
    main()
