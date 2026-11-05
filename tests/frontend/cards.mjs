// The achievement cards, in Node with a minimal stand-in for the browser:
// defined and listed, drawn from the achievements answer, tappable, and
// drawing only when what they show changes.
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

const registry = new Map();
class Element {
  constructor() {
    this.listeners = {};
    this.writes = 0;
  }
  attachShadow() {
    const host = this;
    let html = "";
    this.shadowRoot = {
      get innerHTML() {
        return html;
      },
      set innerHTML(v) {
        html = v;
        host.writes++;
      },
    };
    return this.shadowRoot;
  }
  addEventListener(type, f) {
    (this.listeners[type] = this.listeners[type] || []).push(f);
  }
  fire(type, ev) {
    (this.listeners[type] || []).forEach((f) => f(ev));
  }
}
globalThis.HTMLElement = Element;
globalThis.window = {
  customElements: {
    get: (t) => registry.get(t),
    define: (t, c) => registry.set(t, c),
  },
};
globalThis.customElements = window.customElements;
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

const data = JSON.parse(
  fs.readFileSync(path.join(HERE, "achievements.json"), "utf8"),
);
const hass = {
  states: {},
  config: { currency: "EUR" },
  callWS: async (q) => {
    if (q.type === "solar_energy_insights/achievements") return data;
    return {};
  },
};
const settle = () => new Promise((r) => setTimeout(r, 0));
const text = (h) => h.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");

const TAGS = ["level", "streaks", "records", "badges"].map(
  (k) => "solar-energy-insights-" + k,
);
check(
  "all four cards are defined",
  TAGS.every((t) => registry.has(t)),
);
check(
  "and listed in the card picker",
  TAGS.every((t) => window.customCards.some((c) => c.type === t)),
);

const cards = {};
for (const tag of TAGS) {
  const card = new (registry.get(tag))();
  card.setConfig({});
  card.hass = hass;
  cards[tag] = card;
}
await settle();
await settle();
await settle();
for (const tag of TAGS)
  check(
    tag + " draws inside an ha-card",
    cards[tag].shadowRoot.innerHTML.startsWith("<ha-card"),
  );
check(
  "the level card shows the level",
  text(cards["solar-energy-insights-level"].shadowRoot.innerHTML).includes(
    "Level 17",
  ),
);
check(
  "the records card shows the records",
  text(cards["solar-energy-insights-records"].shadowRoot.innerHTML).includes(
    "Longest off the grid",
  ),
);
const badges = cards["solar-energy-insights-badges"];
check(
  "the badges card starts on the latest badge",
  text(badges.shadowRoot.innerHTML).includes("Latest · Harvest night"),
);

const tap = (id, dx = 0) => {
  const p = [{ dataset: { badge: id } }, {}];
  badges.fire("pointerdown", {
    clientX: 10,
    clientY: 10,
    composedPath: () => p,
  });
  badges.fire("pointerup", {
    clientX: 10 + dx,
    clientY: 10,
    composedPath: () => p,
    stopPropagation() {},
  });
};
tap("island_mode");
await settle();
check(
  "a tap selects a badge",
  text(badges.shadowRoot.innerHTML).includes(
    "Island mode · 24 hours without the grid in summer",
  ),
);
tap("last_light", 40);
await settle();
check(
  "a scroll selects nothing",
  text(badges.shadowRoot.innerHTML).includes("Island mode · 24 hours"),
);

const before = badges.writes;
badges.hass = { ...hass };
await settle();
check("an update that changes nothing draws nothing", badges.writes === before);

registry.set("broken", null);
const Broken = registry.get("solar-energy-insights-level");
const card = new Broken();
card.setConfig({});
card.hass = {
  states: {},
  callWS: () => {
    throw new Error("no");
  },
};
await settle();
check(
  "a card that fails shows why instead of breaking the page",
  card.shadowRoot.innerHTML.includes("ha-card"),
);
check(
  "the records card shows how today chases the best day",
  text(cards["solar-energy-insights-records"].shadowRoot.innerHTML).includes(
    "today 22% of the record",
  ),
);
const level = text(cards["solar-energy-insights-level"].shadowRoot.innerHTML);
check(
  "the quest carries the plan's advice",
  level.includes("keep the rest of the evening under ~5.7 kWh"),
);
check("yesterday's news", level.includes("Yesterday: best day since 12 Oct"));
check(
  "last week in one line",
  level.includes(
    "Last week: 4 of 7 nights without the grid · best day Sunday (26 kWh) · €37.80 kept at home · Night owl earned",
  ),
);
check(
  "a started badge's progress bar has room to show",
  badges.shadowRoot.innerHTML.includes(
    '<div style="flex:1;min-width:0"><div style="height:4px',
  ),
);
// Long text wraps on a phone instead of running off the card.
const streaks = cards["solar-energy-insights-streaks"].shadowRoot.innerHTML;
check(
  "the streaks' explanation wraps",
  /white-space:normal[^"]*">Shields come with each/.test(streaks),
);
check(
  "and so do their descriptions, in a column that may shrink",
  streaks.includes('<div style="min-width:0">') &&
    /white-space:normal;line-height:1.35">/.test(streaks),
);
// Settings: each does what it says, and the editor's defaults look like none.
const drawn = async (tag, config) => {
  const card = new (registry.get(tag))();
  card.setConfig(config);
  card.hass = hass;
  await settle();
  await settle();
  await settle();
  return card.shadowRoot.innerHTML;
};
for (const tag of [
  "solar-energy-insights-level",
  "solar-energy-insights-badges",
]) {
  const Card = registry.get(tag);
  check(
    tag + ": the editor's defaults look like no settings",
    (await drawn(tag, {})) === (await drawn(tag, Card.getStubConfig())),
  );
}
const levelPlain = text(
  await drawn("solar-energy-insights-level", { quest: false, recap: false }),
);
check(
  "level: quest and recap can go",
  !levelPlain.includes("keep the rest of the evening") &&
    !levelPlain.includes("Last week"),
);
const onlyDone = text(
  await drawn("solar-energy-insights-badges", { sections: ["complete"] }),
);
check(
  "badges: sections can go",
  onlyDone.includes("Complete") &&
    !onlyDone.includes("Any time") &&
    !onlyDone.includes("MWh club"),
);
console.log("ok");
