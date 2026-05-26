# DDG API

Free AI chat, image generation, and image editing API powered by [duck.ai](https://duck.ai) via CloakBrowser on GitHub Actions with rotating proxies.

**Live API:** https://ddg-api-iota.vercel.app

## How It Works

```
User sends message
     |
     v
POST /api/chat  -->  Stores in Redis  -->  Triggers GH Actions
     |                                         |
     v                                         v
Returns request_id                   CloakBrowser opens duck.ai
                                             |
                                             v
                                    Types message, presses Enter
                                             |
                                             v
                                    Intercepts duck.ai response
                                             |
                                             v
                               If text (question) --> stores "needs_reply" in Redis
                               If image (base64)  --> uploads to tmpfiles.org
                               If nothing         --> takes screenshot, uploads
                                             |
     +---------------------------------------+
     v
POST /api/chat/result  -->  Reads from Redis  -->  Returns JSON
```

## API Endpoints

All endpoints are POST-only with JSON body.

| # | Endpoint | Body | Description |
|---|----------|------|-------------|
| 1 | `/api/chat` | `{ "message": "..." }` | Send chat / image gen request |
| 2 | `/api/chat/result` | `{ "id": "..." }` | Poll for chat result |
| 3 | `/api/chat/reply` | `{ "id": "...", "message": "..." }` | Reply to duck.ai's follow-up question |
| 4 | `/api/edit` | `{ "image_url": "...", "prompt": "..." }` | Send image edit request |
| 5 | `/api/edit/result` | `{ "id": "..." }` | Poll for edit result |
| 6 | `/api/edit/reply` | `{ "id": "...", "message": "..." }` | Reply to duck.ai's follow-up question |

---

### 1. Chat — Trigger

```bash
curl -X POST "https://ddg-api-iota.vercel.app/api/chat" \
  -H "Content-Type: application/json" \
  -d '{"message": "generate an image of a cute cat"}'
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
    "type": "image",
    "proxy": "px043005.pointtoserver.com:10780"
}
```

**Response (text):**
```json
{
    "status": "done",
    "model": "gpt-5-mini",
    "response": "Here is the answer...",
    "type": "text",
    "proxy": "px043005.pointtoserver.com:10780"
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

When the result returns `status: "needs_reply"`, reply to duck.ai's question:

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
    "type": "image",
    "proxy": "px031901.pointtoserver.com:10780"
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

**Response (screenshot fallback — duck.ai didn't generate image):**
```json
{
    "status": "done",
    "images": ["https://tmpfiles.org/dl/xxxx/ddg_edit_screenshot.png"],
    "type": "screenshot",
    "note": "duck.ai did not generate an edited image. This is a screenshot of the conversation."
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

## Multi-Turn Conversation

duck.ai often asks follow-up questions before generating images:

1. Send your request via `/api/chat` or `/api/edit` → get `request_id`
2. Poll `/api/chat/result` or `/api/edit/result`
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

# --- Chat: Image generation ---
r = requests.post(f"{BASE}/chat", json={"message": "generate an image of a cat"})
data = r.json()
result = poll_result("chat", data["request_id"])

if result.get("status") == "needs_reply":
    # duck.ai asked a question — reply to it
    r2 = requests.post(f"{BASE}/chat/reply", json={
        "id": data["request_id"],
        "message": "yes, make it"
    })
    result = poll_result("chat", data["request_id"])

if result.get("images"):
    print(result["images"][0])  # tmpfiles.org URL

# --- Image Editing ---
r = requests.post(f"{BASE}/edit", json={
    "image_url": "https://example.com/cat.jpg",
    "prompt": "make the cat wear sunglasses"
})
data = r.json()
result = poll_result("edit", data["request_id"])

if result.get("status") == "needs_reply":
    r2 = requests.post(f"{BASE}/edit/reply", json={
        "id": data["request_id"],
        "message": "just do it"
    })
    result = poll_result("edit", data["request_id"])

if result.get("images"):
    print(result["images"][0])
```

## Quick Start (curl)

```bash
# --- Chat (Image Gen) ---
# 1. Trigger
curl -X POST "https://ddg-api-iota.vercel.app/api/chat" \
  -H "Content-Type: application/json" \
  -d '{"message": "generate an image of a cute baby bunny"}'
# → { "status": "triggered", "request_id": "xxx" }

# 2. Poll (wait ~30s, then every 8s)
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

# 2. Poll (wait ~60s, then every 8s)
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

## Proxy Rotation

All requests go through rotating proxies for anti-detection. 5 proxies are used randomly per request:

| Proxy | Type |
|-------|------|
| px043005.pointtoserver.com:10780 | PureVPN |
| px043005.pointtoserver.com:10780 | PureVPN (alt account) |
| px031901.pointtoserver.com:10780 | PureVPN |
| p101.squidproxies.com:9088 | SquidProxies |
| 136.179.19.164:3128 | Residential |

## Timing

| Type | Time |
|------|------|
| GH Actions startup + CloakBrowser install | ~2 min |
| Text response | ~20-30s |
| Image generation | ~30-60s |
| Image editing | ~5 min per attempt (luck-based) |
| Screenshot fallback | ~5 min (if duck.ai gives nothing) |

**Polling strategy:** Wait 30s after trigger for chat, 60s for edit. Then poll every 8s. Max 120s.

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
│   └── chat.js              # Vercel serverless function (6 endpoints)
├── scripts/
│   ├── proxy_chat.py        # CloakBrowser chat proxy (multi-turn + screenshot fallback)
│   └── proxy_edit.py        # CloakBrowser image edit proxy (multi-turn + screenshot fallback)
├── .github/
│   └── workflows/
│       ├── proxy-chat.yml   # Chat workflow_dispatch
│       └── proxy-edit.yml   # Image edit workflow_dispatch
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
| `GH_PAT` | GitHub Personal Access Token (to trigger workflow) |

### GitHub Secrets
| Variable | Description |
|----------|-------------|
| `UPSTASH_REDIS_REST_URL` | Upstash Redis REST URL |
| `UPSTASH_REDIS_REST_TOKEN` | Upstash Redis write token |

## Known Limitations

- **Image edit is luck-based** — duck.ai sometimes generates images, sometimes doesn't. Retry if it fails.
- **~2 min GH Actions startup** — CloakBrowser install takes time
- **One request at a time** per GH Actions runner
- **Images hosted on tmpfiles.org** — temporary, auto-deletes after some time
- **Input images for edit** must be on persistent URLs (tmpfiles.org expires fast, use catbox.moe)
- **CloakBrowser segfaults** intermittently on GH Actions (~60% success rate)
- **Prompt truncation** at ~100 chars in CloakBrowser input
- **Rate limit** depends on duck.ai

## Why Browser Intercept?

- Vercel DC IPs get blocked (`ERR_BN_LIMIT` — bot network detection)
- Manual header capture fails (`ERR_CHALLENGE` — missing fingerprint)
- CloakBrowser = real browser, handles all anti-bot natively
- Proxy rotation adds extra layer of anti-detection

## License

Private — for personal use only.
