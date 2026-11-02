import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
const ROOT = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);
let now = 1_000_000;
const timers = [];
const RealDate = Date;
const check = (what, ok) => {
  if (!ok) {
    console.error("FAILED: " + what);
    process.exit(1);
  }
};
globalThis.Date = class extends RealDate {
  constructor(...a) {
    super(...(a.length ? a : [now]));
  }
  static now() {
    return now;
  }
};
globalThis.setTimeout = (f, ms) => timers.push({ at: now + ms, f });
globalThis.window = {};
globalThis.document = { querySelector: () => null };
eval(
  fs.readFileSync(
    path.join(
      ROOT,
      "custom_components/solar_energy_insights/frontend/solar-energy-insights.js",
    ),
    "utf8",
  ),
);
const tick = async (ms) => {
  now += ms;
  for (const t of timers.splice(0)) t.at <= now ? t.f() : timers.push(t);
  await new Promise((r) => setImmediate(r));
};
let mode = "ok",
  calls = 0;
const ready = [];
const hass = {
  callWS: async (q) => {
    if (q.type === "solar_energy_insights/config") return {};
    calls++;
    if (mode === "lost") throw 3;
    return { at: now };
  },
  connection: { addEventListener: (ev, f) => ev === "ready" && ready.push(f) },
};
const ui = () => window.SolarDash.ui({ requestUpdate() {} }, hass, {});
const W = () => window.__sei;
ui().ws("plan", "solar_energy_insights/plan", 60);
await tick(0);
const first = W().plan.data.at;
check("a first answer", !!W().plan.data);
mode = "lost";
await tick(61_000);
ui().ws("plan", "solar_energy_insights/plan", 60);
await tick(0);
check("a lost connection keeps the last answer", W().plan.data.at === first);
check(
  "and says so in words",
  W().plan.error === "the connection to Home Assistant was lost; retrying",
);
check("and retries in 5 s", W().plan.retryS === 5);
const before = calls;
await tick(3000);
ui().ws("plan", "solar_energy_insights/plan", 60);
await tick(0);
check("no retry before 5 s", calls === before);
await tick(2100);
ui().ws("plan", "solar_energy_insights/plan", 60);
await tick(0);
check("a retry after 5 s", calls === before + 1);
check("then waits longer", W().plan.retryS === 10);
mode = "ok";
ready.forEach((f) => f());
ui().ws("plan", "solar_energy_insights/plan", 60);
await tick(0);
check(
  "the connection back retries at once",
  calls === before + 2 &&
    W().plan.error === undefined &&
    W().plan.data.at === now,
);
delete W().other;
mode = "lost";
const u = ui();
u.ws("other", "solar_energy_insights/backtest", 600);
await tick(0);
check(
  "with nothing yet, the tile says what happened",
  u.waiting("other", "the backtest").replace(/<[^>]+>/g, "") ===
    "Could not read the backtest: the connection to Home Assistant was lost; retrying",
);
console.log("ok");
