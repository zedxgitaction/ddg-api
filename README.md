# DDG API

DuckDuckGo AI Chat API — powered by CloakBrowser on GitHub Actions. Intercepts duck.ai chat responses and relays them via Vercel + Upstash Redis.

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
                                    Intercepts /duckchat/v1/chat
                                    network response
                                             |
                                             v
                                    Stores result in Redis (120s TTL)
                                             |
     +---------------------------------------+
     v
POST /api/chat/result  -->  Reads from Redis  -->  Returns JSON
```

## API Endpoints

### 1. Chat — Trigger

```
POST https://ddg-api-iota.vercel.app/api/chat
Content-Type: application/json

{"message": "your prompt here"}
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

```
POST https://ddg-api-iota.vercel.app/api/chat/result
Content-Type: application/json

{"id": "c3uyc6hbfoui"}
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

**Response (image):**
```json
{
    "status": "done",
    "model": "gpt-5-mini",
    "response": "GPT Image 2\n. .",
    "images": ["https://tmpfiles.org/dl/xxxx/ddg_image_0.jpg"],
    "type": "image",
    "proxy": "px043005.pointtoserver.com:10780"
}
```

### 3. Image Edit — Trigger

```
POST https://ddg-api-iota.vercel.app/api/edit
Content-Type: application/json

{
    "image_url": "https://example.com/cat.jpg",
    "prompt": "make the cat wear sunglasses"
}
```

**Response:**
```json
{
    "status": "triggered",
    "request_id": "abc123def456",
    "note": "POST to /api/edit/result with { \"id\": \"<request_id>\" } to get the edited image."
}
```

### 4. Image Edit — Poll for Result

```
POST https://ddg-api-iota.vercel.app/api/edit/result
Content-Type: application/json

{"id": "abc123def456"}
```

**Response:**
```json
{
    "status": "done",
    "model": "gpt-5-mini",
    "response": "Here is the edited image...",
    "images": ["https://tmpfiles.org/dl/xxxx/ddg_edit_0.jpg"],
    "type": "image",
    "proxy": "px031901.pointtoserver.com:10780"
}
```

**All endpoints return:** `processing` | `waiting` | `done` | `not_found` | `error`

## Quick Start (Python)

```python
import requests, time

BASE = "https://ddg-api-iota.vercel.app/api"

def poll_result(endpoint, request_id, max_wait=90):
    elapsed = 0
    while elapsed < max_wait:
        time.sleep(8)
        elapsed += 8
        r = requests.post(f"{BASE}/{endpoint}/result", json={"id": request_id})
        result = r.json()
        if result["status"] == "done":
            return result
        if result["status"] == "not_found":
            return {"error": "Request expired"}
    return {"error": "Timeout"}

# --- Chat: Text ---
r = requests.post(f"{BASE}/chat", json={"message": "What is Python?"})
data = r.json()
result = poll_result("chat", data["request_id"])
print(result["response"])

# --- Chat: Image generation ---
r = requests.post(f"{BASE}/chat", json={"message": "generate an image of a cat"})
data = r.json()
result = poll_result("chat", data["request_id"])
if result.get("images"):
    print(result["images"][0])

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
# --- Chat ---
# Trigger
curl -X POST "https://ddg-api-iota.vercel.app/api/chat" \
  -H "Content-Type: application/json" \
  -d '{"message": "hello"}'

# Poll (use the request_id from trigger)
curl -X POST "https://ddg-api-iota.vercel.app/api/chat/result" \
  -H "Content-Type: application/json" \
  -d '{"id": "YOUR_REQUEST_ID"}'

# --- Image Edit ---
# Trigger
curl -X POST "https://ddg-api-iota.vercel.app/api/edit" \
  -H "Content-Type: application/json" \
  -d '{"image_url": "https://example.com/cat.jpg", "prompt": "add sunglasses"}'

# Poll
curl -X POST "https://ddg-api-iota.vercel.app/api/edit/result" \
  -H "Content-Type: application/json" \
  -d '{"id": "YOUR_REQUEST_ID"}'
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
| px043005.pointtoserver.com:10780 | PureVPN (2 accounts) |
| px031901.pointtoserver.com:10780 | PureVPN |
| p101.squidproxies.com:9088 | SquidProxies |
| 136.179.19.164:3128 | Residential |

## Timing

| Type | Time |
|------|------|
| GH Actions startup | ~14s |
| Text response | ~20-25s |
| Image generation | ~25-45s |
| Image editing | ~30-50s |

**Polling strategy:** Wait 20s after trigger, then poll every 8s. Max 90s.

## Project Structure

```
ddg-api/
├── api/
│   └── chat.js              # Vercel serverless function (chat + edit endpoints)
├── scripts/
│   ├── proxy_chat.py        # CloakBrowser chat proxy (with proxy rotation)
│   └── proxy_edit.py        # CloakBrowser image edit proxy (with proxy rotation)
├── .github/
│   └── workflows/
│       ├── proxy-chat.yml   # Chat workflow_dispatch
│       └── proxy-edit.yml   # Image edit workflow_dispatch
├── .env.example             # Required env vars template
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

## Limitations

- Request TTL: 180 seconds (expires if not polled in time)
- One request at a time per GH Actions runner
- Images hosted on tmpfiles.org (temporary, auto-deletes)
- No multi-turn conversation (single request/response)
- Rate limit depends on duck.ai

## Why Browser Intercept?

- Vercel DC IPs get blocked (`ERR_BN_LIMIT` - bot network detection)
- Manual header capture fails (`ERR_CHALLENGE` - missing fingerprint)
- CloakBrowser = real browser, handles all anti-bot natively
- Network interception of `/duckchat/v1/chat` response = reliable
- Proxy rotation adds extra layer of anti-detection
