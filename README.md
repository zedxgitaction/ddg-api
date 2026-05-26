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

### 1. Trigger Request

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

### 2. Poll for Result

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
    "type": "text"
}
```

**Response (image):**
```json
{
    "status": "done",
    "model": "gpt-5-mini",
    "response": "GPT Image 2\n. .",
    "images": ["https://tmpfiles.org/dl/xxxx/ddg_image_0.jpg"],
    "type": "image"
}
```

**Other statuses:** `processing` | `waiting` | `not_found` | `error`

## Quick Start (Python)

```python
import requests, time

BASE = "https://ddg-api-iota.vercel.app/api"

def ask_ddg(message, max_wait=90):
    # Step 1: Trigger
    r = requests.post(f"{BASE}/chat", json={"message": message})
    data = r.json()
    if data.get("status") != "triggered":
        return {"error": data.get("message", "Trigger failed")}

    request_id = data["request_id"]

    # Step 2: Poll
    elapsed = 0
    while elapsed < max_wait:
        time.sleep(8)
        elapsed += 8
        r = requests.post(f"{BASE}/chat/result", json={"id": request_id})
        result = r.json()
        if result["status"] == "done":
            return result
        if result["status"] == "not_found":
            return {"error": "Request expired"}

    return {"error": "Timeout"}

# Text chat
result = ask_ddg("What is Python?")
print(result["response"])

# Image generation
result = ask_ddg("generate an image of a cat")
if result.get("images"):
    print(result["images"][0])
```

## Quick Start (curl)

```bash
# Step 1: Trigger
curl -X POST "https://ddg-api-iota.vercel.app/api/chat" \
  -H "Content-Type: application/json" \
  -d '{"message": "hello"}'

# Step 2: Poll (use the request_id from step 1)
curl -X POST "https://ddg-api-iota.vercel.app/api/chat/result" \
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

## Timing

| Type | Time |
|------|------|
| GH Actions startup | ~14s |
| Text response | ~20-25s |
| Image generation | ~25-45s |

**Polling strategy:** Wait 20s after trigger, then poll every 8s. Max 90s.

## Project Structure

```
ddg-api/
├── api/
│   └── chat.js              # Vercel serverless function (trigger + result)
├── scripts/
│   └── proxy_chat.py        # CloakBrowser proxy script
├── .github/
│   └── workflows/
│       └── proxy-chat.yml   # GH Actions workflow_dispatch
├── .env.example             # Required env vars template
├── requirements.txt         # Python deps (cloakbrowser, requests)
├── package.json             # Vercel project config
└── vercel.json              # Vercel routing config
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

- Request TTL: 120 seconds (expires if not polled in time)
- One request at a time per GH Actions runner
- Images hosted on tmpfiles.org (temporary, auto-deletes)
- No multi-turn conversation (single request/response)
- Rate limit depends on duck.ai

## Why Browser Intercept?

- Vercel DC IPs get blocked (`ERR_BN_LIMIT` - bot network detection)
- Manual header capture fails (`ERR_CHALLENGE` - missing fingerprint)
- CloakBrowser = real browser, handles all anti-bot natively
- Network interception of `/duckchat/v1/chat` response = reliable
