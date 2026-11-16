import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
const ROOT = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);
const SCRIPT = fs.readFileSync(
  path.join(
    ROOT,
    "custom_components/solar_energy_insights/frontend/solar-energy-insights.js",
  ),
  "utf8",
);
const check = (what, ok) => {
  if (!ok) {
    console.error("FAILED: " + what);
    process.exit(1);
  }
};
const pad = (n) => String(n).padStart(2, "0");
const iso = (d) =>
  `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const monthsAgo = (k) => {
  const d = new Date();
  d.setDate(1);
  d.setMonth(d.getMonth() - k);
  return iso(d);
};
const CONFIG = {
  entities: { solar: "sensor.po_solar_yield_today", house: "sensor.po_house" },
  prefix: "sensor.po_",
  history_start: monthsAgo(14),
  strings: [],
};

// The browser's store: a plain map that outlives the page
const store = new Map();
const answer = (f) => {
  const r = {};
  setTimeout(() => {
    r.result = f();
    r.onsuccess && r.onsuccess();
  }, 0);
  return r;
};
const db = {
  createObjectStore() {},
  transaction: () => ({
    objectStore: () => ({
      get: (k) => answer(() => store.get(k)),
      put: (v, k) => answer(() => store.set(k, structuredClone(v))),
    }),
  }),
};
globalThis.indexedDB = {
  open() {
    const r = {};
    setTimeout(() => {
      r.result = db;
      r.onupgradeneeded && r.onupgradeneeded();
      r.onsuccess();
    }, 0);
    return r;
  },
};

// A recorder that takes a while and answers one month per request
let busy = 0,
  most = 0;
const months = [];
const hass = {
  states: {},
  callService() {},
  callWS: async (q) => {
    if (q.type === "solar_energy_insights/config") return CONFIG;
    if (q.type !== "recorder/statistics_during_period" || q.period !== "hour")
      return {};
    months.push(q.start_time);
    busy++;
    most = Math.max(most, busy);
    await new Promise((r) => setTimeout(r, 20));
    busy--;
    const at = new Date(q.start_time).getTime();
    return { "sensor.po_solar_yield_today": [{ start: at, change: 5 }] };
  },
};
globalThis.document = { querySelector: () => ({ hass }) };
const visit = async () => {
  globalThis.window = {};
  eval(SCRIPT);
  const SP = window.SolarPeriod;
  SP.ready();
  await new Promise((r) => setTimeout(r, 30));
  const span = [monthsAgo(11), iso(new Date(Date.now() + 864e5))];
  let rows = null;
  for (let k = 0; k < 200 && !rows; k++) {
    rows = SP.daysSync(hass, span[0], span[1], null);
    if (!rows) await new Promise((r) => setTimeout(r, 10));
  }
  return rows;
};

const first = await visit();
check("the year arrives", !!first);
check("at most two requests at once", most <= 2);
check("twelve months asked for", months.length === 12);
check(
  "the newest month first",
  new Date(months[0]) > new Date(months[months.length - 1]),
);

months.length = 0;
const again = await visit();
check("the year arrives again", !!again);
check(
  "months that are over come from the browser, not the recorder",
  months.length <= 2,
);
console.log("ok");
