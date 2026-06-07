// Vercel Serverless Function: DDG AI Chat + Image Edit + Image Gen API
//
// POST /api/chat         { "message": "hello" }                          → { status: "triggered", request_id }
// POST /api/chat/result  { "id": "xxx" }                                 → polls Redis for chat result
// POST /api/chat/reply   { "id": "xxx", "message": "yes" }              → send reply to multi-turn conversation
// POST /api/edit         { "image_url": "url", "prompt": "edit this" }   → { status: "triggered", request_id }
// POST /api/edit/result  { "id": "xxx" }                                 → polls Redis for edit result
// POST /api/edit/reply   { "id": "xxx", "message": "yes" }              → send reply to multi-turn edit conversation
// POST /api/image        { "prompt": "a cat" }                           → { status: "triggered", request_id }
// POST /api/image/result { "id": "xxx" }                                 → polls Redis for image result
//
// GH Actions (CloakBrowser) handles the actual DDG request to bypass IP blocking.

const UPSTASH_URL = process.env.UPSTASH_REDIS_REST_URL;
const UPSTASH_TOKEN = process.env.UPSTASH_REDIS_REST_TOKEN;
const GH_PAT = process.env.GH_PAT;
const GH_REPO = "zedxlab/ddg-api";

function randomId() {
  const chars = "abcdefghijklmnopqrstuvwxyz0123456789";
  let s = "";
  for (let i = 0; i < 12; i++) s += chars[Math.floor(Math.random() * chars.length)];
  return s;
}

function parseBody(req) {
  if (req.body && typeof req.body === "object") return req.body;
  try {
    return JSON.parse(req.body || "{}");
  } catch {
    return {};
  }
}

async function redisSet(key, value, ttl = 300) {
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

async function triggerWorkflow(workflow, inputs) {
  const r = await fetch(
    `https://api.github.com/repos/${GH_REPO}/actions/workflows/${workflow}/dispatches`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${GH_PAT}`,
        Accept: "application/vnd.github.v3+json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        ref: "master",
        inputs,
      }),
    }
  );
  return r.ok;
}

export default async function handler(req, res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");

  if (req.method === "OPTIONS") {
    return res.status(200).end();
  }

  // Health check for UptimeRobot (GET)
  if (req.method === "GET") {
    return res.status(200).json({ status: "ok", timestamp: Date.now() });
  }

  if (req.method !== "POST") {
    return res.status(405).json({ error: "Use POST with JSON body" });
  }

  const body = parseBody(req);
  const url = req.url || "";

  // ─── CHAT REPLY (multi-turn) ───
  if (url.includes("/chat/reply")) {
    const id = body.id;
    const msg = body.message;
    if (!id) return res.status(400).json({ error: 'Missing "id" in request body' });
    if (!msg) return res.status(400).json({ error: 'Missing "message" in request body' });

    await redisSet(`reply:${id}`, { message: msg }, 120);
    return res.status(200).json({
      status: "reply_sent",
      request_id: id,
      message: "Reply stored. The script will send it to duck.ai shortly. Poll /api/chat/result for the final response.",
    });
  }

  // ─── EDIT REPLY (multi-turn) ───
  if (url.includes("/edit/reply")) {
    const id = body.id;
    const msg = body.message;
    if (!id) return res.status(400).json({ error: 'Missing "id" in request body' });
    if (!msg) return res.status(400).json({ error: 'Missing "message" in request body' });

    await redisSet(`reply:${id}`, { message: msg }, 120);
    return res.status(200).json({
      status: "reply_sent",
      request_id: id,
      message: "Reply stored. The script will send it to duck.ai shortly. Poll /api/edit/result for the final response.",
    });
  }

  // ─── CHAT RESULT POLL ───
  if (url.includes("/chat/result")) {
    const id = body.id;
    if (!id) return res.status(400).json({ error: 'Missing "id" in request body' });

    const data = await redisGet(`chat:${id}`);
    if (!data) {
      return res.status(200).json({
        status: "waiting",
        message: "Request still processing or expired. GH Actions takes ~30-60s.",
      });
    }
    return res.status(200).json(data);
  }

  // ─── EDIT RESULT POLL ───
  if (url.includes("/edit/result")) {
    const id = body.id;
    if (!id) return res.status(400).json({ error: 'Missing "id" in request body' });

    const data = await redisGet(`chat:${id}`);
    if (!data) {
      return res.status(200).json({
        status: "waiting",
        message: "Edit still processing or expired. GH Actions takes ~60-120s.",
      });
    }
    return res.status(200).json(data);
  }

  // ─── IMAGE RESULT POLL ───
  if (url.includes("/image/result")) {
    const id = body.id;
    if (!id) return res.status(400).json({ error: 'Missing "id" in request body' });

    const data = await redisGet(`result:${id}`);
    if (!data) {
      return res.status(200).json({
        status: "waiting",
        message: "Image still processing or expired. GH Actions takes ~60-90s.",
      });
    }
    return res.status(200).json(data);
  }

  // ─── IMAGE GEN TRIGGER ───
  if (url.includes("/image")) {
    const prompt = body.prompt;
    if (!prompt) return res.status(400).json({ error: 'Missing "prompt" in request body' });

    if (!GH_PAT) return res.status(500).json({ error: "GH_PAT not configured" });

    const requestId = randomId();
    await redisSet(`result:${requestId}`, { status: "queued" }, 600);

    const triggered = await triggerWorkflow("proxy-image.yml", {
      prompt,
      request_id: requestId,
    });

    if (!triggered) {
      return res.status(500).json({ error: "Failed to trigger GH Actions workflow" });
    }

    return res.status(200).json({
      status: "triggered",
      request_id: requestId,
      note: 'POST to /api/image/result with { "id": "<request_id>" } to get the image.',
    });
  }

  // ─── IMAGE EDIT TRIGGER ───
  if (url.includes("/edit")) {
    const image_url = body.image_url;
    const prompt = body.prompt;
    if (!image_url) return res.status(400).json({ error: 'Missing "image_url" in request body' });
    if (!prompt) return res.status(400).json({ error: 'Missing "prompt" in request body' });

    if (!GH_PAT) return res.status(500).json({ error: "GH_PAT not configured" });

    const requestId = randomId();
    await redisSet(`chat:${requestId}`, { status: "queued" }, 300);

    const triggered = await triggerWorkflow("proxy-edit.yml", {
      image_url,
      edit_prompt: prompt,
      request_id: requestId,
    });

    if (!triggered) {
      return res.status(500).json({ error: "Failed to trigger GH Actions workflow" });
    }

    return res.status(200).json({
      status: "triggered",
      request_id: requestId,
      note: 'POST to /api/edit/result with { "id": "<request_id>" } to get the result.',
    });
  }

  // ─── CHAT TRIGGER (default) ───
  const msg = body.message;
  if (!msg) return res.status(400).json({ error: 'Missing "message" in request body' });

  if (!GH_PAT) return res.status(500).json({ error: "GH_PAT not configured" });

  const requestId = randomId();
  await redisSet(`chat:${requestId}`, { status: "queued" }, 300);

  const triggered = await triggerWorkflow("proxy-chat.yml", {
    message: msg,
    request_id: requestId,
  });

  if (!triggered) {
    return res.status(500).json({ error: "Failed to trigger GH Actions workflow" });
  }

  return res.status(200).json({
    status: "triggered",
    request_id: requestId,
    note: 'POST to /api/chat/result with { "id": "<request_id>" } to get the response.',
  });
}
