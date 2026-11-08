import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
const ROOT = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
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
const daysAgo = (k) => {
  const d = new Date();
  d.setHours(12, 0, 0, 0);
  d.setDate(d.getDate() - k);
  return iso(d);
};
const TODAY = daysAgo(0);
const START = daysAgo(400);

const CONFIG = {
  entities: {
    solar: "sensor.po_solar_yield_today",
    house: "sensor.po_house_consumption_today",
    import: "sensor.po_grid_import_today",
    export: "sensor.po_grid_export_today",
    solar_power: "sensor.po_solar_power",
  },
  prefix: "sensor.po_",
  history_start: START,
  strings: [{ n: 1, energy: "sensor.solar_energy_insights_pv1_energy_today" }],
};
let configAsked = 0;
const asked = [];
// Every day: 10 kWh solar, 8 kWh house, 2 kWh bought, 3 kWh sold, 6 kW peak
const PER_DAY = { solar_yield: 10, house_consumption: 8, grid_import: 2 };
const hass = {
  states: {},
  callService() {},
  callWS: async (q) => {
    if (q.type === "solar_energy_insights/config") {
      configAsked++;
      await new Promise((r) => setTimeout(r, 5));
      return CONFIG;
    }
    if (q.type !== "recorder/statistics_during_period") return {};
    asked.push(q);
    const out = {};
    for (const id of q.statistic_ids) {
      const rows = [];
      for (
        let t = new Date(q.start_time);
        t < new Date(q.end_time) && t <= new Date();
        q.period === "hour"
          ? t.setHours(t.getHours() + 1)
          : t.setDate(t.getDate() + 1)
      ) {
        const key = Object.keys(PER_DAY).find((k) => id.includes(k));
        const v = id.includes("grid_export")
          ? 3
          : key
            ? PER_DAY[key]
            : id.includes("pv1")
              ? 10
              : null;
        if (id.endsWith("solar_power"))
          rows.push({ start: t.getTime(), change: null, max: 6000 });
        else if (v !== null)
          rows.push({
            start: t.getTime(),
            change: q.period === "hour" ? v / 24 : v,
          });
      }
      if (rows.length) out[id] = rows;
    }
    return out;
  },
};
globalThis.window = {};
globalThis.document = { querySelector: () => ({ hass }) };
eval(
  fs.readFileSync(
    path.join(
      ROOT,
      "custom_components/solar_energy_insights/frontend/solar-energy-insights.js",
    ),
    "utf8",
  ),
);
const SP = window.SolarPeriod;
const settle = () => new Promise((r) => setTimeout(r, 50));
const show = (P, date) => {
  hass.states["input_select.solar_history_period"] = { state: P };
  hass.states["input_datetime.solar_day"] = { state: date };
};

// Nothing is fetched before the integration's settings are in
show("Week", TODAY);
check("not ready without settings", !SP.ready());
check("asks for the settings", configAsked === 1);
check(
  "no statistics before the settings",
  SP.view(hass, hass.states, null).loading && asked.length === 0,
);
await settle();
check("ready once the settings are in", SP.ready());

// Periods: weeks start on Monday, months clamp, never past today
const w = SP.range("Week", "2026-11-08");
check("week from Monday", w.start === "2026-11-02" && w.end === "2026-11-09");
check(
  "a month back from 31 March",
  SP.step("Month", "2026-03-31", -1) === "2026-02-28",
);
check("never past today", SP.step("Year", TODAY, 1) === TODAY);
check(
  "a year back from 29 February",
  SP.step("Year", "2024-02-29", -1) === "2023-02-28",
);
check(
  "ISO week across the new year",
  SP.isoWeek("2026-01-01") === 1 && SP.isoWeek("2024-12-30") === 1,
);

// A finished week adds up its days
const lastWeek = SP.range("Week", daysAgo(7));
show("Week", lastWeek.start);
SP.view(hass, hass.states, null);
await settle();
const V = SP.view(hass, hass.states, null);
check("a finished week has seven days", V.tot && V.tot.n === 7);
check(
  "its totals",
  V.tot.y === 70 && V.tot.h === 56 && V.tot.i === 14 && V.tot.e === 21,
);
check("its peak in kW", V.tot.pk === 6);
check("compared with the week before", V.prev && V.prev.y === 70);
check("autarky", Math.round(V.aut) === 75);

// Today takes the live day counters
Object.assign(hass.states, {
  "sensor.po_solar_yield_today": { state: "4.5" },
  "sensor.po_house_consumption_today": { state: "3" },
  "sensor.po_solar_power": { state: "7000" },
});
show("Week", TODAY);
SP.view(hass, hass.states, null);
await settle();
const T = SP.view(hass, hass.states, null);
check("today from the live counters", T.rows[TODAY].y === 4.5);
check("today's peak from live power", T.rows[TODAY].pk === 7);

// Months before the first statistics are never asked for
show("Year", START);
SP.view(hass, hass.states, null);
await settle();
const first = START.slice(0, 7);
check(
  "nothing before the first day with statistics",
  asked.every(
    (q) =>
      q.period === "month" || iso(new Date(q.end_time)).slice(0, 7) >= first,
  ),
);

// A year without a typical year: only months whose every day had a forecast
const fc = await SP.series(hass, "fc");
check("no forecast, no reference line", fc.length === 0);
SP.setTypical([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]);
const typ = await SP.series(hass, "fc");
check("a typical year, one point a month", typ.length === 12);
check("the typical year is kept", SP.typical[11] === 12);

console.log("ok");
