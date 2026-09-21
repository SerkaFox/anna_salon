import express from "express";
import qrcode from "qrcode";
import pino from "pino";
import fs from "fs";
import path from "path";
import https from "https";
import { fileURLToPath } from "url";

let makeWASocket, useMultiFileAuthState, DisconnectReason, jidNormalizedUser, isJidUser, getAggregateVotesInPollMessage;
try {
  const baileys = await import("@whiskeysockets/baileys");
  makeWASocket = baileys.default;
  useMultiFileAuthState = baileys.useMultiFileAuthState;
  DisconnectReason = baileys.DisconnectReason;
  jidNormalizedUser = baileys.jidNormalizedUser;
  isJidUser = baileys.isJidUser;
  getAggregateVotesInPollMessage = baileys.getAggregateVotesInPollMessage;
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
const BUTTON_REPLY_WEBHOOK_URL = process.env.WHATSAPP_BUTTON_REPLY_WEBHOOK_URL || "";
const AUTH_BASE = process.env.WHATSAPP_AUTH_DATA_PATH
  ? path.resolve(process.env.WHATSAPP_AUTH_DATA_PATH)
  : path.join(__dirname, "sessions");

// Maps a client's phone (digits) to the pending decline/keep button ids of the
// last reminder/confirmation sent to them, so an incoming reply (text or poll
// vote) can be forwarded to Django's button_reply_webhook.
const pendingReplies = new Map();
// Maps a sent poll message id to its full WAMessage + accumulated vote updates,
// required by Baileys to decrypt later poll votes.
const pollMessages = new Map();

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
    // Baileys needs the original message back (poll creation, in particular)
    // to decrypt things like incoming poll votes internally — without this
    // it silently can't, which is why pollUpdateMessage.vote arrived as raw
    // ciphertext instead of a decrypted selectedOptions list.
    getMessage: async (key) => {
      const record = key?.id ? pollMessages.get(key.id) : null;
      return record?.message?.message || undefined;
    },
  });

  state.sock = sock;

  sock.ev.on("creds.update", saveCreds);

  sock.ev.on("messages.upsert", async ({ messages }) => {
    for (const msg of messages || []) {
      try {
        await handleIncomingMessage(state, sock, msg);
      } catch (error) {
        console.warn(`[whatsapp:${state.name}] handleIncomingMessage error:`, error?.message || error);
      }
    }
  });

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

// Returns { exists, lid }. `lid` is the contact's linked-device pseudo-jid —
// onWhatsApp() itself doesn't report one, but Baileys keeps its own LID<->PN
// table (needed for its own session routing) that we can query directly.
// Replies/votes from a contact sometimes arrive addressed via this LID
// instead of their phone number, so callers should register pending-reply
// mappings under it up front, and can even send straight to it.
async function checkOnWhatsApp(sock, phone) {
  const digits = String(phone).replace(/\D/g, "");
  let exists = true;
  try {
    const results = await sock.onWhatsApp(`${digits}@s.whatsapp.net`);
    console.log(`[bridge] onWhatsApp(${digits}) raw:`, JSON.stringify(results));
    exists = Boolean(results?.some(r => r.exists));
  } catch (error) {
    console.warn(`[bridge] onWhatsApp(${phone}) check failed:`, error?.message || error);
  }

  let lid = "";
  try {
    const lidStore = sock?.signalRepository?.lidMapping;
    const resolved = await lidStore?.getLIDForPN?.(`${digits}@s.whatsapp.net`);
    console.log(`[bridge] getLIDForPN(${digits}@s.whatsapp.net) ->`, resolved);
    if (resolved) lid = String(resolved).split(":")[0].replace(/@.*/, "") + "@lid";
  } catch (error) {
    console.warn(`[bridge] getLIDForPN(${digits}) failed:`, error?.message || error);
  }

  return { exists, lid };
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

// ---- incoming reply handling (text replies + poll votes) ----

function isExplicitDeclineReply(value) {
  const normalized = String(value || "")
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLocaleLowerCase("es")
    .replace(/[.!?,;:]+$/g, "")
    .trim()
    .replace(/\s+/g, " ");
  return new Set(["no", "no voy", "no quiero", "no puedo", "no puedo ir"]).has(normalized);
}

// Extracts the decline/keep button ids Django embeds in poll/button payloads,
// e.g. "confirm_decline_42" / "decline_42" / "attend_42" / "keep_booking_42".
function extractBookingButtons(buttons) {
  const find = (prefixes) => {
    for (const button of buttons) {
      const id = String(button.id || "");
      if (prefixes.some((prefix) => id.startsWith(prefix))) {
        return { id, label: String(button.body || "") };
      }
    }
    return { id: "", label: "" };
  };
  const decline = find(["confirm_decline_", "decline_"]);
  const keep = find(["attend_", "keep_booking_"]);
  return {
    declineButtonId: decline.id,
    declineButtonLabel: decline.label,
    keepButtonId: keep.id,
    keepButtonLabel: keep.label,
  };
}

function postJson(urlString, payload) {
  return new Promise((resolve, reject) => {
    const url = new URL(urlString);
    const data = JSON.stringify(payload);
    const req = https.request(
      {
        hostname: url.hostname,
        port: url.port || (url.protocol === "https:" ? 443 : 80),
        path: url.pathname + url.search,
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Content-Length": Buffer.byteLength(data),
          ...(TOKEN ? { Authorization: `Bearer ${TOKEN}` } : {}),
        },
      },
      (res) => {
        let body = "";
        res.on("data", (chunk) => { body += chunk; });
        res.on("end", () => resolve({ statusCode: res.statusCode, body }));
      }
    );
    req.on("error", reject);
    req.write(data);
    req.end();
  });
}

// Forwards a captured decline (or keep) button id to Django, exactly as a
// native WhatsApp button tap would have. Idempotent per mapping.
async function postButtonReply(state, mapping, buttonId) {
  if (!BUTTON_REPLY_WEBHOOK_URL || mapping.handled || !buttonId) return false;
  mapping.handled = true;
  clearReplyMapping(mapping);
  console.log(`[whatsapp:${state.name}] captured client reply phone=${mapping.toDigits} button_id=${buttonId}`);
  try {
    const { statusCode, body } = await postJson(BUTTON_REPLY_WEBHOOK_URL, {
      session: state.name,
      from_phone: mapping.toDigits,
      button_id: buttonId,
    });
    console.log(`[whatsapp:${state.name}] button_reply_webhook status=${statusCode} body=${body}`);
  } catch (error) {
    console.warn(`[whatsapp:${state.name}] button_reply_webhook error:`, error?.message || error);
  }
  return true;
}

// Records what a client should be able to reply (text or poll vote) to, right
// after a poll/buttons message asking for attendance/decline was sent to them.
//
// WhatsApp sometimes addresses a contact's replies via a LID (a linked-device
// pseudo-id) instead of their real phone number, even in a plain 1:1 chat —
// this affected the old whatsapp-web.js bridge too. Keying only by phone
// digits then silently misses every reply from such a chat, so we also key
// by the exact remoteJid Baileys used to send, and match incoming replies
// against both.
function registerReplyMapping(digits, buttons, messageId, sentMessage, lid) {
  const { declineButtonId, declineButtonLabel, keepButtonId, keepButtonLabel } = extractBookingButtons(buttons);
  if (!declineButtonId) return;
  const lidJid = lid ? (lid.includes("@") ? lid : `${lid}@lid`) : "";
  const mapping = {
    toDigits: digits,
    remoteJid: sentMessage?.key?.remoteJid || "",
    lidJid,
    declineButtonId,
    declineButtonLabel,
    keepButtonId,
    keepButtonLabel,
    handled: false,
  };
  pendingReplies.set(digits, mapping);
  if (mapping.remoteJid) pendingReplies.set(mapping.remoteJid, mapping);
  if (lidJid) pendingReplies.set(lidJid, mapping);
  if (messageId && sentMessage) {
    // Incoming vote updates reference the poll via the LID jid when the
    // contact has one, even though we sent it to their phone-based jid.
    // Store a copy addressed the same way votes will be, since Baileys'
    // vote decryption likely uses the poll's own key.remoteJid as context.
    const pollMessage = lidJid
      ? { ...sentMessage, key: { ...sentMessage.key, remoteJid: lidJid } }
      : sentMessage;
    pollMessages.set(messageId, { message: pollMessage, updates: [] });
  }
}

function lookupReplyMapping(remoteJid, digits) {
  return pendingReplies.get(remoteJid) || pendingReplies.get(digits) || null;
}

// Baileys keeps its own LID<->phone-number mapping internally (it needs it to
// route/decrypt sessions correctly), even though onWhatsApp() doesn't surface
// it. If a reply arrives via a LID jid we've never registered, ask that store
// directly before giving up — logs whatever shape it finds either way, since
// the exact method name isn't confirmed for this Baileys version yet.
async function resolveMapping(sock, remoteJid, digits) {
  const direct = lookupReplyMapping(remoteJid, digits);
  if (direct || !remoteJid.endsWith("@lid")) return direct;

  const lidStore = sock?.signalRepository?.lidMapping;
  if (!lidStore) {
    console.log(`[bridge] no signalRepository.lidMapping available to resolve ${remoteJid}`);
    return null;
  }
  console.log(
    `[bridge] lidMapping methods:`,
    Object.getOwnPropertyNames(Object.getPrototypeOf(lidStore)).join(",")
  );
  for (const method of ["getPNForLID", "getPNForLid", "getPnForLid", "getPNForLIDSync"]) {
    if (typeof lidStore[method] !== "function") continue;
    try {
      const pnJid = await lidStore[method](remoteJid);
      console.log(`[bridge] ${method}(${remoteJid}) ->`, pnJid);
      if (pnJid) {
        // pnJid looks like "34607025851:0@s.whatsapp.net" — the ":0" device
        // suffix must be dropped before the colon, not stripped digit-by-digit
        // (that would splice its "0" onto the real number).
        const pnDigits = String(pnJid).split("@")[0].split(":")[0].replace(/\D/g, "");
        const mapping = lookupReplyMapping(pnJid, pnDigits);
        if (mapping) return mapping;
      }
    } catch (error) {
      console.warn(`[bridge] ${method} failed:`, error?.message || error);
    }
  }
  return null;
}

function clearReplyMapping(mapping) {
  pendingReplies.delete(mapping.toDigits);
  if (mapping.remoteJid) pendingReplies.delete(mapping.remoteJid);
  if (mapping.lidJid) pendingReplies.delete(mapping.lidJid);
}

async function handleIncomingMessage(state, sock, msg) {
  if (!msg?.message || msg.key?.fromMe) return;
  const remoteJid = msg.key?.remoteJid || "";
  if (!remoteJid || remoteJid.endsWith("@g.us") || remoteJid === "status@broadcast") return;

  // Ignore anything older than a few minutes (reconnect backfill/history sync).
  const messageTs = Number(msg.messageTimestamp || 0) * 1000;
  if (messageTs && Date.now() - messageTs > 5 * 60 * 1000) return;

  const digits = remoteJid.split("@")[0].split(":")[0].replace(/\D/g, "");

  let content = msg.message;
  if (content.ephemeralMessage) content = content.ephemeralMessage.message;
  if (content.viewOnceMessage) content = content.viewOnceMessage.message;
  if (content.viewOnceMessageV2) content = content.viewOnceMessageV2.message;
  if (!content) return;

  const text = content.conversation || content.extendedTextMessage?.text || "";
  if (text) {
    console.log(`[whatsapp:${state.name}] incoming text from ${digits} (jid=${remoteJid}): "${text}"`);
    const mapping = await resolveMapping(sock, remoteJid, digits);
    if (mapping && !mapping.handled && isExplicitDeclineReply(text)) {
      await postButtonReply(state, mapping, mapping.declineButtonId);
    } else if (!mapping) {
      console.log(`[whatsapp:${state.name}] no pending mapping for jid=${remoteJid} digits=${digits}`);
    }
    return;
  }

  if (content.pollUpdateMessage) {
    const pollId = content.pollUpdateMessage.pollCreationMessageKey?.id || "";
    const record = pollId ? pollMessages.get(pollId) : null;
    console.log(`[whatsapp:${state.name}] poll vote update from ${digits} (jid=${remoteJid}) pollId=${pollId} known=${Boolean(record)}`);
    if (!record || !getAggregateVotesInPollMessage) return;
    record.updates.push(content.pollUpdateMessage);
    try {
      const results = getAggregateVotesInPollMessage({
        message: record.message,
        pollUpdates: record.updates,
      });
      console.log(`[whatsapp:${state.name}] poll aggregate pollId=${pollId}:`, JSON.stringify(results));
      const votedOption = (results || []).find(
        (option) => Array.isArray(option.voters) && option.voters.some((voter) => String(voter).includes(digits))
      );
      const mapping = await resolveMapping(sock, remoteJid, digits);
      if (votedOption && mapping && !mapping.handled) {
        const buttonId = votedOption.name === mapping.keepButtonLabel ? mapping.keepButtonId : mapping.declineButtonId;
        await postButtonReply(state, mapping, buttonId);
      }
    } catch (error) {
      console.warn(`[whatsapp:${state.name}] poll decrypt error:`, error?.stack || error);
    }
  }
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
    const { exists } = await checkOnWhatsApp(state.sock, digits);
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

// Poll message (used by Brimoon for appointment confirmations).
//
// This used to send a native WhatsApp poll, but its votes can't be reliably
// read back (see git history: getPNForLID/getMessage/LID-aware caching were
// all tried and the vote still arrives undecryptable) — the same dead end
// the previous whatsapp-web.js bridge hit before deliberately switching to
// plain text. A tappable poll that silently does nothing is worse than no
// poll: it lets a client believe they've declined when nothing happened.
// Send plain text instead; `body` already spells out the exact reply
// phrases, and registerReplyMapping keeps the text-reply path working.
app.post("/messages/poll", async (req, res) => {
  const { session = "main", to, body, buttons } = req.body || {};
  if (!to || !body || !Array.isArray(buttons) || buttons.length < 2) {
    return res.status(400).json({ error: "to, body and at least 2 buttons[] are required." });
  }
  const state = getSession(session);
  if (!readyGuard(state, res)) return;

  const digits = String(to).replace(/\D/g, "");
  try {
    const { exists, lid } = await checkOnWhatsApp(state.sock, digits);
    if (!exists) return res.status(422).json({ error: `Number not registered on WhatsApp: +${digits}` });

    const jid = toJid(digits);
    const msg = await state.sock.sendMessage(jid, { text: String(body) });
    const id = msg?.key?.id || "";
    registerReplyMapping(digits, buttons, id, msg, lid);
    return res.json({ id, message_id: id });
  } catch (error) {
    console.error(`[whatsapp:${state.name}] poll error:`, error?.message);
    return res.status(500).json({ error: error?.message || String(error) });
  }
});

// Buttons message — sent as plain text (see /messages/poll comment above).
app.post("/messages/buttons", async (req, res) => {
  const { session = "main", to, body, buttons, footer } = req.body || {};
  if (!to || !body || !Array.isArray(buttons)) {
    return res.status(400).json({ error: "to, body and buttons[] are required." });
  }
  const state = getSession(session);
  if (!readyGuard(state, res)) return;

  const digits = String(to).replace(/\D/g, "");
  try {
    const { exists, lid } = await checkOnWhatsApp(state.sock, digits);
    if (!exists) return res.status(422).json({ error: `Number not registered on WhatsApp: +${digits}` });

    const jid = toJid(digits);
    const text = footer ? `${body}\n\n${footer}` : String(body);
    const msg = await state.sock.sendMessage(jid, { text });
    const id = msg?.key?.id || "";
    registerReplyMapping(digits, buttons, id, msg, lid);
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
