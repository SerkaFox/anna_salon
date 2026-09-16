import express from "express";
import qrcode from "qrcode";
import pkg from "whatsapp-web.js";
import { deadline, guardPageBindings, recoveryReason, setLoginMode } from "./resilience.js";

// whatsapp-web.js calls requestPairingCode() without await inside initialize(),
// so a WhatsApp API rejection becomes an unhandled promise rejection that would
// crash Node. Catch it here so the bridge stays alive.
process.on("unhandledRejection", (reason) => {
  console.error("[bridge] unhandled rejection:", reason?.stack || reason);
  if (/context|binding|protocol|target closed|session closed/i.test(String(reason?.message || reason))) {
    for (const state of sessions.values()) markFault(state, reason);
  }
});

const { Client, LocalAuth, Buttons, List } = pkg;

// Maps an interactive request by message ID, destination phone and LID chat.
const pollMappings = new Map();

const PORT = Number(process.env.WHATSAPP_BRIDGE_PORT || 8125);
const TOKEN = process.env.WHATSAPP_BRIDGE_TOKEN || "";
const CHROME_PATH = process.env.WHATSAPP_CHROME_PATH || "";
const BUTTON_REPLY_WEBHOOK_URL = process.env.WHATSAPP_BUTTON_REPLY_WEBHOOK_URL || "";

// Self-healing tuning. A "soft" restart recreates the Puppeteer/browser
// client but keeps the LocalAuth session folder on disk, so it reconnects
// without requiring a new QR scan. Only /sessions/:session/reset wipes auth.
const HEALTH_CHECK_INTERVAL_MS = Number(process.env.WHATSAPP_HEALTHCHECK_INTERVAL_MS || 60000);
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

function markFault(state, error) {
  state.lastError = error?.message || String(error);
  state.faultAt ||= Date.now();
  state.qr = "";
  state.qrImage = "";
  console.error(`[whatsapp:${state.name}] browser fault:`, error?.stack || error);
}

function initializeClient(client, state) {
  const inject = client.inject.bind(client);
  client.inject = async (...args) => {
    const page = client.pupPage;
    guardPageBindings(page);
    if (page && !page.__brimoonDiagnostics) {
      page.__brimoonDiagnostics = true;
      page.on("framenavigated", frame => {
        if (frame.parentFrame()) return;
        let path = "unknown";
        try { const url = new URL(frame.url()); path = url.origin + url.pathname; } catch (_) {}
        console.log(`[whatsapp:${state.name}] navigation: ${path} logout_redirect=${frame.url().includes("post_logout=1")}`);
      });
      page.on("error", error => markFault(state, error));
      page.on("close", () => { if (state.client === client && !state.restarting) markFault(state, new Error("browser page closed")); });
    }
    try { return await inject(...args); }
    catch (error) {
      if (state.client === client && !state.restarting) markFault(state, error);
      throw error;
    }
  };
  client.initialize().catch(error => {
    if (state.client !== client) return;
    state.status = "error";
    markFault(state, error);
  });
}

function isExplicitDeclineReply(value) {
  const normalized = String(value || "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLocaleLowerCase("es")
    .replace(/[.!?,;:]+$/g, "")
    .trim()
    .replace(/\s+/g, " ");
  return new Set(["no", "no voy", "no quiero", "no puedo", "no puedo ir"]).has(normalized);
}

async function postButtonReply(state, mapping, selectedName) {
  if (!BUTTON_REPLY_WEBHOOK_URL || mapping.handled || !selectedName) return false;
  const selected = mapping.options.find((option) => option.body === selectedName);
  if (!selected) {
    console.warn(`[whatsapp:${state.name}] unknown poll option for booking ${mapping.bookingId}: ${selectedName}`);
    return false;
  }

  // Claim the first non-empty answer before awaiting network calls. This also
  // prevents a late vote_update event and the watcher from processing twice.
  mapping.handled = true;
  console.log(`[whatsapp:${state.name}] poll vote for booking ${mapping.bookingId}: ${selectedName}`);
  const payload = {
    session: state.name,
    from_phone: mapping.toDigits,
    button_id: selected.id,
    button_text: selectedName,
  };

  try {
    const { default: transport } = await import(BUTTON_REPLY_WEBHOOK_URL.startsWith("https") ? "https" : "http");
    const url = new URL(BUTTON_REPLY_WEBHOOK_URL);
    const data = JSON.stringify(payload);
    const statusCode = await new Promise((resolve, reject) => {
      const request = transport.request({
        hostname: url.hostname,
        port: url.port || (url.protocol === "https:" ? 443 : 80),
        path: url.pathname + url.search,
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Content-Length": Buffer.byteLength(data),
          ...(TOKEN ? { "Authorization": `Bearer ${TOKEN}` } : {}),
        },
      }, (response) => {
        response.resume();
        resolve(response.statusCode || 0);
      });
      request.on("error", reject);
      request.write(data);
      request.end();
    });
    console.log(`[whatsapp:${state.name}] poll webhook status=${statusCode} booking=${mapping.bookingId}`);
  } catch (error) {
    console.warn(`[whatsapp:${state.name}] poll vote webhook error:`, error?.message || error);
  }

  // Keep both the request and the client's reply in the chat as evidence of
  // the explicit cancellation instruction.
  for (const key of [mapping.messageId, `phone_${mapping.toDigits}`, `chat_${mapping.chatId}`]) {
    if (key && pollMappings.get(key) === mapping) pollMappings.delete(key);
  }
  return true;
}

async function recoverSentMessage(state, chatId, body, directResult, sentAfter) {
  if (directResult?.id?._serialized) return directResult.id._serialized;
  for (let attempt = 0; attempt < 6; attempt += 1) {
    if (attempt > 0) await sleep(500);
    try {
      // Read the raw in-page message collection. Chat.fetchMessages() tries to
      // serialize the newly-sent poll and currently throws for LID chats.
      const messageId = await state.client.pupPage.evaluate((targetChatId, pollBody, sentAfterMs) => {
        const { Chat } = window.require("WAWebCollections");
        const { createWid } = window.require("WAWebWidFactory");
        const chat = Chat.get(createWid(targetChatId));
        const messages = chat?.msgs?.getModelsArray?.() || [];
        const sentMessage = [...messages].reverse().find((message) => (
          message.id?.fromMe
          && (message.body === pollBody || message.pollName === pollBody)
          && (!sentAfterMs || Number(message.t || 0) * 1000 >= sentAfterMs - 5000)
        ));
        return sentMessage?.id?._serialized || sentMessage?.id?.toString?.() || "";
      }, chatId, body, sentAfter);
      if (messageId) return messageId;
    } catch (error) {
      if (attempt === 5) {
        console.warn(`[whatsapp:${state.name}] could not recover sent poll id:`, error?.message || error);
      }
    }
  }
  return "";
}

function normalizeSession(value) {
  return String(value || "main").replace(/[^a-zA-Z0-9_-]/g, "") || "main";
}

function buildClient(sessionName) {
  const puppeteer = {
    headless: true,
    protocolTimeout: 30000,
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
    puppeteer,
    // Pin to the latest locally-cached WhatsApp Web version so initialize()
    // doesn't stall trying to download the outdated default (2.3000.1017054665).
    webVersion: "2.3000.1045866108",
    webVersionCache: { type: "local" },
  });
}

function attachClientEvents(client, state) {
  for (const event of ["qr", "ready", "authenticated", "auth_failure", "disconnected"]) {
    client.on(event, detail => {
      if (state.client !== client || (event === "qr" && state.authMode === "code")) return;
      state.statusSince = Date.now();
      console.log(`[whatsapp:${state.name}] event=${event}${event === "disconnected" || event === "auth_failure" ? ` reason=${String(detail)}` : ""}`);
    });
  }
  client.on("qr", async (qr) => {
    if (state.client !== client || state.authMode === "code" || ["ready", "authenticated"].includes(state.status)) return;
    state.qr = qr;
    state.qrImage = await qrcode.toDataURL(qr);
    if (state.client !== client || state.authMode === "code" || ["ready", "authenticated"].includes(state.status)) return;
    state.status = "qr";
    state.lastError = "";

    // If a pairing code was requested, call requestPairingCode now that the
    // page is in the correct auth-needed state (qr event confirms this).
    if (state.pairingPhone && !state.pairingBusy) {
      const phone = state.pairingPhone;
      state.pairingPhone = null; // clear now so subsequent QR events don't re-trigger
      console.log(`[whatsapp:${state.name}] QR ready — requesting pairing code for ${phone}`);
      try {
        const code = await deadline(client.requestPairingCode(phone), 20000, "pairing");
        state.pairingCode = code;
        console.log(`[whatsapp:${state.name}] pairing code issued`);
      } catch (err) {
        console.error(`[whatsapp:${state.name}] requestPairingCode error:`, err?.message || err);
        state.lastError = err?.message || String(err);
      }
    }
  });

  client.on("ready", () => {
    if (state.client !== client) return;
    state.status = "ready";
    state.qr = "";
    state.qrImage = "";
    state.readyAt = new Date().toISOString();
    state.faultAt = null;
    state.lastError = "";
    state.restartCount = 0;
    state.pairingCode = null;
    state.pairingCodeAt = null;
    const info = client.info || {};
    state.phone = info.wid?.user || "";
  });

  client.on("authenticated", () => {
    if (state.client !== client) return;
    state.status = "authenticated";
    state.authenticatedAt = Date.now();
    state.qr = "";
    state.qrImage = "";
    state.pairingCode = null;
  });

  client.on("auth_failure", (message) => {
    if (state.client !== client) return;
    state.status = "auth_failure";
    state.lastError = String(message || "Authentication failed");
    state.pairingCode = null;
    state.pairingCodeAt = null;
  });

  client.on("disconnected", (reason) => {
    if (state.client !== client) return;
    state.status = "disconnected";
    state.lastError = String(reason || "");
    state.pairingCode = null;
    state.pairingCodeAt = null;
    if (!/LOGOUT|UNPAIRED|CONFLICT/i.test(String(reason))) markFault(state, new Error(`Disconnected: ${reason}`));
  });

  client.on("code", (code) => {
    if (state.client !== client || state.authMode !== "code" || ["ready", "authenticated"].includes(state.status)) return;
    state.pairingCode = code;
    state.pairingCodeAt = Date.now();
    state.status = "pairing";
    state.statusSince = Date.now();
    console.log(`[whatsapp:${state.name}] pairing code received`);
  });

  client.on("vote_update", async (vote) => {
    const pollMsgId = vote.parentMsgKey?._serialized || vote.parentMessage?.id?._serialized || "";
    const voterPhone = (vote.voter || "").replace("@c.us", "").replace("@lid", "").replace(/\D/g, "");
    console.log(`[whatsapp:${state.name}] vote_update: voter=${voterPhone} pollMsgId=${pollMsgId} options=${JSON.stringify(vote.selectedOptions?.map(o=>o.name))}`);
    const mapping = (pollMsgId ? pollMappings.get(pollMsgId) : null)
                 || (voterPhone ? pollMappings.get(`phone_${voterPhone}`) : null);
    if (!mapping) {
      console.log(`[whatsapp:${state.name}] vote_update: no mapping for pollMsgId=${pollMsgId} phone=${voterPhone}`);
      return;
    }
    const selectedName = vote.selectedOptions?.[0]?.name || "";
    await postButtonReply(state, mapping, selectedName);
  });

  client.on("message", async (msg) => {
    const isButtonReply = msg.type === "buttons_response";
    const isListReply = msg.type === "list_response";
    const senderId = String(msg.from || "");
    const senderDigits = senderId.replace("@c.us", "").replace("@lid", "").replace(/\D/g, "");
    const mapping = pollMappings.get(`chat_${senderId}`)
      || pollMappings.get(`phone_${senderDigits}`);

    // Only an explicit negative phrase is actionable. Silence and every other
    // reply leave the booking intact for automatic attendance confirmation.
    if (mapping && msg.type === "chat" && isExplicitDeclineReply(msg.body)) {
      const selectedName = mapping.options[1]?.body || "";
      if (selectedName) await postButtonReply(state, mapping, selectedName);
      return;
    }

    if ((!isButtonReply && !isListReply) || !BUTTON_REPLY_WEBHOOK_URL) return;
    const payload = {
      session: state.name,
      from_phone: msg.from?.replace("@c.us", "").replace("@lid", "") || "",
      button_id: isListReply ? (msg.selectedRowId || "") : (msg.selectedButtonId || ""),
      button_text: msg.body || "",
    };
    try {
      const { default: https } = await import(BUTTON_REPLY_WEBHOOK_URL.startsWith("https") ? "https" : "http");
      const url = new URL(BUTTON_REPLY_WEBHOOK_URL);
      const data = JSON.stringify(payload);
      const reqOptions = {
        hostname: url.hostname, port: url.port || (url.protocol === "https:" ? 443 : 80),
        path: url.pathname + url.search, method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Content-Length": Buffer.byteLength(data),
          ...(TOKEN ? { "Authorization": `Bearer ${TOKEN}` } : {}),
        },
      };
      await new Promise((resolve, reject) => {
        const req = https.request(reqOptions, (res) => {
          res.resume();
          resolve(res.statusCode);
        });
        req.on("error", reject);
        req.write(data);
        req.end();
      });
    } catch (err) {
      console.warn(`[whatsapp:${state.name}] button reply webhook error:`, err?.message || err);
    }
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
    restartCount: 0,
    pairingCode: null,
    pairingCodeAt: null,
    authMode: "qr",
    loginPhone: "",
    pairingPhone: null,
    authenticatedAt: null,
    statusSince: Date.now(),
    faultAt: null,
    pairingBusy: false,
    probing: false,
    recentRestarts: [],
  };

  const client = buildClient(sessionName);
  attachClientEvents(client, state);
  state.client = client;
  sessions.set(sessionName, state);

  initializeClient(client, state);

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
    state.recentRestarts = state.recentRestarts.filter(t => now - t < 3600000);
    if (state.recentRestarts.length >= MAX_CONSECUTIVE_RESTARTS) {
      state.lastError = `Recovery limit reached (${MAX_CONSECUTIVE_RESTARTS}/hour); administrator assistance required. Login files preserved.`;
      return;
    }
  }

  state.restarting = true;
  state.lastRestartAt = now;
  state.restartCount += 1;
  state.recentRestarts.push(now);
  console.log(`[whatsapp:${state.name}] restarting client (wipeAuth=${wipeAuth}, reason=${reason || "n/a"})`);

  try {
    if (state.client) {
      await deadline(state.client.destroy(), 10000, "browser shutdown");
    }
  } catch (_) {
    // Only this session's browser is targeted; never a shared process/service.
    const pid = state.client?.pupBrowser?.process()?.pid;
    if (pid) { try { process.kill(pid, "SIGTERM"); } catch (_) {} }
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
  state.pairingCode = null;
  state.pairingCodeAt = null;
  state.pairingPhone = null;
  state.faultAt = null;
  state.statusSince = Date.now();
  state.authenticatedAt = null;

  const client = buildClient(state.name);
  if (state.authMode === "code" && state.loginPhone) {
    setLoginMode(client, state, "code", state.loginPhone);
  }
  attachClientEvents(client, state);
  state.client = client;

  initializeClient(client, state);

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
    restart_count: state.restartCount,
    pairing_busy: state.pairingBusy,
    auth_mode: state.authMode,
    needs_attention: state.status !== "ready",
    fault_at: state.faultAt,
    status_since: state.statusSince
  };
}

const app = express();
app.use(express.json({ limit: "1mb" }));
app.use(requireAuth);

app.get("/health", (req, res) => {
  res.json({ ok: true });
});

app.post("/sessions/:session/debug-pairing", async (req, res) => {
  const state = sessions.get(normalizeSession(req.params.session));
  if (!state?.client?.pupPage) return res.status(404).json({ error: "no page" });
  const phone = String(req.body?.phone || "34643996431").replace(/\D/g, "");
  try {
    const result = await state.client.pupPage.evaluate(async (phone) => {
      try {
        const api = window.require("WAWebAltDeviceLinkingApi");
        const apiKeys = Object.keys(api);
        let stepError = null;
        try { api.setPairingType("ALT_DEVICE_LINKING"); } catch(e) { stepError = "setPairingType: " + e?.message; }
        if (!stepError) {
          try { await api.initializeAltDeviceLinking(); } catch(e) { stepError = "initializeAltDeviceLinking: " + e?.message; }
        }
        if (!stepError) {
          try {
            const code = await api.startAltLinkingFlow(phone, false);
            return { ok: true, code, apiKeys };
          } catch(e) { stepError = "startAltLinkingFlow: " + (e?.message || JSON.stringify(e)); }
        }
        return { ok: false, stepError, apiKeys };
      } catch(e) {
        return { ok: false, error: "require failed: " + e?.message };
      }
    }, phone);
    res.json(result);
  } catch(err) {
    res.status(500).json({ error: err?.message || String(err) });
  }
});

app.get("/sessions/:session/state", async (req, res) => {
  const state = getSession(req.params.session);
  if (!state.client) return res.json({ error: "no client" });
  try {
    const waState = await deadline(state.client.getState(), 5000, "state probe");
    // If WhatsApp is CONNECTED but the bridge event never fired, sync the status.
    if (waState === "CONNECTED" && state.status === "authenticated") {
      state.status = "ready";
      state.readyAt = new Date().toISOString();
      const info = state.client.info || {};
      state.phone = info.wid?.user || "";
      console.log(`[whatsapp:${state.name}] force-synced status to ready (wa_state=CONNECTED)`);
    }
    res.json({ wa_state: waState, bridge_status: state.status });
  } catch (err) {
    res.json({ error: err?.message || String(err), bridge_status: state.status });
  }
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
  if (req.body?.confirm !== "DELETE_SAVED_LOGIN") {
    return res.status(400).json({ error: "Explicit DELETE_SAVED_LOGIN confirmation required" });
  }
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

  // Verify the number is on WhatsApp. getNumberId may return a LID
  // (@lid) address for migrated contacts — we always send to the
  // canonical phone-number chatId (digits@c.us) so that getChat can
  // find or create the conversation regardless of LID migration status.
  let onWhatsApp;
  try {
    onWhatsApp = await state.client.getNumberId(digits);
  } catch (_) {
    onWhatsApp = null;
  }
  if (!onWhatsApp) {
    console.warn(`[whatsapp:${state.name}] number not on WhatsApp: ${digits}`);
    return res.status(422).json({ error: `Number not registered on WhatsApp: +${digits}` });
  }
  const lid = onWhatsApp._serialized;
  console.log(`[whatsapp:${state.name}] sending to ${digits} (lid=${lid})`);

  // WhatsApp Web stores existing chats under LID keys. For new contacts
  // (no chat history), getChat fails on both LID and phone wids. We try
  // the LID first, then fall back to phone, accepting undefined as "sent
  // but ID unresolvable" (LID key mismatch in Msg.get after send).
  async function trySend() {
    // Ensure the chat exists in the local collection before sending.
    // For contacts migrated to LID, getChat(phone) returns null but
    // getChat(lid) finds existing chats. For brand-new contacts,
    // we force-open the chat via pupPage before sending.
    const primed = await state.client.pupPage.evaluate(async (digitsArg, lidArg) => {
      const WAWebWidFactory = window.require("WAWebWidFactory");
      const WAWebFindChatAction = window.require("WAWebFindChatAction");
      const Chat = window.require("WAWebCollections").Chat;

      // Try LID wid first (existing chats are indexed by LID).
      if (lidArg) {
        const lidWid = WAWebWidFactory.createWid(lidArg);
        let chat = Chat.get(lidWid);
        if (chat) return { found: "lid", serialized: lidArg };

        const res = await WAWebFindChatAction.findOrCreateLatestChat(lidWid);
        chat = res?.chat || res;
        if (chat && chat.id) return { found: "lid-created", serialized: chat.id._serialized };
      }

      // Fallback: phone-based wid.
      const phoneWid = WAWebWidFactory.createWid(`${digitsArg}@c.us`);
      let chat = Chat.get(phoneWid);
      if (chat) return { found: "phone", serialized: `${digitsArg}@c.us` };

      const res2 = await WAWebFindChatAction.findOrCreateLatestChat(phoneWid);
      chat = res2?.chat || res2;
      if (chat && chat.id) return { found: "phone-created", serialized: chat.id._serialized };

      return { found: null, serialized: null };
    }, digits, lid);

    console.log(`[whatsapp:${state.name}] chat probe for ${digits}: found=${primed.found} chatId=${primed.serialized}`);

    if (!primed.serialized) {
      return { id: "", message_id: "", error: "chat_not_found" };
    }

    const result = await state.client.sendMessage(primed.serialized, body, { sendSeen: false });
    const msgId = result?.id?._serialized || "";
    console.log(`[whatsapp:${state.name}] sent to ${digits} via ${primed.serialized}, msgId=${msgId || "(lid-keyed)"}`);
    return { id: msgId, message_id: msgId };
  }

  try {
    return res.json(await trySend());
  } catch (error) {
    console.warn(`[whatsapp:${state.name}] send failed, self-healing:`, error?.message || error);
    await restartClient(state, { reason: "send_failure" });
    const recovered = await waitForReady(state, SEND_RECOVERY_TIMEOUT_MS);
    if (!recovered) {
      return res.status(500).json({ error: error?.message || String(error) });
    }
    try {
      return res.json({ ...(await trySend()), self_healed: true });
    } catch (error2) {
      return res.status(500).json({ error: error2?.message || String(error2) });
    }
  }
});

app.post("/messages/buttons", async (req, res) => {
  const state = getSession(req.body?.session);
  const digits = String(req.body?.to || "").replace(/\D/g, "");
  const body = String(req.body?.body || "");
  const rawButtons = Array.isArray(req.body?.buttons) ? req.body.buttons : [];
  const title = String(req.body?.title || "");
  const footer = String(req.body?.footer || "");
  if (!digits || !body || rawButtons.length === 0) {
    return res.status(400).json({ error: "to, body and buttons[] are required." });
  }
  if (state.status !== "ready") {
    return res.status(409).json({ error: `Session is not ready: ${state.status}` });
  }

  let onWhatsApp;
  try {
    onWhatsApp = await state.client.getNumberId(digits);
  } catch (_) { onWhatsApp = null; }
  if (!onWhatsApp) {
    return res.status(422).json({ error: `Number not registered on WhatsApp: +${digits}` });
  }
  const lid = onWhatsApp._serialized;

  const buttonObjects = rawButtons.map((b) => ({ id: String(b.id || b.body), body: String(b.body) }));
  const buttonsMsg = new Buttons(body, buttonObjects, title, footer);

  try {
    const primed = await state.client.pupPage.evaluate(async (digitsArg, lidArg) => {
      const WAWebWidFactory = window.require("WAWebWidFactory");
      const WAWebFindChatAction = window.require("WAWebFindChatAction");
      const Chat = window.require("WAWebCollections").Chat;
      if (lidArg) {
        const lidWid = WAWebWidFactory.createWid(lidArg);
        let chat = Chat.get(lidWid);
        if (chat) return { serialized: lidArg };
        const res = await WAWebFindChatAction.findOrCreateLatestChat(lidWid);
        chat = res?.chat || res;
        if (chat && chat.id) return { serialized: chat.id._serialized };
      }
      const phoneWid = WAWebWidFactory.createWid(`${digitsArg}@c.us`);
      const res2 = await WAWebFindChatAction.findOrCreateLatestChat(phoneWid);
      const chat2 = res2?.chat || res2;
      return { serialized: chat2?.id?._serialized || `${digitsArg}@c.us` };
    }, digits, lid);

    const result = await state.client.sendMessage(primed.serialized, buttonsMsg, { sendSeen: false });
    const msgId = result?.id?._serialized || "";
    console.log(`[whatsapp:${state.name}] sent buttons to ${digits}, msgId=${msgId}`);
    return res.json({ id: msgId, message_id: msgId });
  } catch (error) {
    console.error(`[whatsapp:${state.name}] buttons send error:`, error?.message || error);
    return res.status(500).json({ error: error?.message || String(error) });
  }
});

// List message — works where Buttons are deprecated.
// sections: [{ title, rows: [{ id, title, description }] }]
app.post("/messages/list", async (req, res) => {
  const state = getSession(req.body?.session);
  const digits = String(req.body?.to || "").replace(/\D/g, "");
  const body = String(req.body?.body || "");
  const buttonText = String(req.body?.button_text || "Ver opciones");
  const rawSections = Array.isArray(req.body?.sections) ? req.body.sections : [];
  if (!digits || !body || rawSections.length === 0) {
    return res.status(400).json({ error: "to, body and sections[] are required." });
  }
  if (state.status !== "ready") {
    return res.status(409).json({ error: `Session is not ready: ${state.status}` });
  }
  let onWhatsApp;
  try { onWhatsApp = await state.client.getNumberId(digits); } catch (_) { onWhatsApp = null; }
  if (!onWhatsApp) return res.status(422).json({ error: `Number not registered on WhatsApp: +${digits}` });
  const lid = onWhatsApp._serialized;
  const title = String(req.body?.title || "");
  const footer = String(req.body?.footer || "");
  const listMsg = new List(body, buttonText, rawSections, title, footer);
  try {
    const primed = await state.client.pupPage.evaluate(async (digitsArg, lidArg) => {
      const WAWebWidFactory = window.require("WAWebWidFactory");
      const WAWebFindChatAction = window.require("WAWebFindChatAction");
      const Chat = window.require("WAWebCollections").Chat;
      if (lidArg) {
        const lidWid = WAWebWidFactory.createWid(lidArg);
        let chat = Chat.get(lidWid);
        if (chat) return { serialized: lidArg };
        const r = await WAWebFindChatAction.findOrCreateLatestChat(lidWid);
        chat = r?.chat || r;
        if (chat && chat.id) return { serialized: chat.id._serialized };
      }
      const phoneWid = WAWebWidFactory.createWid(`${digitsArg}@c.us`);
      const r2 = await WAWebFindChatAction.findOrCreateLatestChat(phoneWid);
      const chat2 = r2?.chat || r2;
      return { serialized: chat2?.id?._serialized || `${digitsArg}@c.us` };
    }, digits, lid);
    const result = await state.client.sendMessage(primed.serialized, listMsg, { sendSeen: false });
    const msgId = result?.id?._serialized || "";
    console.log(`[whatsapp:${state.name}] sent list to ${digits}, msgId=${msgId}`);
    return res.json({ id: msgId, message_id: msgId });
  } catch (error) {
    console.error(`[whatsapp:${state.name}] list send error:`, error?.message || error);
    return res.status(500).json({ error: error?.message || String(error) });
  }
});

// Interactive confirmation. Native polls display correctly but their votes are
// currently not synchronized to linked WhatsApp Web devices for LID chats.
// Send a plain prompt; only explicit negative text replies are actionable.
app.post("/messages/poll", async (req, res) => {
  const state = getSession(req.body?.session);
  const digits = String(req.body?.to || "").replace(/\D/g, "");
  const body = String(req.body?.body || "");
  const rawButtons = Array.isArray(req.body?.buttons) ? req.body.buttons : [];
  if (!digits || !body || rawButtons.length < 2) {
    return res.status(400).json({ error: "to, body and at least 2 buttons[] are required." });
  }
  if (state.status !== "ready") {
    return res.status(409).json({ error: `Session is not ready: ${state.status}` });
  }
  let onWhatsApp;
  try { onWhatsApp = await state.client.getNumberId(digits); } catch (_) { onWhatsApp = null; }
  if (!onWhatsApp) return res.status(422).json({ error: `Number not registered on WhatsApp: +${digits}` });
  const lid = onWhatsApp._serialized;

  const options = rawButtons.map((button) => ({
    id: String(button.id || button.body),
    body: String(button.body),
  }));
  // Extract booking ID from first button id, e.g. "attend_42" → 42
  const bookingId = String(rawButtons[0]?.id || "").split("_")[1] || "";

  const replyBody = body;
  try {
    const primed = await state.client.pupPage.evaluate(async (digitsArg, lidArg) => {
      const WAWebWidFactory = window.require("WAWebWidFactory");
      const WAWebFindChatAction = window.require("WAWebFindChatAction");
      const Chat = window.require("WAWebCollections").Chat;
      if (lidArg) {
        const lidWid = WAWebWidFactory.createWid(lidArg);
        let chat = Chat.get(lidWid);
        if (chat) return { serialized: lidArg };
        const r = await WAWebFindChatAction.findOrCreateLatestChat(lidWid);
        chat = r?.chat || r;
        if (chat && chat.id) return { serialized: chat.id._serialized };
      }
      const phoneWid = WAWebWidFactory.createWid(`${digitsArg}@c.us`);
      const r2 = await WAWebFindChatAction.findOrCreateLatestChat(phoneWid);
      const chat2 = r2?.chat || r2;
      return { serialized: chat2?.id?._serialized || `${digitsArg}@c.us` };
    }, digits, lid);

    const sentAt = Date.now();
    const result = await state.client.sendMessage(primed.serialized, replyBody, { sendSeen: false });
    const msgId = await recoverSentMessage(state, primed.serialized, replyBody, result, sentAt);
    if (bookingId) {
      const mapping = {
        bookingId,
        options,
        toDigits: digits,
        chatId: primed.serialized,
        messageId: msgId,
        sentAt,
        handled: false,
      };
      if (msgId) pollMappings.set(msgId, mapping);
      pollMappings.set(`phone_${digits}`, mapping);
      pollMappings.set(`chat_${primed.serialized}`, mapping);
      console.log(`[whatsapp:${state.name}] response request sent to ${digits}, booking=${bookingId} msgId=${msgId || "(empty)"}`);
    } else {
      console.log(`[whatsapp:${state.name}] response request sent to ${digits}, msgId=${msgId || "(empty)"}`);
    }
    return res.json({ id: msgId, message_id: msgId });
  } catch (error) {
    console.error(`[whatsapp:${state.name}] poll send error:`, error?.message || error);
    return res.status(500).json({ error: error?.message || String(error) });
  }
});

// Clears pairingPhone so QR scanning works again (requestPairingCode would
// otherwise deactivate the QR every time it fires).
app.post("/sessions/:session/cancel-pairing", async (req, res) => {
  const sessionName = normalizeSession(req.params.session);
  const state = sessions.get(sessionName);
  if (!state) return res.status(404).json({ error: "session not found" });
  if (state.status === "ready") return res.json({ ok: true, status: "ready" });
  if (state.pairingBusy) return res.status(409).json({ error: "Espera a que termine la solicitud actual." });
  state.pairingPhone = null;
  if (state.status === "authenticated") return res.json({ ok: true, status: state.status });
  setLoginMode(state.client, state, "qr");
  try {
    await deadline((async () => {
      await state.client.cancelPairingCode();
      // A browser restored directly in code mode has no QR listener yet.
      await state.client.inject();
    })(), 20000, "return to QR");
    if (!["ready", "authenticated"].includes(state.status)) state.status = "qr";
    res.json({ ok: true, status: state.status });
  } catch (error) {
    markFault(state, error);
    res.status(503).json({ error: "No se pudo cambiar al QR. Espera un minuto." });
  }
});

app.get("/sessions/:session/pairing-progress", (req, res) => {
  const state = getSession(req.params.session);
  res.set("Cache-Control", "no-store");
  res.json({ ...publicState(state), code: state.pairingCode, code_at: state.pairingCodeAt });
});

app.post("/sessions/:session/pairing-code", async (req, res) => {
  const sessionName = normalizeSession(req.params.session);
  const phone = String(req.body?.phone || "").replace(/\D/g, "");
  if (!/^[1-9][0-9]{7,14}$/.test(phone)) return res.status(400).json({ error: "Introduce el número completo con prefijo internacional." });

  const state = getSession(sessionName);
  if (state.status === "ready") return res.json({ code: null, note: "already connected" });
  if (state.status === "authenticated") return res.status(409).json({ error: "La vinculación está sincronizando. No solicites otro código." });

  if (state.pairingBusy || state.restarting) {
    return res.status(409).json({ error: "Ya hay una solicitud en curso. Espera unos segundos." });
  }
  if (!["qr", "pairing"].includes(state.status) || state.faultAt) {
    if (state.status !== "starting") await restartClient(state, { reason: "pairing_recovery" });
    return res.status(409).json({ error: "La conexión se está recuperando sin borrar el acceso. Espera un minuto y vuelve a intentarlo." });
  }
  state.pairingBusy = true;
  state.pairingPhone = null;
  if (state.authMode === "code" && state.loginPhone === phone && state.pairingCode && Date.now() - state.pairingCodeAt < 170000) {
    state.pairingBusy = false;
    return res.json({ code: state.pairingCode });
  }
  setLoginMode(state.client, state, "code", phone);
  try {
    console.log(`[whatsapp:${sessionName}] pairing request started`);
    const code = await deadline(state.client.requestPairingCode(phone), 20000, "pairing");
    if (["ready", "authenticated"].includes(state.status)) {
      return res.json({ code: null, note: state.status === "ready" ? "already connected" : null });
    }
    state.pairingCode = code;
    state.pairingCodeAt = Date.now();
    state.lastError = "";
    console.log(`[whatsapp:${sessionName}] pairing request completed`);
    return res.json({ code });
  } catch (error) {
    markFault(state, error);
    return res.status(504).json({ error: "WhatsApp no respondió. Se recuperará la conexión sin borrar el acceso. Espera un minuto o utiliza el QR." });
  } finally { state.pairingBusy = false; }
});

app.listen(PORT, "0.0.0.0", () => {
  console.log(`WhatsApp bridge listening on 0.0.0.0:${PORT}`);
});

process.once("SIGTERM", async () => {
  for (const state of sessions.values()) {
    state.restarting = true;
    try { await deadline(state.client.destroy(), 8000, "shutdown"); } catch (_) {}
  }
  process.exit(0);
});

// Proactively probes every connected session so a detached/stuck browser
// gets self-healed before a real customer notification ever hits it,
// instead of only reacting after a send has already failed.
setInterval(async () => {
  for (const state of sessions.values()) {
    if (state.restarting || state.probing || state.pairingBusy) continue;
    const reason = recoveryReason(state);
    if (reason) {
      await restartClient(state, { reason });
      continue;
    }
    if (["qr", "pairing"].includes(state.status)) {
      state.probing = true;
      try {
        const responsive = await deadline(state.client.pupPage.evaluate(() => (
          document.readyState !== "loading" && Boolean(window.Debug?.VERSION)
        )), HEALTH_CHECK_TIMEOUT_MS, "QR browser healthcheck");
        if (!responsive && Date.now() - state.statusSince > 120000) {
          markFault(state, new Error("QR page is not initialized"));
        }
      } catch (error) { markFault(state, error); }
      finally { state.probing = false; }
      continue;
    }

    // If stuck in authenticated, check the actual WA state and auto-promote to ready.
    // If stuck for >90s it means initialize() stalled — restart to retry.
    if (state.status === "authenticated" && state.client) {
      state.probing = true;
      const stuckMs = state.authenticatedAt ? Date.now() - state.authenticatedAt : 0;
      try {
        const waState = await Promise.race([
          state.client.getState(),
          new Promise((_, reject) => setTimeout(() => reject(new Error("timeout")), HEALTH_CHECK_TIMEOUT_MS))
        ]);
        if (waState === "CONNECTED") {
          state.status = "ready";
          state.readyAt = new Date().toISOString();
          const info = state.client.info || {};
          state.phone = info.wid?.user || "";
          console.log(`[whatsapp:${state.name}] healthcheck promoted authenticated → ready`);
        } else if (stuckMs > 90000) {
          console.warn(`[whatsapp:${state.name}] stuck in authenticated for ${Math.round(stuckMs/1000)}s, soft-restarting`);
          await restartClient(state, { reason: "authenticated_stuck" });
        }
      } catch (_) {
        if (stuckMs > 90000) {
          console.warn(`[whatsapp:${state.name}] authenticated getState failed after ${Math.round(stuckMs/1000)}s, soft-restarting`);
          await restartClient(state, { reason: "authenticated_stuck" });
        }
      }
      state.probing = false;
      continue;
    }

    if (state.status !== "ready") continue;
    state.probing = true;
    try {
      const waState = await deadline(state.client.getState(), HEALTH_CHECK_TIMEOUT_MS, "healthcheck");
      if (waState !== "CONNECTED") {
        console.warn(`[whatsapp:${state.name}] actual state=${waState}, bridge was ready`);
        state.status = ["UNPAIRED", "UNPAIRED_IDLE"].includes(waState) ? "qr" : "disconnected";
        state.statusSince = Date.now();
        state.lastError = `WhatsApp state: ${waState}`;
        if (state.status === "disconnected") markFault(state, new Error(state.lastError));
      }
    } catch (error) {
      console.warn(`[whatsapp:${state.name}] healthcheck failed, self-healing:`, error?.message || error);
      await restartClient(state, { reason: "healthcheck" });
    } finally { state.probing = false; }
  }
}, HEALTH_CHECK_INTERVAL_MS);
