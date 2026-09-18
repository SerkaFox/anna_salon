import express from "express";
import qrcode from "qrcode";
import pino from "pino";
import fs from "fs";
import path from "path";
import https from "https";
import { fileURLToPath } from "url";

let makeWASocket, useMultiFileAuthState, DisconnectReason, jidNormalizedUser, isJidUser;
try {
  const baileys = await import("@whiskeysockets/baileys");
  makeWASocket = baileys.default;
  useMultiFileAuthState = baileys.useMultiFileAuthState;
  DisconnectReason = baileys.DisconnectReason;
  jidNormalizedUser = baileys.jidNormalizedUser;
  isJidUser = baileys.isJidUser;
} catch (e) {
  console.error("[bridge] Baileys not installed:", e.message);
  process.exit(1);
}

process.on("unhandledRejection", (reason) => {
  console.error("[bridge] unhandledRejection:", reason?.message || reason);
});

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PORT = Number(process.env.WHATSAPP_BRIDGE_PORT || 8125);
const TOKEN = process.env.WHATSAPP_BRIDGE_TOKEN || "";
const AUTH_BASE = process.env.WHATSAPP_AUTH_DATA_PATH
  ? path.resolve(process.env.WHATSAPP_AUTH_DATA_PATH)
  : path.join(__dirname, "sessions");

const RECONNECT_DELAY_MS = 5000;
const LOGOUT_RECONNECT_DELAY_MS = 3000;
const logger = pino({ level: "silent" });

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

// ---- state ----

function makeState(name) {
  return {
    name,
    status: "starting",   // starting|qr|pairing|authenticated|ready|error|disconnected
    phone: "",
    readyAt: null,
    lastError: "",
    restartCount: 0,
    pairingBusy: false,
    authMode: "qr",       // qr | code
    loginPhone: "",
    pairingCode: null,
    pairingCodeAt: null,
    faultAt: null,
    statusSince: Date.now(),
    qr: "",
    qrImage: "",
    sock: null,
    restarting: false,
    wsUp: false,
    _reconnectTimer: null,
  };
}

function setStatus(state, status) {
  state.status = status;
  state.statusSince = Date.now();
  console.log(`[whatsapp:${state.name}] status=${status}`);
}

function publicState(state) {
  return {
    session: state.name,
    status: state.status,
    phone: state.phone,
    ready_at: state.readyAt,
    error: state.lastError || null,
    restart_count: state.restartCount,
    pairing_busy: state.pairingBusy,
    auth_mode: state.authMode,
    needs_attention: state.status !== "ready",
    fault_at: state.faultAt,
    status_since: state.statusSince,
  };
}

const sessions = new Map();

function getSession(name) {
  const key = String(name || "main").toLowerCase().trim();
  if (!sessions.has(key)) {
    const s = makeState(key);
    sessions.set(key, s);
    connectSession(s);
  }
  return sessions.get(key);
}

// ---- Baileys connection ----

function authDir(state) {
  return path.join(AUTH_BASE, `baileys-${state.name}`);
}

async function connectSession(state) {
  const dir = authDir(state);
  fs.mkdirSync(dir, { recursive: true });

  const { state: authState, saveCreds } = await useMultiFileAuthState(dir);

  const sock = makeWASocket({
    auth: authState,
    logger,
    printQRInTerminal: false,
    syncFullHistory: false,
    generateHighQualityLinkPreview: false,
    connectTimeoutMs: 30000,
    defaultQueryTimeoutMs: 30000,
    retryRequestDelayMs: 2000,
    maxMsgRetryCount: 3,
  });

  state.sock = sock;

  sock.ev.on("creds.update", saveCreds);

  sock.ev.on("connection.update", async (update) => {
    const { connection, lastDisconnect, qr } = update;

    if (qr) {
      state.wsUp = true;
      try { state.qrImage = await qrcode.toDataURL(qr, { margin: 1, scale: 6 }); } catch (_) {}
      state.qr = qr;
      state.authMode = "qr";
      state.lastError = "";
      if (state.status !== "qr") setStatus(state, "qr");
    }

    if (connection === "open") {
      state.wsUp = true;
      state.phone = (sock.user?.id || "").split(":")[0].split("@")[0];
      state.readyAt = new Date().toISOString();
      state.lastError = "";
      state.faultAt = null;
      state.qr = "";
      state.qrImage = "";
      state.pairingBusy = false;
      state.pairingCode = null;
      setStatus(state, "ready");
      console.log(`[whatsapp:${state.name}] event=ready phone=${state.phone}`);
    }

    if (connection === "close") {
      if (state.sock !== sock) return; // stale listener
      const err = lastDisconnect?.error;
      const code = err?.output?.statusCode ?? err?.output?.payload?.statusCode;
      console.log(`[whatsapp:${state.name}] event=disconnected code=${code}`);

      const wasReady = state.status === "ready";
      const didLogOut = code === DisconnectReason.loggedOut;

      if (didLogOut) {
        console.log(`[whatsapp:${state.name}] logged out — clearing auth and reconnecting`);
        state.lastError = "logged_out";
        try { fs.rmSync(authDir(state), { recursive: true, force: true }); } catch (_) {}
        fs.mkdirSync(authDir(state), { recursive: true });
        setStatus(state, "pairing");
        scheduleReconnect(state, LOGOUT_RECONNECT_DELAY_MS);
      } else {
        state.lastError = err?.message || `disconnect_${code}`;
        setStatus(state, wasReady ? "starting" : state.status);
        if (!state.restarting) scheduleReconnect(state, RECONNECT_DELAY_MS);
      }
    }
  });

  return sock;
}

function scheduleReconnect(state, delay) {
  clearTimeout(state._reconnectTimer);
  state._reconnectTimer = setTimeout(async () => {
    if (state.restarting) return;
    state.restarting = true;
    state.restartCount++;
    try {
      if (state.sock) {
        try { state.sock.end(undefined); } catch (_) {}
        state.sock = null;
      }
      state.wsUp = false;
      await sleep(500);
      await connectSession(state);
    } catch (e) {
      console.error(`[whatsapp:${state.name}] reconnect failed:`, e.message);
      setStatus(state, "error");
      state.lastError = e.message;
      scheduleReconnect(state, RECONNECT_DELAY_MS * 2);
    } finally {
      state.restarting = false;
    }
  }, delay);
}

// ---- helpers ----

function toJid(phone) {
  return `${String(phone).replace(/\D/g, "")}@s.whatsapp.net`;
}

async function checkOnWhatsApp(sock, phone) {
  try {
    const digits = String(phone).replace(/\D/g, "");
    const results = await sock.onWhatsApp(`${digits}@s.whatsapp.net`);
    return results?.some(r => r.exists) ?? false;
  } catch (_) {
    return true; // assume exists if check fails
  }
}

// Wait until the socket has received a QR (or connected), meaning WS is up.
async function waitForWsUp(state, timeoutMs = 15000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    if (state.wsUp) return;
    await sleep(300);
  }
  // Proceed anyway — requestPairingCode may still work or fail gracefully
}

// ---- HTTP ----

const app = express();
app.use(express.json({ limit: "1mb" }));

function requireAuth(req, res, next) {
  if (!TOKEN) return next();
  const h = req.get("authorization") || "";
  if (h !== `Bearer ${TOKEN}`) return res.status(401).json({ error: "Unauthorized" });
  return next();
}
app.use(requireAuth);

app.get("/health", (_req, res) => res.json({ ok: true }));

// Status
app.get("/sessions/:session/status", (req, res) => {
  res.json(publicState(getSession(req.params.session)));
});

// Pairing progress (polling)
app.get("/sessions/:session/pairing-progress", (req, res) => {
  const state = getSession(req.params.session);
  res.set("Cache-Control", "no-store");
  res.json({ ...publicState(state), code: state.pairingCode, code_at: state.pairingCodeAt });
});

// QR (legacy endpoint used by dashboard)
app.post("/auth/qr", async (req, res) => {
  const name = String(req.body?.session || "main");
  const state = getSession(name);
  res.json({ ...publicState(state), qr: state.qr || "", qrImage: state.qrImage || "" });
});

// Request pairing code
app.post("/sessions/:session/pairing-code", async (req, res) => {
  const state = getSession(req.params.session);
  const phone = String(req.body?.phone || "").replace(/\D/g, "");
  if (!/^[1-9][0-9]{7,14}$/.test(phone)) {
    return res.status(400).json({ error: "Introduce el número completo con prefijo internacional." });
  }

  if (state.status === "ready") return res.json({ code: null, note: "already connected" });

  if (state.pairingBusy) {
    return res.status(409).json({ error: "Ya hay una solicitud en curso. Espera unos segundos." });
  }

  // Return cached code if still fresh (< 2 min 50 sec)
  if (state.authMode === "code" && state.loginPhone === phone &&
      state.pairingCode && Date.now() - state.pairingCodeAt < 170000) {
    return res.json({ code: state.pairingCode });
  }

  // If no socket or socket is broken, restart
  if (!state.sock || ["error", "disconnected"].includes(state.status)) {
    clearTimeout(state._reconnectTimer);
    if (!state.restarting) {
      state.restarting = true;
      if (state.sock) { try { state.sock.end(undefined); } catch (_) {} state.sock = null; }
      await connectSession(state);
      state.restarting = false;
    }
  }

  if (!state.sock) {
    return res.status(503).json({ error: "No hay sesión activa. Inténtalo en un momento." });
  }

  state.pairingBusy = true;
  state.authMode = "code";
  state.loginPhone = phone;
  try {
    console.log(`[whatsapp:${state.name}] pairing request started`);
    await waitForWsUp(state, 12000);
    const code = await state.sock.requestPairingCode(phone);
    console.log(`[whatsapp:${state.name}] pairing code received`);
    state.pairingCode = String(code);
    state.pairingCodeAt = Date.now();
    state.lastError = "";
    if (!["ready", "authenticated"].includes(state.status)) setStatus(state, "pairing");
    console.log(`[whatsapp:${state.name}] pairing request completed`);
    return res.json({ code: state.pairingCode });
  } catch (error) {
    state.lastError = error?.message || String(error);
    console.error(`[whatsapp:${state.name}] pairing code error:`, error?.message);
    return res.status(504).json({ error: "WhatsApp no generó el código. Espera 2-3 minutos y vuelve a intentarlo." });
  } finally {
    state.pairingBusy = false;
  }
});

// Cancel pairing (return to QR mode)
app.post("/sessions/:session/cancel-pairing", (req, res) => {
  const state = getSession(req.params.session);
  state.authMode = "qr";
  state.loginPhone = "";
  state.pairingCode = null;
  res.json({ ok: true, status: state.status });
});

// Reset session (wipe auth)
app.post("/sessions/:session/reset", async (req, res) => {
  const body = req.body || {};
  if (body.confirm !== "DELETE_SAVED_LOGIN") {
    return res.status(400).json({ error: "Explicit DELETE_SAVED_LOGIN confirmation required" });
  }
  const state = getSession(req.params.session);
  if (state.sock) { try { state.sock.end(undefined); } catch (_) {} state.sock = null; }
  clearTimeout(state._reconnectTimer);
  try { fs.rmSync(authDir(state), { recursive: true, force: true }); } catch (_) {}
  fs.mkdirSync(authDir(state), { recursive: true });
  setStatus(state, "starting");
  state.pairingCode = null;
  state.pairingBusy = false;
  state.lastError = "";
  state.restarting = true;
  await connectSession(state);
  state.restarting = false;
  res.json({ ok: true, session: state.name });
});

// Manual restart (keep auth)
app.post("/sessions/:session/restart", async (req, res) => {
  const state = getSession(req.params.session);
  scheduleReconnect(state, 0);
  res.json({ ok: true, session: state.name, status: state.status });
});

// ---- Send messages ----

function readyGuard(state, res) {
  if (state.status !== "ready") {
    res.status(409).json({ error: `Session is not ready: ${state.status}` });
    return false;
  }
  return true;
}

// Text message
app.post("/messages", async (req, res) => {
  const { session = "main", to, body } = req.body || {};
  if (!to || !body) return res.status(400).json({ error: "Both to and body are required." });
  const state = getSession(session);
  if (!readyGuard(state, res)) return;

  const digits = String(to).replace(/\D/g, "");
  try {
    const exists = await checkOnWhatsApp(state.sock, digits);
    if (!exists) return res.status(422).json({ error: `Number not registered on WhatsApp: +${digits}` });

    const jid = toJid(digits);
    const msg = await state.sock.sendMessage(jid, { text: String(body) });
    const id = msg?.key?.id || "";
    return res.json({ id, message_id: id });
  } catch (error) {
    console.error(`[whatsapp:${state.name}] send error:`, error?.message);
    return res.status(500).json({ error: error?.message || String(error) });
  }
});

// Poll message (used by Brimoon for appointment confirmations)
app.post("/messages/poll", async (req, res) => {
  const { session = "main", to, body, buttons } = req.body || {};
  if (!to || !body || !Array.isArray(buttons) || buttons.length < 2) {
    return res.status(400).json({ error: "to, body and at least 2 buttons[] are required." });
  }
  const state = getSession(session);
  if (!readyGuard(state, res)) return;

  const digits = String(to).replace(/\D/g, "");
  try {
    const exists = await checkOnWhatsApp(state.sock, digits);
    if (!exists) return res.status(422).json({ error: `Number not registered on WhatsApp: +${digits}` });

    const jid = toJid(digits);
    const options = buttons.map(b => String(b.body || b.id || b));
    const msg = await state.sock.sendMessage(jid, {
      poll: {
        name: String(body),
        values: options,
        selectableCount: 1,
      },
    });
    const id = msg?.key?.id || "";
    return res.json({ id, message_id: id });
  } catch (error) {
    console.error(`[whatsapp:${state.name}] poll error:`, error?.message);
    return res.status(500).json({ error: error?.message || String(error) });
  }
});

// Buttons message — Baileys sends as text on personal numbers
app.post("/messages/buttons", async (req, res) => {
  const { session = "main", to, body, buttons, footer } = req.body || {};
  if (!to || !body || !Array.isArray(buttons)) {
    return res.status(400).json({ error: "to, body and buttons[] are required." });
  }
  const state = getSession(session);
  if (!readyGuard(state, res)) return;

  const digits = String(to).replace(/\D/g, "");
  try {
    const exists = await checkOnWhatsApp(state.sock, digits);
    if (!exists) return res.status(422).json({ error: `Number not registered on WhatsApp: +${digits}` });

    const jid = toJid(digits);
    // Personal numbers: send as poll (interactive) instead of deprecated buttonsMessage
    const options = buttons.map(b => String(b.body || b.id || b));
    const pollName = footer ? `${body}\n\n${footer}` : String(body);
    const msg = await state.sock.sendMessage(jid, {
      poll: {
        name: pollName,
        values: options,
        selectableCount: 1,
      },
    });
    const id = msg?.key?.id || "";
    return res.json({ id, message_id: id });
  } catch (error) {
    console.error(`[whatsapp:${state.name}] buttons error:`, error?.message);
    return res.status(500).json({ error: error?.message || String(error) });
  }
});

// List message — send as text
app.post("/messages/list", async (req, res) => {
  const { session = "main", to, body } = req.body || {};
  if (!to || !body) return res.status(400).json({ error: "to and body are required." });
  const state = getSession(session);
  if (!readyGuard(state, res)) return;

  const digits = String(to).replace(/\D/g, "");
  try {
    const jid = toJid(digits);
    const msg = await state.sock.sendMessage(jid, { text: String(body) });
    const id = msg?.key?.id || "";
    return res.json({ id, message_id: id });
  } catch (error) {
    return res.status(500).json({ error: error?.message || String(error) });
  }
});

// ---- start ----

// Pre-initialize the "main" session on startup
getSession("main");

app.listen(PORT, "0.0.0.0", () => {
  console.log(`WhatsApp bridge (Baileys) listening on 0.0.0.0:${PORT}`);
});

process.once("SIGTERM", async () => {
  for (const state of sessions.values()) {
    clearTimeout(state._reconnectTimer);
    if (state.sock) { try { state.sock.end(undefined); } catch (_) {} }
  }
  process.exit(0);
});
