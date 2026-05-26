// Vercel Serverless Function: GET /api/chat?msg=hello
// Reads fresh DDG headers from Upstash Redis, proxies chat, returns JSON

const UPSTASH_URL = process.env.UPSTASH_REDIS_REST_URL;
const UPSTASH_TOKEN = process.env.UPSTASH_REDIS_REST_TOKEN;
const DDG_CHAT_URL = "https://duck.ai/duckchat/v1/chat";

async function getHeaders() {
  const res = await fetch(`${UPSTASH_URL}/get/ddg_headers`, {
    headers: { Authorization: `Bearer ${UPSTASH_TOKEN}` },
  });
  const data = await res.json();
  if (!data.result) return null;
  try {
    return JSON.parse(data.result);
  } catch {
    return null;
  }
}

export default async function handler(req, res) {
  // CORS
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET");

  if (req.method !== "GET") {
    return res.status(405).json({ error: "Use GET ?msg=your+message" });
  }

  const msg = req.query.msg;
  if (!msg) {
    return res.status(400).json({ error: "Missing ?msg= parameter" });
  }

  const headers = await getHeaders();
  if (!headers) {
    return res.status(503).json({
      error: "No fresh headers. Cron job may not have run yet. Retry in 4 min.",
    });
  }

  const payload = {
    model: "gpt-5-mini",
    metadata: {
      toolChoice: {
        NewsSearch: false,
        VideosSearch: false,
        LocalSearch: false,
        WeatherForecast: false,
      },
      "x-vqd-hash-1": headers["x-vqd-hash-1"],
    },
    messages: [{ role: "user", content: msg }],
  };

  const ddgHeaders = {
    "Content-Type": "application/json",
    accept: "text/event-stream",
    "User-Agent":
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    "x-fe-version": headers["x-fe-version"],
    "x-fe-signals": headers["x-fe-signals"],
    "x-ddg-journey-id": headers["x-ddg-journey-id"],
    Origin: "https://duck.ai",
    Referer: "https://duck.ai/",
  };

  try {
    const ddgRes = await fetch(DDG_CHAT_URL, {
      method: "POST",
      headers: ddgHeaders,
      body: JSON.stringify(payload),
    });

    if (!ddgRes.ok) {
      const errText = await ddgRes.text();
      return res.status(ddgRes.status).json({
        error: `DDG returned ${ddgRes.status}`,
        detail: errText.slice(0, 500),
      });
    }

    // Collect full SSE response, extract text
    const raw = await ddgRes.text();
    let fullText = "";

    for (const line of raw.split("\n")) {
      if (!line.startsWith("data: ") || line.includes("[DONE]")) continue;
      try {
        const json = JSON.parse(line.slice(6));
        const message = json.message;
        if (message) fullText += message;
      } catch {
        // skip malformed lines
      }
    }

    if (!fullText) {
      // Try alternate format: messages[].parts[]
      for (const line of raw.split("\n")) {
        if (!line.startsWith("data: ") || line.includes("[DONE]")) continue;
        try {
          const json = JSON.parse(line.slice(6));
          const parts = json.messages?.[0]?.parts;
          if (parts) {
            for (const p of parts) {
              if (p.text) fullText += p.text;
            }
          }
        } catch {}
      }
    }

    return res.status(200).json({
      status: "success",
      model: "gpt-5-mini",
      response: fullText.trim() || "No response text extracted",
      raw_length: raw.length,
    });
  } catch (err) {
    return res.status(500).json({ error: err.message });
  }
}
