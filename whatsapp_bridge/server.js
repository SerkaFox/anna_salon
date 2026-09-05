import express from "express";
import qrcode from "qrcode";
import pkg from "whatsapp-web.js";

// whatsapp-web.js calls requestPairingCode() without await inside initialize(),
// so a WhatsApp API rejection becomes an unhandled promise rejection that would
// crash Node. Catch it here so the bridge stays alive.
process.on("unhandledRejection", (reason) => {
  console.error("[bridge] unhandled rejection (suppressed crash):", reason?.message || reason);
});

const { Client, LocalAuth, Buttons, List, Poll } = pkg;

// Maps poll message ID / destination phone to the action payload. WhatsApp Web
// does not reliably emit vote_update for LID-addressed chats, so each sent poll
// also gets a lightweight results watcher.
const pollMappings = new Map();

const PORT = Number(process.env.WHATSAPP_BRIDGE_PORT || 8125);
const TOKEN = process.env.WHATSAPP_BRIDGE_TOKEN || "";
const CHROME_PATH = process.env.WHATSAPP_CHROME_PATH || "";
const BUTTON_REPLY_WEBHOOK_URL = process.env.WHATSAPP_BUTTON_REPLY_WEBHOOK_URL || "";

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

  // WhatsApp Web has no public "stop poll" method. Deleting the poll for
  // everyone removes the voting controls immediately after the first answer.
  if (mapping.messageId) {
    try {
      const pollMessage = await state.client.getMessageById(mapping.messageId);
      if (pollMessage) {
        await pollMessage.delete(true);
        console.log(`[whatsapp:${state.name}] closed poll for booking ${mapping.bookingId}`);
      }
    } catch (error) {
      console.warn(`[whatsapp:${state.name}] could not close poll for booking ${mapping.bookingId}:`, error?.message || error);
    }
  }
  return true;
}

async function recoverSentPoll(state, chatId, body, directResult, sentAfter) {
  if (directResult?.id?._serialized) return directResult;
  for (let attempt = 0; attempt < 6; attempt += 1) {
    if (attempt > 0) await sleep(500);
    try {
      const chat = await state.client.getChatById(chatId);
      const messages = chat ? await chat.fetchMessages({ limit: 20 }) : [];
      const poll = [...messages].reverse().find((message) => (
        message.fromMe
        && message.type === "poll_creation"
        && (message.body === body || message.pollName === body)
        && (!sentAfter || Number(message.timestamp || 0) * 1000 >= sentAfter - 5000)
      ));
      if (poll?.id?._serialized) return poll;
    } catch (error) {
      if (attempt === 5) {
        console.warn(`[whatsapp:${state.name}] could not recover sent poll id:`, error?.message || error);
      }
    }
  }
  return null;
}

async function watchPollVotes(state, mapping) {
  if (!mapping.messageId) return;
  const expiresAt = Date.now() + 24 * 60 * 60 * 1000;
  let errorCount = 0;
  while (!mapping.handled && Date.now() < expiresAt) {
    await sleep(2000);
    if (state.status !== "ready") continue;
    try {
      const votes = await state.client.getPollVotes(mapping.messageId);
      const vote = [...votes]
        .filter((item) => item.selectedOptions?.length)
        .sort((left, right) => Number(right.interractedAtTs || 0) - Number(left.interractedAtTs || 0))[0];
      const selectedName = vote?.selectedOptions?.[0]?.name || "";
      if (selectedName) await postButtonReply(state, mapping, selectedName);
    } catch (error) {
      errorCount += 1;
      if (errorCount === 1 || errorCount % 30 === 0) {
        console.warn(`[whatsapp:${state.name}] poll watcher error for booking ${mapping.bookingId}:`, error?.message || error);
      }
    }
  }
  if (mapping.messageId) pollMappings.delete(mapping.messageId);
  if (pollMappings.get(`phone_${mapping.toDigits}`) === mapping) {
    pollMappings.delete(`phone_${mapping.toDigits}`);
  }
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
    puppeteer,
    // Pin to the latest locally-cached WhatsApp Web version so initialize()
    // doesn't stall trying to download the outdated default (2.3000.1017054665).
    webVersion: "2.3000.1045866108",
    webVersionCache: { type: "local" },
  });
}

function attachClientEvents(client, state) {
  client.on("qr", async (qr) => {
    state.qr = qr;
    state.qrImage = await qrcode.toDataURL(qr);
    state.status = "qr";
    state.lastError = "";

    // If a pairing code was requested, call requestPairingCode now that the
    // page is in the correct auth-needed state (qr event confirms this).
    if (state.pairingPhone) {
      const phone = state.pairingPhone;
      state.pairingPhone = null; // clear now so subsequent QR events don't re-trigger
      console.log(`[whatsapp:${state.name}] QR ready — requesting pairing code for ${phone}`);
      try {
        const code = await client.requestPairingCode(phone);
        state.pairingCode = code;
        console.log(`[whatsapp:${state.name}] pairing code: ${code}`);
      } catch (err) {
        console.error(`[whatsapp:${state.name}] requestPairingCode error:`, err?.message || err);
        state.lastError = err?.message || String(err);
      }
    }
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
    state.authenticatedAt = Date.now();
  });

  client.on("auth_failure", (message) => {
    state.status = "auth_failure";
    state.lastError = String(message || "Authentication failed");
  });

  client.on("disconnected", (reason) => {
    state.status = "disconnected";
    state.lastError = String(reason || "");
  });

  client.on("code", (code) => {
    state.pairingCode = code;
    console.log(`[whatsapp:${state.name}] pairing code received: ${code}`);
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
    pairingPhone: null,
    authenticatedAt: null,
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
  state.pairingCode = null;

  const client = buildClient(state.name);
  attachClientEvents(client, state);
  state.client = client;

  client.initialize().catch((error) => {
    state.status = "error";
    state.lastError = error?.message || String(error);
    console.error(`[whatsapp:${state.name}] initialize error:`, state.lastError);
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
    const waState = await state.client.getState();
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

// Poll message — works on personal and business accounts. buttons[] is the
// same format as /messages/buttons so Django can reuse the same payload.
// The button labels are shown as poll options; their exact IDs are retained for
// the Django webhook. Results are read both from vote_update and by a watcher.
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
  const pollOptions = options.map((option) => option.body);
  // Extract booking ID from first button id, e.g. "attend_42" → 42
  const bookingId = String(rawButtons[0]?.id || "").split("_")[1] || "";

  const pollMsg = new Poll(body, pollOptions, { allowMultipleAnswers: false });
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
    const result = await state.client.sendMessage(primed.serialized, pollMsg, { sendSeen: false });
    const pollMessage = await recoverSentPoll(state, primed.serialized, body, result, sentAt);
    const msgId = pollMessage?.id?._serialized || "";
    if (bookingId) {
      const mapping = {
        bookingId,
        options,
        toDigits: digits,
        messageId: msgId,
        sentAt,
        handled: false,
      };
      if (msgId) pollMappings.set(msgId, mapping);
      pollMappings.set(`phone_${digits}`, mapping);
      console.log(`[whatsapp:${state.name}] poll sent to ${digits}, booking=${bookingId} msgId=${msgId || "(empty)"}`);
      void watchPollVotes(state, mapping).catch((error) => {
        console.warn(`[whatsapp:${state.name}] poll watcher stopped for booking ${bookingId}:`, error?.message || error);
      });
    } else {
      console.log(`[whatsapp:${state.name}] poll sent to ${digits}, msgId=${msgId || "(empty)"}`);
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
  state.pairingPhone = null;
  state.pairingCode = null;
  // Soft-restart to get a clean WhatsApp Web page in pure QR mode.
  await restartClient(state, { wipeAuth: false, reason: "cancel_pairing" });
  res.json({ ok: true, status: state.status });
});

app.post("/sessions/:session/pairing-code", async (req, res) => {
  const sessionName = normalizeSession(req.params.session);
  const phone = String(req.body?.phone || "").replace(/\D/g, "");
  if (!phone) return res.status(400).json({ error: "phone is required" });

  const state = getSession(sessionName);
  if (state.status === "ready") return res.json({ code: null, note: "already connected" });

  // Clear any stale code and set the phone so the qr event handler fires requestPairingCode.
  state.pairingCode = null;
  state.pairingPhone = phone;

  // If already in QR state, call requestPairingCode immediately (page is ready).
  if (state.status === "qr" && state.client) {
    console.log(`[whatsapp:${sessionName}] already in QR state — requesting pairing code for ${phone}`);
    state.client.requestPairingCode(phone).then((code) => {
      state.pairingCode = code;
      state.pairingPhone = null; // clear so QR events don't re-trigger
      console.log(`[whatsapp:${sessionName}] pairing code: ${code}`);
    }).catch((err) => {
      console.error(`[whatsapp:${sessionName}] requestPairingCode error:`, err?.message || err);
      state.lastError = err?.message || String(err);
    });
  } else if (state.status !== "starting") {
    // Not in a usable state — do a fresh reset so QR event fires.
    await restartClient(state, { wipeAuth: true, reason: "pairing_code_request" });
  }
  // If status is "starting", just wait — the qr event will trigger requestPairingCode.

  // Wait up to 120 s for the code to arrive.
  const deadline = Date.now() + 120000;
  while (Date.now() < deadline) {
    if (state.pairingCode) {
      return res.json({ code: state.pairingCode });
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  return res.status(504).json({ error: "timeout waiting for pairing code" });
});

app.listen(PORT, "0.0.0.0", () => {
  console.log(`WhatsApp bridge listening on 0.0.0.0:${PORT}`);
});

// Proactively probes every connected session so a detached/stuck browser
// gets self-healed before a real customer notification ever hits it,
// instead of only reacting after a send has already failed.
setInterval(async () => {
  for (const state of sessions.values()) {
    if (state.restarting) continue;

    // If stuck in authenticated, check the actual WA state and auto-promote to ready.
    // If stuck for >90s it means initialize() stalled — restart to retry.
    if (state.status === "authenticated" && state.client) {
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
      continue;
    }

    if (state.status !== "ready") continue;

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
