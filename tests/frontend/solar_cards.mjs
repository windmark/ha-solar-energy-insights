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
    const listeners = {};
    const found = {};
    this.shadowRoot = {
      get innerHTML() {
        return html;
      },
      set innerHTML(v) {
        html = v;
      },
      appendChild() {},
      addEventListener(type, f) {
        (listeners[type] = listeners[type] || []).push(f);
      },
      // An event on an element in the card, as the browser would bubble it.
      fire(type, target) {
        (listeners[type] || []).forEach((f) => f({ type, target }));
      },
      // Only what the scenario lab asks for: its readouts.
      querySelector(sel) {
        const m = /data-out="([^"]+)"/.exec(sel);
        if (!m) return null;
        return (found[m[1]] = found[m[1]] || {
          textContent: "",
          innerHTML: "",
        });
      },
      found,
    };
    return this.shadowRoot;
  }
  addEventListener(type, f) {
    (this.listeners[type] = this.listeners[type] || []).push(f);
  }
  dispatchEvent() {}
}
globalThis.HTMLElement = Element;
const windowListeners = {};
const stored = new Map();
globalThis.window = {
  localStorage: {
    getItem: (k) => (stored.has(k) ? stored.get(k) : null),
    setItem: (k, v) => stored.set(k, String(v)),
    removeItem: (k) => stored.delete(k),
  },
  customElements: {
    get: (t) => registry.get(t),
    define: (t, c) => registry.set(t, c),
  },
  addEventListener: (type, f) =>
    (windowListeners[type] = windowListeners[type] || []).push(f),
  dispatchEvent: (ev) => (windowListeners[ev.type] || []).forEach((f) => f(ev)),
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
const outcome = (imported, offGrid, pump = 0) => ({
  import_kwh: imported,
  export_kwh: 9000,
  use_kwh: 4200 + pump,
  heat_pump_kwh: pump,
  space_heat_kwh: pump ? 12400 : 0,
  hot_water_heat_kwh: pump ? 3000 : 0,
  heating_rod_kwh: 0,
  seasonal_cop: pump ? 3.2 : null,
  heating_days: pump ? 210 : 0,
  battery_cycles: 250,
  self_sufficiency_pct: Math.round((1 - imported / (4200 + pump)) * 1000) / 10,
  nights: 365,
  nights_off_grid: offGrid,
});
const scenarioCalls = [];
const scenario = (sd) => {
  scenarioCalls.push(sd);
  const pump = sd.heat_loss_w_per_k ? 4800 : 0;
  const battery = 12 * sd.extra_battery_kwh;
  return {
    response: {
      days: 365,
      now: outcome(900, 240),
      changed: outcome(
        900 - 40 * sd.extra_battery_kwh + pump * 0.78,
        240 + sd.extra_battery_kwh,
        pump,
      ),
      saving_per_year: battery - (pump ? 1100 : 0),
      battery_saving_per_year: battery,
      heat_pump_cost_per_year: pump ? 1100 : null,
      heat_pump_solar_pct: pump ? 22 : null,
      payback_years: sd.battery_price ? sd.battery_price / battery : null,
      gas: sd.gas_price ? { cost_per_year: 1900, saving_per_year: 800 } : null,
      degree_hours: { 12: 80000, 13: 84000, 15: 90000 },
      degree_hours_g20_15: 91000,
      year: {
        filled: true,
        measured_days: 197,
        filled_days: 168,
        filled_by_month: [100, 100, 100, 100, 80, 0, 0, 0, 0, 0, 30, 100],
      },
      months: Array.from({ length: 12 }, (_, i) => ({
        month: i + 1,
        import_now_kwh: 75,
        import_kwh: 70,
        heat_pump_kwh: pump ? 400 : 0,
        heat_pump_solar_kwh: pump ? 90 : 0,
        measured_pct: i >= 5 && i <= 9 ? 100 : 0,
      })),
      price: 0.3,
      feed_in: 0.08,
    },
  };
};
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
          ? q.service === "scenario"
            ? scenario(q.service_data)
            : simulate(q.service_data)
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
  "all five solar cards are defined",
  ["now", "until-sunrise", "best-time", "appliances", "forecast-trust"].every(
    (k) => registry.has("solar-energy-insights-" + k),
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
// Theme colours: deep tones on a light theme, pastels on dark, and a theme's
// own --sei-* values win over both because the cards only set the *-auto ones.
const themed = async (darkMode) => {
  const card = new (registry.get("solar-energy-insights-best-time"))();
  card.setConfig({ appliances: [TWO.appliances[0]] });
  for (let k = 0; k < 6; k++) {
    card.hass = { ...hass, themes: { darkMode } };
    await settle();
  }
  return card.shadowRoot.innerHTML;
};
const light = await themed(false),
  dark = await themed(true);
check(
  "a light theme gets deep status colours",
  light.includes("--sei-good-auto:#2e7d32"),
);
check("a dark theme gets pastels", dark.includes("--sei-good-auto:#8be36a"));
check(
  "a theme's own colour wins",
  light.includes("var(--sei-good, var(--sei-good-auto"),
);
check(
  "svg colours arrive as styles, never as attributes",
  !/\s(fill|stroke)="var\(/.test(light) && /style="fill:var\(/.test(light),
);
check(
  "and a self-closing shape stays closed, so the shapes after it are drawn",
  /<rect[^>]*style="fill:var\([^"]*"\/>/.test(light) &&
    !/<(rect|line|path|circle)[^>]*\/ style=/.test(light),
);
// Settings: each does what it says, and the editor's defaults look like none.
const html = async (key, config) => {
  const card = new (registry.get("solar-energy-insights-" + key))();
  card.setConfig(config);
  for (let k = 0; k < 6; k++) {
    card.hass = hass;
    await settle();
  }
  return card.shadowRoot.innerHTML;
};
for (const key of [
  "now",
  "until-sunrise",
  "best-time",
  "appliances",
  "forecast-trust",
]) {
  const Card = registry.get("solar-energy-insights-" + key);
  const stub = Card.getStubConfig();
  const plainHtml = await html(key, {});
  const stubHtml = await html(key, stub);
  check(
    key + ": the editor's defaults look like no settings",
    plainHtml === stubHtml,
  );
  const names = Card.getConfigForm().schema.map((f) => f.name);
  check(
    key + ": every default has a field in the editor",
    Object.keys(stub).every((k) => names.includes(k)),
  );
}
const rows = text(await html("now", { rows: ["tonight", "battery"] }));
check(
  "now: rows filtered and ordered",
  rows.indexOf("Tonight") < rows.indexOf("Battery") &&
    !rows.includes("Heavy loads"),
);
check(
  "now: the header can go",
  !text(await html("now", { header: false })).includes("own power"),
);
check(
  "now: taps can be switched off",
  !(await html("now", { tap: "none" })).includes('data-tap="jump"'),
);
const typicalOnly = text(await html("until-sunrise", { columns: ["typical"] }));
check(
  "until sunrise: one column",
  typicalOnly.includes("Typical night") && !typicalOnly.includes("Heavy night"),
);
check(
  "until sunrise: notes can go",
  !text(await html("until-sunrise", { footnotes: false })).includes(
    "kWh left above the reserve",
  ),
);
check(
  "best time: the chart can go",
  !(await html("best-time", { chart: false })).includes("<svg"),
);
const six = text(await html("forecast-trust", { checkpoints: ["06:00"] }));
check(
  "forecast: checkpoints filtered",
  six.includes("06:00") && !six.includes("18:00 for tomorrow"),
);
check(
  "forecast: notes can go",
  !text(await html("forecast-trust", { footnotes: false })).includes(
    "Frozen for today",
  ),
);
// The shipped cards keep their own cache: a dashboard tile's `sims`, in its own
// shape, must survive them drawing.
window.__sei.sims = {
  key: "tile",
  data: { dw: {}, dr: {}, both: { full_at: "x" } },
};
await html("until-sunrise", TWO);
await html("best-time", TWO);
check(
  "a dashboard tile's own cache is left alone",
  window.__sei.sims.data.both.full_at === "x",
);
// Appliances added under the integration show on cards without their own.
window.SolarDash.config().appliances = [
  { name: "Heat pump boost", icon: "mdi:heat-pump", kwh: 2.0, hours: 1.0 },
];
const added = await draw("best-time");
check(
  "a card without appliances shows the integration's",
  added.includes("Heat pump boost") && added.includes("2.0 kWh · 1.0 h"),
);
const own = await draw("best-time", { appliances: [TWO.appliances[0]] });
check(
  "and a card's own appliances win",
  own.includes("Dishwasher") && !own.includes("Heat pump boost"),
);
window.SolarDash.config().appliances = [];

// Appliances: best time and until sunrise in one card with a Today / Tonight switch.
const appliances = async (config = TWO) => {
  const card = new (registry.get("solar-energy-insights-appliances"))();
  card.setConfig(config);
  for (let k = 0; k < 6; k++) {
    card.hass = hass;
    await settle();
  }
  return card;
};
const tap = (card, view) =>
  card.listeners.click.forEach((f) =>
    f({
      composedPath: () => [
        { getAttribute: (a) => (a === "data-sei-view" ? view : null) },
      ],
    }),
  );
const redraw = async (card) => {
  for (let k = 0; k < 3; k++) {
    card.hass = hass;
    await settle();
  }
  return card.shadowRoot.innerHTML;
};
const day = await appliances();
const dayHtml = day.shadowRoot.innerHTML;
check(
  "appliances: in daylight it opens on today, with the best start per appliance",
  text(dayHtml).includes("1.0 kWh · 2.5 h") &&
    text(dayHtml).includes("until sunset") &&
    !text(dayHtml).includes("House only"),
);
check(
  "appliances: one switch, no second title",
  dayHtml.includes('data-sei-view="today"') &&
    dayHtml.includes('data-sei-view="tonight"') &&
    !text(dayHtml).includes("Best time to run") &&
    !text(dayHtml).includes("Until sunrise"),
);
check(
  "appliances: the tonight row of the Now card can land on it in either view",
  dayHtml.includes('data-solar-jump="sunrise"'),
);
tap(day, "tonight");
const nightHtml = text(await redraw(day));
check(
  "appliances: a tap shows tonight, the same appliances replayed through the night",
  nightHtml.includes("House only") &&
    nightHtml.includes("+ Dishwasher") &&
    nightHtml.includes("+ Both") &&
    !nightHtml.includes("1.0 kWh · 2.5 h"),
);
check(
  "appliances: and the choice holds for a card drawn later the same day",
  text((await appliances()).shadowRoot.innerHTML).includes("House only"),
);
tap(day, "today");
await redraw(day);
check(
  "appliances: a choice by hand wins over the card's setting",
  !text(
    (await appliances({ ...TWO, view: "tonight" })).shadowRoot.innerHTML,
  ).includes("House only"),
);
window.__sei.applianceView = null;
check(
  "appliances: a card set to open on tonight does",
  text(
    (await appliances({ ...TWO, view: "tonight" })).shadowRoot.innerHTML,
  ).includes("House only"),
);
window.SolarDash.jump("sunrise");
check(
  "appliances: jumping to tonight from the Now card switches to tonight",
  text(await redraw(day)).includes("House only"),
);
window.__sei.applianceView = { view: "today", phase: "tonight" };
check(
  "appliances: a choice from the night before is forgotten by day",
  text(await redraw(day)).includes("1.0 kWh · 2.5 h"),
);
const untouched = text(await html("until-sunrise", TWO));
check(
  "the until sunrise card keeps its own title",
  untouched.includes("Until sunrise") && !untouched.includes("Tonight Today"),
);

// The scenario lab: the year with the card's starting values.
const LAB = { extra_battery_kwh: 5, battery_price_per_kwh: 400 };
const drawLab = async (config) => {
  const card = new (registry.get("solar-energy-insights-scenario-lab"))();
  card.setConfig(config);
  for (let k = 0; k < 6; k++) {
    card.hass = hass;
    await settle();
  }
  return card;
};
const labCard = await drawLab(LAB);
const lab = text(labCard.shadowRoot.innerHTML);
check(
  "the scenario lab compares the year as it is and with more battery",
  lab.includes("Bought from the grid") &&
    lab.includes("900 kWh") &&
    lab.includes("700 kWh") &&
    lab.includes("245 of 365"),
);
check(
  "and says what it saves and when it pays back",
  lab.includes("saves about") && lab.includes("paid back in 33.3 years"),
);
check(
  "and how much of the year was recorded",
  lab.includes("197 days recorded, 168 filled"),
);
// Dragging the slider shows its value at once, without asking again.
const asked = scenarioCalls.length;
const before = labCard.shadowRoot.innerHTML;
labCard.shadowRoot.fire("input", {
  dataset: { lab: "battery" },
  value: "7.5",
  type: "range",
});
check(
  "a slider shows the value it is moving to",
  labCard.shadowRoot.found.battery?.textContent === "+7.5 kWh" &&
    labCard.shadowRoot.innerHTML === before &&
    scenarioCalls.length === asked,
);
// Letting go asks for that value and keeps it.
labCard.shadowRoot.fire("change", {
  dataset: { lab: "battery" },
  value: "7.5",
  type: "range",
});
for (let k = 0; k < 4; k++) {
  labCard.hass = hass;
  await settle();
}
check(
  "letting go replays the year with it",
  scenarioCalls.some((c) => c.extra_battery_kwh === 7.5) &&
    text(labCard.shadowRoot.innerHTML).includes("+7.5 kWh"),
);
const reloaded = text((await drawLab(LAB)).shadowRoot.innerHTML);
check(
  "and a reload keeps what was set",
  reloaded.includes("+7.5 kWh") && reloaded.includes("7.5 kWh more battery"),
);
const other = text(
  (await drawLab({ ...LAB, extra_battery_kwh: 3 })).shadowRoot.innerHTML,
);
check("another card keeps its own", other.includes("+3 kWh"));

// With a heat pump: its heat, electricity and roof share, against gas.
const pumpCard = await drawLab({
  heat_loss_w_per_k: 150,
  heating_limit_c: 12.5,
  gas_price: 0.11,
});
const pumpText = text(pumpCard.shadowRoot.innerHTML);
check(
  "a heat pump shows its heat, its electricity and its roof share",
  pumpText.includes("12,400 + 3,000 kWh") &&
    pumpText.includes("4,800 kWh, SPF 3.2") &&
    pumpText.includes("22%") &&
    pumpText.includes("from the roof"),
);
check(
  "and what it saves against gas",
  pumpText.includes("gas for the same heat costs") &&
    pumpText.includes("it saves about"),
);
const sent = scenarioCalls.find((c) => c.heat_loss_w_per_k === 150);
check(
  "and asks with the hot water and the heating limit",
  sent.heating_limit_c === 12.5 &&
    sent.hot_water_kwh_per_day === 8 &&
    sent.gas_price === 0.11,
);
check(
  "the months are drawn",
  (pumpCard.shadowRoot.innerHTML.match(/<rect/g) || []).length >= 24,
);
pumpCard.shadowRoot.fire("input", {
  dataset: { lab: "loss" },
  value: "200",
  type: "range",
});
check(
  "moving the heat loss shows the year's heat before asking",
  pumpCard.shadowRoot.found.loss?.textContent === "200 W/K" &&
    pumpCard.shadowRoot.found.estimate?.textContent.includes(
      // 200 W/K × 82,000 Kh at 12.5 °C
      "16,400 kWh of heating",
    ),
);
pumpCard.shadowRoot.fire("input", {
  dataset: { lab: "bill" },
  value: "29000",
  type: "number",
});
check(
  "a year's gas gives today's heat loss",
  // (29,000 × 0.9 − 8 × 365) / 91,000 Kh = 254.7 W/K
  pumpCard.shadowRoot.found.bill?.innerHTML.includes("255 W/K"),
);
console.log("ok");
