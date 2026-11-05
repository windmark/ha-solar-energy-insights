// Prints every card's editor form as JSON, for test_forms.py to validate with
// Home Assistant's own selector rules.
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const ROOT = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);
const registry = new Map();
globalThis.HTMLElement = class {};
globalThis.window = {
  customElements: {
    get: (t) => registry.get(t),
    define: (t, c) => registry.set(t, c),
  },
};
globalThis.customElements = window.customElements;
globalThis.document = { querySelector: () => null };
globalThis.structuredClone =
  globalThis.structuredClone || ((v) => JSON.parse(JSON.stringify(v)));
eval(
  fs.readFileSync(
    path.join(
      ROOT,
      "custom_components/solar_energy_insights/frontend/solar-energy-insights.js",
    ),
    "utf8",
  ),
);
const forms = {};
for (const [tag, Card] of registry)
  if (Card.getConfigForm)
    forms[tag] = {
      schema: Card.getConfigForm().schema,
      stub: Card.getStubConfig(),
    };
console.log(JSON.stringify(forms));
