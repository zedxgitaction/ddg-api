// Vercel Serverless Function: DDG AI Chat API (Full Proxy)
//
// POST /api/chat        { "message": "hello" }        → triggers GH Actions proxy, returns { request_id }
// POST /api/chat/result { "id": "xxx" }                → polls Redis for result
//
// GH Actions (CloakBrowser) handles the actual DDG request to bypass IP blocking.

const UPSTASH_URL = process.env.UPSTASH_REDIS_REST_URL;
const UPSTASH_TOKEN = process.env.UPSTASH_REDIS_REST_TOKEN;
const GH_PAT = process.env.GH_PAT;
const GH_REPO = "zade911786/ddg-api";
const GH_WORKFLOW = "proxy-chat.yml";

function randomId() {
  const chars = "abcdefghijklmnopqrstuvwxyz0123456789";
  let s = "";
  for (let i = 0; i < 12; i++) s += chars[Math.floor(Math.random() * chars.length)];
  return s;
}

function parseBody(req) {
  if (req.body) return req.body;
  try {
    return JSON.parse(req.body || "{}");
  } catch {
    return {};
  }
}

async function redisSet(key, value, ttl = 120) {
  const r = await fetch(`${UPSTASH_URL}/pipeline`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${UPSTASH_TOKEN}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify([["SET", key, typeof value === "string" ? value : JSON.stringify(value), "EX", ttl]]),
  });
  return r.ok;
}

async function redisGet(key) {
  const r = await fetch(`${UPSTASH_URL}/get/${key}`, {
    headers: { Authorization: `Bearer ${UPSTASH_TOKEN}` },
  });
  const data = await r.json();
  if (!data.result) return null;
  try {
    return JSON.parse(data.result);
  } catch {
    return data.result;
  }
}

async function triggerWorkflow(message, requestId) {
  const r = await fetch(
    `https://api.github.com/repos/${GH_REPO}/actions/workflows/${GH_WORKFLOW}/dispatches`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${GH_PAT}`,
        Accept: "application/vnd.github.v3+json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        ref: "master",
        inputs: { message, request_id: requestId },
      }),
    }
  );
  return r.ok;
}

export default async function handler(req, res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "POST, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");

  if (req.method === "OPTIONS") {
    return res.status(200).end();
  }

  if (req.method !== "POST") {
    return res.status(405).json({ error: "Use POST with JSON body" });
  }

  const body = parseBody(req);

  // Poll for result: POST /api/chat/result { "id": "xxx" }
  if (req.url.includes("/result")) {
    const id = body.id;
    if (!id) {
      return res.status(400).json({ error: 'Missing "id" in request body' });
    }

    const data = await redisGet(`chat:${id}`);
    if (!data) {
      return res.status(200).json({
        status: "waiting",
        message: "Request still processing or expired. GH Actions takes ~30-60s.",
      });
    }

    return res.status(200).json(data);
  }

  // Trigger: POST /api/chat { "message": "hello" }
  const msg = body.message;
  if (!msg) {
    return res.status(400).json({ error: 'Missing "message" in request body' });
  }

  if (!GH_PAT) {
    return res.status(500).json({ error: "GH_PAT not configured" });
  }

  const requestId = randomId();

  await redisSet(`chat:${requestId}`, { status: "queued" }, 120);

  const triggered = await triggerWorkflow(msg, requestId);
  if (!triggered) {
    return res.status(500).json({ error: "Failed to trigger GH Actions workflow" });
  }

  return res.status(200).json({
    status: "triggered",
    request_id: requestId,
    note: "POST to /api/chat/result with { \"id\": \"<request_id>\" } to get the response.",
  });
}
