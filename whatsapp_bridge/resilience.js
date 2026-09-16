// No credential deletion belongs in recovery or ordinary pairing.
export async function deadline(promise, ms, label = "operation") {
  let timer;
  try {
    return await Promise.race([promise, new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error(`${label} timed out`)), ms);
    })]);
  } finally { clearTimeout(timer); }
}

export function guardPageBindings(page) {
  if (!page || page.__brimoonBindings) return;
  const registrations = new Map();
  const expose = page.exposeFunction.bind(page);
  page.__brimoonBindings = registrations;
  page.exposeFunction = async (name, fn) => {
    if (registrations.has(name)) return registrations.get(name);
    const pending = (async () => {
      try { await expose(name, fn); }
      catch (error) {
        if (!String(error.message).includes("already exists")) throw error;
        // Puppeteer retains bindings across navigation; replace a stale binding.
        await page.removeExposedFunction(name);
        await expose(name, fn);
      }
    })();
    registrations.set(name, pending);
    try { await pending; }
    catch (error) { registrations.delete(name); throw error; }
  };
}

export function recoveryReason(state, now = Date.now()) {
  if (state.restarting || state.pairingBusy || state.probing) return null;
  if (state.faultAt && now - state.faultAt > 10000) return "browser_fault";
  if (["starting", "authenticated"].includes(state.status)
      && now - state.statusSince > 120000) return "initialization_stuck";
  if (state.status === "error") return "client_error";
  // A healthy unpaired browser must remain open for the customer to scan QR.
  return null;
}

// The library's QR refresh callback reads this option dynamically. Keep it in
// sync when switching an existing browser to phone-code linking.
export function setLoginMode(client, state, mode, phone = "") {
  client.options.pairWithPhoneNumber ||= {};
  Object.assign(client.options.pairWithPhoneNumber, {
    phoneNumber: mode === "code" ? phone : "",
    showNotification: true,
    intervalMs: 180000,
  });
  state.authMode = mode;
  state.loginPhone = mode === "code" ? phone : "";
  state.pairingCode = null;
  state.pairingCodeAt = null;
  if (mode === "code") {
    state.qr = "";
    state.qrImage = "";
    state.status = "pairing";
  }
}
