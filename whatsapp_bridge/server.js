import express from "express";
import qrcode from "qrcode";
import pkg from "whatsapp-web.js";

const { Client, LocalAuth } = pkg;

const PORT = Number(process.env.WHATSAPP_BRIDGE_PORT || 8125);
const TOKEN = process.env.WHATSAPP_BRIDGE_TOKEN || "";
const CHROME_PATH = process.env.WHATSAPP_CHROME_PATH || "";

// Self-healing tuning. A "soft" restart recreates the Puppeteer/browser
// client but keeps the LocalAuth session folder on disk, so it reconnects
// without requiring a new QR scan. Only /sessions/:session/reset wipes auth.
const HEALTH_CHECK_INTERVAL_MS = Number(process.env.WHATSAPP_HEALTHCHECK_INTERVAL_MS || 3 * 60 * 1000);
const HEALTH_CHECK_TIMEOUT_MS = Number(process.env.WHATSAPP_HEALTHCHECK_TIMEOUT_MS || 15000);
const RESTART_COOLDOWN_MS = Number(process.env.WHATSAPP_RESTART_COOLDOWN_MS || 60000);
const MAX_CONSECUTIVE_RESTARTS = Number(process.env.WHATSAPP_MAX_AUTO_RESTARTS || 5);
const SEND_RECOVERY_TIMEOUT_MS = Number(process.env.WHATSAPP_SEND_RECOVERY_TIMEOUT_MS || 25000);

const sessions = new Map();

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function requireAuth(req, res, next) {
  if (!TOKEN) {
    return next();
  }
  const header = req.get("authorization") || "";
  if (header !== `Bearer ${TOKEN}`) {
    return res.status(401).json({ error: "Unauthorized" });
  }
  return next();
}

function normalizeSession(value) {
  return String(value || "main").replace(/[^a-zA-Z0-9_-]/g, "") || "main";
}

function buildClient(sessionName) {
  const puppeteer = {
    headless: true,
    args: ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
  };
  if (CHROME_PATH) {
    puppeteer.executablePath = CHROME_PATH;
  }

  return new Client({
    authStrategy: new LocalAuth({
      clientId: sessionName,
      dataPath: process.env.WHATSAPP_AUTH_DATA_PATH || "./sessions"
    }),
    puppeteer
  });
}

function attachClientEvents(client, state) {
  client.on("qr", async (qr) => {
    state.qr = qr;
    state.qrImage = await qrcode.toDataURL(qr);
    state.status = "qr";
    state.lastError = "";
  });

  client.on("ready", () => {
    state.status = "ready";
    state.qr = "";
    state.qrImage = "";
    state.readyAt = new Date().toISOString();
    state.restartCount = 0;
    const info = client.info || {};
    state.phone = info.wid?.user || "";
  });

  client.on("authenticated", () => {
    state.status = "authenticated";
  });

  client.on("auth_failure", (message) => {
    state.status = "auth_failure";
    state.lastError = String(message || "Authentication failed");
  });

  client.on("disconnected", (reason) => {
    state.status = "disconnected";
    state.lastError = String(reason || "");
  });
}

function getSession(name) {
  const sessionName = normalizeSession(name);
  let state = sessions.get(sessionName);
  if (state) {
    return state;
  }

  state = {
    name: sessionName,
    client: null,
    status: "starting",
    qr: "",
    qrImage: "",
    phone: "",
    lastError: "",
    readyAt: null,
    restarting: false,
    lastRestartAt: 0,
    restartCount: 0
  };

  const client = buildClient(sessionName);
  attachClientEvents(client, state);
  state.client = client;
  sessions.set(sessionName, state);

  client.initialize().catch((error) => {
    state.status = "error";
    state.lastError = error?.message || String(error);
  });

  return state;
}

// Recreates the underlying WhatsApp Web client for a session without
// touching its saved LocalAuth credentials, unless wipeAuth is requested
// (that's the destructive path used by /sessions/:session/reset, which
// forces a fresh QR scan). Used both reactively (a send failed) and
// proactively (periodic health check found a stuck/detached session).
async function restartClient(state, { wipeAuth = false, reason = "" } = {}) {
  if (state.restarting) {
    return;
  }
  const now = Date.now();
  if (!wipeAuth) {
    if (now - state.lastRestartAt < RESTART_COOLDOWN_MS) {
      return;
    }
    if (state.restartCount >= MAX_CONSECUTIVE_RESTARTS) {
      state.lastError = `Auto-restart limit reached (${MAX_CONSECUTIVE_RESTARTS}); manual /reset required.`;
      return;
    }
  }

  state.restarting = true;
  state.lastRestartAt = now;
  state.restartCount += 1;
  console.log(`[whatsapp:${state.name}] restarting client (wipeAuth=${wipeAuth}, reason=${reason || "n/a"})`);

  try {
    if (state.client) {
      await state.client.destroy();
    }
  } catch (_) {
    // ignore — client may already be in a broken/detached state
  }

  if (wipeAuth) {
    const { rm } = await import("fs/promises");
    const dataPath = process.env.WHATSAPP_AUTH_DATA_PATH || "./sessions";
    try {
      await rm(`${dataPath}/session-${state.name}`, { recursive: true, force: true });
    } catch (_) {}
    state.restartCount = 0;
  }

  state.status = "starting";
  state.qr = "";
  state.qrImage = "";
  state.lastError = "";

  const client = buildClient(state.name);
  attachClientEvents(client, state);
  state.client = client;

  client.initialize().catch((error) => {
    state.status = "error";
    state.lastError = error?.message || String(error);
  });

  state.restarting = false;
}

async function waitForReady(state, timeoutMs) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    if (state.status === "ready") {
      return true;
    }
    if (state.status === "qr" || state.status === "auth_failure") {
      // Needs a human to scan a QR code — no point in busy-waiting.
      return false;
    }
    await sleep(500);
  }
  return state.status === "ready";
}

function publicState(state) {
  return {
    session: state.name,
    status: state.status,
    phone: state.phone,
    ready_at: state.readyAt,
    error: state.lastError,
    restart_count: state.restartCount
  };
}

const app = express();
app.use(express.json({ limit: "1mb" }));
app.use(requireAuth);

app.get("/health", (req, res) => {
  res.json({ ok: true });
});

app.post("/auth/qr", (req, res) => {
  const state = getSession(req.body?.session);
  res.json({
    ...publicState(state),
    qr: state.qr,
    qr_image: state.qrImage
  });
});

app.get("/sessions/:session/status", (req, res) => {
  res.json(publicState(getSession(req.params.session)));
});

// Soft recovery: keeps the saved WhatsApp login, just recreates the
// browser/client. Safe to call any time a session looks stuck.
app.post("/sessions/:session/restart", async (req, res) => {
  const state = getSession(req.params.session);
  await restartClient(state, { reason: "manual" });
  res.json({ ok: true, session: state.name, status: state.status });
});

// Destructive: wipes the saved session, always requires a new QR scan.
app.post("/sessions/:session/reset", async (req, res) => {
  const state = getSession(req.params.session);
  await restartClient(state, { wipeAuth: true, reason: "manual_reset" });
  res.json({ ok: true, session: state.name });
});

app.post("/messages", async (req, res) => {
  const state = getSession(req.body?.session);
  const digits = String(req.body?.to || "").replace(/\D/g, "");
  const body = String(req.body?.body || "");
  if (!digits || !body) {
    return res.status(400).json({ error: "Both to and body are required." });
  }
  if (state.status !== "ready") {
    return res.status(409).json({ error: `Session is not ready: ${state.status}` });
  }

  try {
    const result = await state.client.sendMessage(`${digits}@c.us`, body);
    return res.json({ id: result.id?._serialized || "", message_id: result.id?._serialized || "" });
  } catch (error) {
    console.warn(`[whatsapp:${state.name}] send failed, self-healing:`, error?.message || error);
    await restartClient(state, { reason: "send_failure" });
    const recovered = await waitForReady(state, SEND_RECOVERY_TIMEOUT_MS);
    if (!recovered) {
      return res.status(500).json({ error: error?.message || String(error) });
    }
    try {
      const result = await state.client.sendMessage(`${digits}@c.us`, body);
      return res.json({
        id: result.id?._serialized || "",
        message_id: result.id?._serialized || "",
        self_healed: true
      });
    } catch (error2) {
      return res.status(500).json({ error: error2?.message || String(error2) });
    }
  }
});

app.listen(PORT, "0.0.0.0", () => {
  console.log(`WhatsApp bridge listening on 0.0.0.0:${PORT}`);
});

// Proactively probes every connected session so a detached/stuck browser
// gets self-healed before a real customer notification ever hits it,
// instead of only reacting after a send has already failed.
setInterval(async () => {
  for (const state of sessions.values()) {
    if (state.status !== "ready" || state.restarting) {
      continue;
    }
    try {
      await Promise.race([
        state.client.getState(),
        new Promise((_, reject) => setTimeout(() => reject(new Error("healthcheck timeout")), HEALTH_CHECK_TIMEOUT_MS))
      ]);
    } catch (error) {
      console.warn(`[whatsapp:${state.name}] healthcheck failed, self-healing:`, error?.message || error);
      await restartClient(state, { reason: "healthcheck" });
    }
  }
}, HEALTH_CHECK_INTERVAL_MS);
