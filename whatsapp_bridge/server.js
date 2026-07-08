import express from "express";
import qrcode from "qrcode";
import pkg from "whatsapp-web.js";

const { Client, LocalAuth } = pkg;

const PORT = Number(process.env.WHATSAPP_BRIDGE_PORT || 8125);
const TOKEN = process.env.WHATSAPP_BRIDGE_TOKEN || "";
const CHROME_PATH = process.env.WHATSAPP_CHROME_PATH || "";

const sessions = new Map();

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
    readyAt: null
  };

  const puppeteer = {
    headless: true,
    args: ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
  };
  if (CHROME_PATH) {
    puppeteer.executablePath = CHROME_PATH;
  }

  const client = new Client({
    authStrategy: new LocalAuth({
      clientId: sessionName,
      dataPath: process.env.WHATSAPP_AUTH_DATA_PATH || "./sessions"
    }),
    puppeteer
  });

  state.client = client;
  sessions.set(sessionName, state);

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

  client.initialize().catch((error) => {
    state.status = "error";
    state.lastError = error?.message || String(error);
  });

  return state;
}

function publicState(state) {
  return {
    session: state.name,
    status: state.status,
    phone: state.phone,
    ready_at: state.readyAt,
    error: state.lastError
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

app.post("/messages", async (req, res) => {
  const state = getSession(req.body?.session);
  if (state.status !== "ready") {
    return res.status(409).json({ error: `Session is not ready: ${state.status}` });
  }
  const digits = String(req.body?.to || "").replace(/\D/g, "");
  const body = String(req.body?.body || "");
  if (!digits || !body) {
    return res.status(400).json({ error: "Both to and body are required." });
  }
  try {
    const result = await state.client.sendMessage(`${digits}@c.us`, body);
    return res.json({ id: result.id?._serialized || "", message_id: result.id?._serialized || "" });
  } catch (error) {
    return res.status(500).json({ error: error?.message || String(error) });
  }
});

app.listen(PORT, "127.0.0.1", () => {
  console.log(`WhatsApp bridge listening on 127.0.0.1:${PORT}`);
});
