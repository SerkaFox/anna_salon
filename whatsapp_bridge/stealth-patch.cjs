// Runs before server.js (via --require). Replaces the puppeteer instance in
// Node's CJS module cache with puppeteer-extra + stealth BEFORE whatsapp-web.js
// loads, so Client.js gets the stealth-patched puppeteer transparently.
const puppeteerExtra = require("puppeteer-extra");
const StealthPlugin = require("puppeteer-extra-plugin-stealth");

puppeteerExtra.use(StealthPlugin());

// Resolve the exact path whatsapp-web.js will require('puppeteer') from.
const puppeteerMain = require.resolve("puppeteer");
require.cache[puppeteerMain] = {
  id: puppeteerMain,
  filename: puppeteerMain,
  loaded: true,
  exports: puppeteerExtra,
  parent: null,
  children: [],
  path: require("path").dirname(puppeteerMain),
};
