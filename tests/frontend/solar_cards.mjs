// The solar cards, in Node with a minimal stand-in for the browser: drawn from a
// real morning's plan and summary, with and without appliances.
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, "../..");
const check = (what, ok) => {
  if (!ok) {
    console.error("FAILED: " + what);
    process.exit(1);
  }
};
const data = JSON.parse(fs.readFileSync(path.join(HERE, "solar.json"), "utf8"));
const NOW = new Date(data.now).getTime();
const RealDate = Date;
globalThis.Date = class extends RealDate {
  constructor(...a) {
    super(...(a.length ? a : [NOW]));
  }
  static now() {
    return NOW;
  }
};
const registry = new Map();
class Element {
  constructor() {
    this.listeners = {};
  }
  attachShadow() {
    let html = "";
    this.shadowRoot = {
      get innerHTML() {
        return html;
      },
      set innerHTML(v) {
        html = v;
      },
      appendChild() {},
    };
    return this.shadowRoot;
  }
  addEventListener(type, f) {
    (this.listeners[type] = this.listeners[type] || []).push(f);
  }
  dispatchEvent() {}
}
globalThis.HTMLElement = Element;
globalThis.window = {
  customElements: {
    get: (t) => registry.get(t),
    define: (t, c) => registry.set(t, c),
  },
};
globalThis.customElements = window.customElements;
globalThis.document = { querySelector: () => null, createElement: () => ({}) };
eval(
  fs.readFileSync(
    path.join(
      ROOT,
      "custom_components/solar_energy_insights/frontend/solar-energy-insights.js",
    ),
    "utf8",
  ),
);

const simulate = (sd) => ({
  response: { ...data.plan, night: { ...data.plan.night } },
});
const hourly = (q) => {
  const out = {};
  for (const id of q.statistic_ids) {
    const rows = [];
    for (let h = 0; h < 10; h++) {
      const start = new RealDate(q.start_time).getTime() + h * 3600e3;
      const change = id.includes("house")
        ? 1.0
        : id.includes("import")
          ? 0.3
          : id.includes("solar")
            ? 0.5
            : 0;
      rows.push({ start, end: start + 3600e3, change, mean: 50 });
    }
    out[id] = rows;
  }
  return out;
};
const hass = {
  states: data.states,
  config: { currency: "EUR" },
  callWS: async (q) =>
    q.type === "solar_energy_insights/plan"
      ? data.plan
      : q.type === "solar_energy_insights/config"
        ? data.config
        : q.type === "call_service"
          ? simulate(q.service_data)
          : q.type === "recorder/statistics_during_period"
            ? hourly(q)
            : {},
};
const settle = () => new Promise((r) => setTimeout(r, 0));
const text = (h) => h.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");
const draw = async (key, config = {}) => {
  const card = new (registry.get("solar-energy-insights-" + key))();
  card.setConfig(config);
  for (let k = 0; k < 6; k++) {
    card.hass = hass;
    await settle();
  }
  return text(card.shadowRoot.innerHTML);
};
await window.SolarDash.loadConfig(hass);

const TWO = {
  appliances: [
    { name: "Dishwasher", icon: "mdi:dishwasher", kwh: 1.0, hours: 2.5 },
    { name: "Dryer", icon: "mdi:tumble-dryer", kwh: 1.5, hours: 2.0 },
  ],
  running: {
    entity: "sensor.washer_state",
    state: "run",
    name: "Washing machine",
  },
};
check(
  "all four solar cards are defined",
  ["now", "until-sunrise", "best-time", "forecast-trust"].every((k) =>
    registry.has("solar-energy-insights-" + k),
  ),
);

const now = await draw("now");
check(
  "the now card shows the summary's rows",
  now.includes("Battery") && now.includes("Tonight"),
);
check(
  "and own power today from the configured counters",
  now.includes("70% own power today") || now.includes("importing 0.3 kW"),
);

const plain = await draw("until-sunrise");
check(
  "until sunrise without appliances shows the house only",
  plain.includes("House only") && !plain.includes("+ Both"),
);
const two = await draw("until-sunrise", TWO);
check(
  "with two appliances, a row each and both",
  two.includes("+ Dishwasher") &&
    two.includes("+ Dryer") &&
    two.includes("+ Both"),
);
check(
  "the appliance running is named",
  two.includes("Washing machine is running"),
);
check(
  "and their figures",
  two.includes("Dishwasher 1.0 kWh over 2.5 h · Dryer 1.5 kWh over 2.0 h"),
);

const best = await draw("best-time", { appliances: [TWO.appliances[0]] });
check(
  "best time to run shows the appliance",
  best.includes("Dishwasher") && best.includes("1.0 kWh · 2.5 h"),
);
const bestPlain = await draw("best-time");
check(
  "and without appliances only the sun",
  bestPlain.includes("Best time to run") && !bestPlain.includes("Dishwasher"),
);

const trust = await draw("forecast-trust");
check(
  "the forecast card shows its scores",
  trust.includes("Can I trust the forecast?") && trust.includes("±3.1 kWh"),
);
console.log("ok");
