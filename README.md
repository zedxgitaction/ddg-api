# DDG API

Free AI chat, image generation, and image editing API powered by [duck.ai](https://duck.ai) via CloakBrowser on GitHub Actions.

**Live API:** https://ddg-api-iota.vercel.app

## How It Works

```
User sends message
     |
     v
POST /api/chat or /api/image  -->  Stores in Redis  -->  Triggers GH Actions
     |                                                         |
     v                                                         v
Returns request_id                                CloakBrowser opens duck.ai
                                                         |
                                                         v
                                              Types message, presses Submit
                                                         |
                                                         v
                                              Intercepts duck.ai response
                                                         |
                                                         v
                                         If text (question) --> stores "needs_reply"
                                         If image (base64)  --> uploads to tmpfiles.org
                                         If nothing         --> takes screenshot
                                                         |
     +---------------------------------------------------+
     v
POST /api/chat/result or /api/image/result  -->  Reads from Redis  -->  Returns JSON
```

## API Endpoints

| # | Method | Endpoint | Body | Description |
|---|--------|----------|------|-------------|
| 1 | `GET` | `/api/chat` | — | Health check (for UptimeRobot) |
| 2 | `POST` | `/api/chat` | `{ "message": "..." }` | Send chat / image gen request |
| 3 | `POST` | `/api/chat/result` | `{ "id": "..." }` | Poll for chat result |
| 4 | `POST` | `/api/chat/reply` | `{ "id": "...", "message": "..." }` | Reply to duck.ai's follow-up question |
| 5 | `POST` | `/api/edit` | `{ "image_url": "...", "prompt": "..." }` | Send image edit request |
| 6 | `POST` | `/api/edit/result` | `{ "id": "..." }` | Poll for edit result |
| 7 | `POST` | `/api/edit/reply` | `{ "id": "...", "message": "..." }` | Reply to duck.ai's follow-up question |
| 8 | `POST` | `/api/image` | `{ "prompt": "..." }` | Dedicated image gen (clicks Imagine sidebar) |
| 9 | `POST` | `/api/image/result` | `{ "id": "..." }` | Poll for image gen result |

---

### Health Check

```bash
curl "https://ddg-api-iota.vercel.app/api/chat"
```

**Response:**
```json
{ "status": "ok", "timestamp": 1779796100000 }
```

Use this with [UptimeRobot](https://uptimerobot.com) to keep the API warm (prevent cold starts).

---

### 1. Chat — Trigger

```bash
curl -X POST "https://ddg-api-iota.vercel.app/api/chat" \
  -H "Content-Type: application/json" \
  -d '{"message": "what is the capital of France?"}'
```

**Response:**
```json
{
    "status": "triggered",
    "request_id": "c3uyc6hbfoui",
    "note": "POST to /api/chat/result with { \"id\": \"<request_id>\" } to get the response."
}
```

### 2. Chat — Poll for Result

```bash
curl -X POST "https://ddg-api-iota.vercel.app/api/chat/result" \
  -H "Content-Type: application/json" \
  -d '{"id": "c3uyc6hbfoui"}'
```

**Response (image):**
```json
{
    "status": "done",
    "model": "gpt-5-mini",
    "images": ["https://tmpfiles.org/dl/xxxx/ddg_image_0.jpg"],
    "type": "image"
}
```

**Response (text):**
```json
{
    "status": "done",
    "model": "gpt-5-mini",
    "response": "The capital of France is Paris.",
    "type": "text"
}
```

**Response (duck.ai asked a question — needs your reply):**
```json
{
    "status": "needs_reply",
    "text": "Are you 18+? This image may contain adult content. Should I proceed?",
    "request_id": "c3uyc6hbfoui"
}
```

### 3. Chat — Reply to Follow-up Question

```bash
curl -X POST "https://ddg-api-iota.vercel.app/api/chat/reply" \
  -H "Content-Type: application/json" \
  -d '{"id": "c3uyc6hbfoui", "message": "yes, make it"}'
```

**Response:**
```json
{
    "status": "reply_sent",
    "request_id": "c3uyc6hbfoui",
    "message": "Reply stored. The script will send it to duck.ai shortly. Poll /api/chat/result for the final response."
}
```

Then poll `/api/chat/result` again with the same `request_id` to get the final image.

---

### 4. Image Edit — Trigger

```bash
curl -X POST "https://ddg-api-iota.vercel.app/api/edit" \
  -H "Content-Type: application/json" \
  -d '{"image_url": "https://example.com/cat.jpg", "prompt": "make the cat wear sunglasses"}'
```

**Response:**
```json
{
    "status": "triggered",
    "request_id": "abc123def456",
    "note": "POST to /api/edit/result with { \"id\": \"<request_id>\" } to get the edited image."
}
```

### 5. Image Edit — Poll for Result

```bash
curl -X POST "https://ddg-api-iota.vercel.app/api/edit/result" \
  -H "Content-Type: application/json" \
  -d '{"id": "abc123def456"}'
```

**Response (edited image):**
```json
{
    "status": "done",
    "images": ["https://tmpfiles.org/dl/xxxx/ddg_edit_0.jpg"],
    "type": "image"
}
```

**Response (needs reply):**
```json
{
    "status": "needs_reply",
    "text": "What style should the sunglasses be?",
    "request_id": "abc123def456"
}
```

### 6. Image Edit — Reply to Follow-up Question

```bash
curl -X POST "https://ddg-api-iota.vercel.app/api/edit/reply" \
  -H "Content-Type: application/json" \
  -d '{"id": "abc123def456", "message": "any cool style, just add them"}'
```

Then poll `/api/edit/result` again for the final edited image.

---

### 7. Dedicated Image Generation — Trigger

This endpoint opens duck.ai in **Imagine/Image mode** (via sidebar click) for dedicated image generation. Better results than the chat endpoint for image-only requests.

```bash
curl -X POST "https://ddg-api-iota.vercel.app/api/image" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "an extremely cute fluffy baby cat with big sparkling eyes"}'
```

**Response:**
```json
{
    "status": "triggered",
    "request_id": "wdbq8nm6wpe5",
    "note": "POST to /api/image/result with { \"id\": \"<request_id>\" } to get the image."
}
```

### 8. Dedicated Image Generation — Poll for Result

```bash
curl -X POST "https://ddg-api-iota.vercel.app/api/image/result" \
  -H "Content-Type: application/json" \
  -d '{"id": "wdbq8nm6wpe5"}'
```

**Response (image):**
```json
{
    "status": "done",
    "model": "gpt-5-mini",
    "images": ["https://tmpfiles.org/dl/xxxx/ddg_image_0.jpg"],
    "type": "image"
}
```

**Response (screenshot fallback):**
```json
{
    "status": "done",
    "images": ["https://tmpfiles.org/dl/xxxx/ddg_image_screenshot.png"],
    "type": "screenshot",
    "note": "duck.ai did not generate an image. This is a screenshot of the conversation."
}
```

---

## Multi-Turn Conversation

duck.ai often asks follow-up questions before generating images:

1. Send your request via `/api/chat`, `/api/edit`, or `/api/image` → get `request_id`
2. Poll the corresponding `/result` endpoint
3. If `status: "needs_reply"` — duck.ai asked something (age confirmation, style preference, etc.)
4. Reply via `/api/chat/reply` or `/api/edit/reply` with your answer
5. Poll the result endpoint again → get the final image or text

The conversation happens in the same duck.ai session (CloakBrowser stays alive across turns).

## Quick Start (Python)

```python
import requests, time

BASE = "https://ddg-api-iota.vercel.app/api"

def poll_result(endpoint, request_id, max_wait=120):
    elapsed = 0
    while elapsed < max_wait:
        time.sleep(8)
        elapsed += 8
        r = requests.post(f"{BASE}/{endpoint}/result", json={"id": request_id})
        result = r.json()
        if result["status"] == "done":
            return result
        if result["status"] == "needs_reply":
            return result  # Caller must reply via /reply endpoint
    return {"error": "Timeout"}

# --- Dedicated Image Generation (recommended for images) ---
r = requests.post(f"{BASE}/image", json={"prompt": "a cute fluffy baby cat"})
data = r.json()
result = poll_result("image", data["request_id"])

if result.get("images"):
    print(result["images"][0])  # tmpfiles.org URL

# --- Chat ---
r = requests.post(f"{BASE}/chat", json={"message": "hello"})
data = r.json()
result = poll_result("chat", data["request_id"])

if result.get("response"):
    print(result["response"])

# --- Image Editing ---
r = requests.post(f"{BASE}/edit", json={
    "image_url": "https://example.com/cat.jpg",
    "prompt": "make the cat wear sunglasses"
})
data = r.json()
result = poll_result("edit", data["request_id"])

if result.get("images"):
    print(result["images"][0])
```

## Quick Start (curl)

```bash
# --- Health Check ---
curl "https://ddg-api-iota.vercel.app/api/chat"

# --- Dedicated Image Gen ---
# 1. Trigger
curl -X POST "https://ddg-api-iota.vercel.app/api/image" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "a cute baby bunny wearing sunglasses"}'
# → { "status": "triggered", "request_id": "xxx" }

# 2. Poll (wait ~30s, then every 8s)
curl -X POST "https://ddg-api-iota.vercel.app/api/image/result" \
  -H "Content-Type: application/json" \
  -d '{"id": "xxx"}'

# --- Chat ---
# 1. Trigger
curl -X POST "https://ddg-api-iota.vercel.app/api/chat" \
  -H "Content-Type: application/json" \
  -d '{"message": "generate an image of a cute cat"}'

# 2. Poll
curl -X POST "https://ddg-api-iota.vercel.app/api/chat/result" \
  -H "Content-Type: application/json" \
  -d '{"id": "xxx"}'

# 3. If needs_reply, reply and poll again
curl -X POST "https://ddg-api-iota.vercel.app/api/chat/reply" \
  -H "Content-Type: application/json" \
  -d '{"id": "xxx", "message": "yes"}'

# --- Image Edit ---
# 1. Trigger
curl -X POST "https://ddg-api-iota.vercel.app/api/edit" \
  -H "Content-Type: application/json" \
  -d '{"image_url": "https://example.com/bunny.jpg", "prompt": "add cool sunglasses"}'

# 2. Poll
curl -X POST "https://ddg-api-iota.vercel.app/api/edit/result" \
  -H "Content-Type: application/json" \
  -d '{"id": "xxx"}'

# 3. If needs_reply
curl -X POST "https://ddg-api-iota.vercel.app/api/edit/reply" \
  -H "Content-Type: application/json" \
  -d '{"id": "xxx", "message": "yes, add them"}'
```

## Supported Models

| Model | Provider |
|-------|----------|
| gpt-5-mini (default) | OpenAI |
| gpt-4o-mini | OpenAI |
| Llama-4-Scout | Meta |
| Mistral Small | Mistral |
| Claude Haiku 4.5 | Anthropic |
| gpt-oss-120b | Open-source |
| GPT Image 2 | OpenAI (image gen) |

## Architecture

- **Chat + Edit:** CloakBrowser with proxy rotation (PureVPN, SquidProxies, residential)
- **Image Gen:** CloakBrowser direct connection (no proxy) — clicks Imagine mode in duck.ai sidebar
- **All requests:** GH Actions → CloakBrowser → duck.ai → Redis → Vercel returns result

## Timing

| Type | Time |
|------|------|
| Chat (text) | ~20-30s |
| Chat (image gen) | ~30-60s |
| Dedicated image gen (`/api/image`) | ~30-45s |
| Image editing | ~5 min per attempt (luck-based) |
| Screenshot fallback | ~5 min (if duck.ai gives nothing) |

**Polling strategy:** Wait 30s after trigger, then poll every 8s. Max 120s.

## Response Statuses

| Status | Meaning |
|--------|---------|
| `triggered` | Request received, GH Actions workflow started |
| `processing` | Script is running (CloakBrowser active) |
| `needs_reply` | duck.ai asked a question — reply via `/reply` endpoint |
| `done` | Result ready — check `images[]` for URLs or `response` for text |
| `waiting` | Still processing — poll again |
| `error` | Something failed — check `error` field |

## Project Structure

```
ddg-api/
├── api/
│   └── chat.js              # Vercel serverless function (9 endpoints)
├── scripts/
│   ├── proxy_chat.py        # CloakBrowser chat proxy (multi-turn + screenshot fallback)
│   ├── proxy_edit.py        # CloakBrowser image edit proxy (multi-turn + screenshot fallback)
│   └── proxy_image.py       # CloakBrowser image gen proxy (Imagine sidebar mode)
├── .github/
│   └── workflows/
│       ├── proxy-chat.yml   # Chat workflow_dispatch
│       ├── proxy-edit.yml   # Image edit workflow_dispatch
│       └── proxy-image.yml  # Image gen workflow_dispatch
├── requirements.txt         # Python deps (cloakbrowser, requests)
├── package.json             # Vercel project config
├── vercel.json              # Vercel routing config
└── README.md                # This file
```

## Environment Variables

### Vercel
| Variable | Description |
|----------|-------------|
| `UPSTASH_REDIS_REST_URL` | Upstash Redis REST URL |
| `UPSTASH_REDIS_REST_TOKEN` | Upstash Redis read token |
| `GH_PAT` | GitHub Personal Access Token (classic, with `repo` + `workflow` scopes) |

### GitHub Secrets
| Variable | Description |
|----------|-------------|
| `UPSTASH_REDIS_REST_URL` | Upstash Redis REST URL |
| `UPSTASH_REDIS_REST_TOKEN` | Upstash Redis write token |

## UptimeRobot Setup

To prevent cold starts on Vercel free tier:

1. Sign up at [UptimeRobot](https://uptimerobot.com)
2. Add New Monitor → Type: HTTP(s)
3. URL: `https://ddg-api-iota.vercel.app/api/chat`
4. Interval: 5 minutes
5. Save

This keeps the serverless function warm so first requests are fast.

## Known Limitations

- **Image edit is luck-based** — duck.ai sometimes generates images, sometimes doesn't. Retry if it fails.
- **One request at a time** per GH Actions runner
- **Images hosted on tmpfiles.org** — temporary, auto-deletes after some time
- **Input images for edit** must be on persistent URLs (tmpfiles.org expires fast, use catbox.moe)
- **CloakBrowser segfaults** intermittently on GH Actions (~60% success rate)
- **Rate limit** depends on duck.ai

## Why Browser Intercept?

- Vercel DC IPs get blocked (`ERR_BN_LIMIT` — bot network detection)
- Manual header capture fails (`ERR_CHALLENGE` — missing fingerprint)
- CloakBrowser = real browser, handles all anti-bot natively
- Proxy rotation adds extra layer of anti-detection (chat + edit)

## License

Private — for personal use only.
