import pkg from "whatsapp-web.js";
const { Client, LocalAuth } = pkg;

const client = new Client({
  authStrategy: new LocalAuth({
    clientId: "main",
    dataPath: "/home/seradmin/anna/whatsapp_bridge/sessions"
  }),
  puppeteer: {
    headless: true,
    executablePath: "/usr/bin/google-chrome",
    args: ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
  },
});

client.on("qr", async (qr) => {
  console.log("QR fired, calling requestPairingCode...");
  try {
    // Try to get more error details from the page
    const result = await client.pupPage.evaluate(async (phone) => {
      try {
        const api = window.require("WAWebAltDeviceLinkingApi");
        console.log("API keys:", Object.keys(api));
        api.setPairingType("ALT_DEVICE_LINKING");
        await api.initializeAltDeviceLinking();
        const code = await api.startAltLinkingFlow(phone, false);
        return { ok: true, code };
      } catch(e) {
        return { ok: false, error: e?.message || String(e), name: e?.name, stack: e?.stack };
      }
    }, "34643996431");
    console.log("Result:", JSON.stringify(result, null, 2));
  } catch(e) {
    console.log("Outer error:", e?.message || e);
  }
  await client.destroy();
  process.exit(0);
});

client.on("ready", () => { console.log("READY"); });
client.initialize().catch(e => console.error("init error:", e?.message));
setTimeout(() => { console.log("Timeout"); process.exit(1); }, 120000);
