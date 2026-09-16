import test from "node:test";
import assert from "node:assert/strict";
import { deadline, guardPageBindings, recoveryReason, setLoginMode } from "./resilience.js";

test("healthy QR is not restarted", () => {
  assert.equal(recoveryReason({ status: "qr", statusSince: 0 }, 999999), null);
});
test("code login suppresses QR refresh using the existing captured options object", () => {
  const captured = { phoneNumber: "" };
  const client = { options: { pairWithPhoneNumber: captured } };
  const state = { status: "qr", qr: "old", qrImage: "old" };
  setLoginMode(client, state, "code", "34600000000");
  assert.equal(client.options.pairWithPhoneNumber, captured);
  assert.equal(captured.phoneNumber, "34600000000");
  assert.equal(state.status, "pairing");
  assert.equal(state.qr, "");
  assert.equal(recoveryReason(state, 999999), null);
  setLoginMode(client, state, "qr");
  assert.equal(captured.phoneNumber, "");
  assert.equal(state.authMode, "qr");
});
test("faulted QR and stalled initialization recover", () => {
  assert.equal(recoveryReason({ status: "qr", faultAt: 1 }, 20000), "browser_fault");
  assert.equal(recoveryReason({ status: "starting", statusSince: 1 }, 130000), "initialization_stuck");
});
test("active pairing and recovery are not interrupted", () => {
  assert.equal(recoveryReason({ status: "error", pairingBusy: true }), null);
  assert.equal(recoveryReason({ status: "error", restarting: true }), null);
});
test("deadline rejects a hung operation", async () => {
  await assert.rejects(deadline(new Promise(() => {}), 10, "pairing"), /pairing timed out/);
});
test("concurrent registration exposes one binding", async () => {
  let calls = 0;
  const page = { exposeFunction: async () => { calls++; await new Promise(r => setTimeout(r, 10)); } };
  guardPageBindings(page);
  await Promise.all([page.exposeFunction("qr", () => {}), page.exposeFunction("qr", () => {})]);
  assert.equal(calls, 1);
});
test("stale binding is replaced without session reset", async () => {
  let calls = 0, removed = 0;
  const page = {
    exposeFunction: async () => { if (++calls === 1) throw new Error("binding already exists"); },
    removeExposedFunction: async () => { removed++; },
  };
  guardPageBindings(page);
  await page.exposeFunction("qr", () => {});
  assert.equal(calls, 2);
  assert.equal(removed, 1);
});
