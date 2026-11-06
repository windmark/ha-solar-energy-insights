/* Solar Energy Insights – shared helpers for dashboards (button-card and apexcharts).
   Shipped with the integration and loaded on every page: nothing to install, no resource to add.
   This home's counters, history start and strings come from the integration's own settings
   (WebSocket solar_energy_insights/config). Exposed as window.SolarDash; caches live in
   window.__solarDash, shared with the cards. */
(function () {
  const G = () => {
    const g = (window.__solarDash = window.__solarDash || {
      stats: {},
      fc: null,
      waiters: new Set(),
    });
    g.hourlyP = g.hourlyP || {};
    g.hourly = g.hourly || {};
    g.weekP = g.weekP || {};
    g.week = g.week || {};
    g.hourlyFail = g.hourlyFail || {};
    g.strH = g.strH || {};
    return g;
  };
  const RETRY_CONFIG_MS = 30000;
  const notify = function () {
    G().waiters.forEach(function (f) {
      try {
        f();
      } catch (x) {}
    });
    ((window.__sei || {}).listeners || []).forEach(function (f) {
      try {
        f();
      } catch (x) {}
    });
  };
  const config = function () {
    return G().cfg || null;
  };
  const loadConfig = function (hass) {
    const g = G();
    if (g.cfg) return Promise.resolve(g.cfg);
    if (g.cfgP) return g.cfgP;
    if (
      !hass ||
      !hass.callWS ||
      (g.cfgFail && Date.now() - g.cfgFail < RETRY_CONFIG_MS)
    )
      return Promise.resolve(null);
    g.cfgP = hass
      .callWS({ type: "solar_energy_insights/config" })
      .then(function (c) {
        g.cfg = c;
        notify();
        return c;
      })
      .catch(function () {
        g.cfgFail = Date.now();
        return null;
      })
      .finally(function () {
        delete g.cfgP;
      });
    return g.cfgP;
  };
  const hid = function () {
    const e = (config() || {}).entities || {};
    const one = function (x) {
      return x ? [x] : [];
    };
    return {
      solar: one(e.solar),
      house: one(e.house),
      imp: one(e.import),
      exp: one(e.export),
      soc: one(e.soc),
    };
  };
  /* What the configured counters' entity ids share, up to an underscore, for cards that build on it. */
  const prefix = function () {
    return (config() || {}).prefix || "";
  };
  const start = function () {
    const d = (config() || {}).history_start;
    return d ? new Date(d + "T00:00").getTime() : 0;
  };
  /* Each string's size in kWp, by number, for those that have one: { 1: 5.06, … }. */
  const stringKwp = function () {
    const out = {};
    ((config() || {}).strings || []).forEach(function (s) {
      if (s.kwp) out[s.n] = s.kwp;
    });
    return out;
  };
  const dayRange = function (d) {
    const s = new Date(d + "T00:00");
    const e = new Date(s);
    e.setDate(e.getDate() + 1);
    return [s, e];
  };

  /* Hourly energy + battery level for one local day. Failed requests are retried after 30 s (no tight loop). */
  const RETRY_MS = 30000;
  const loadHourly = function (hass, d) {
    const g = G();
    if (g.hourlyP[d]) return g.hourlyP[d];
    const failed = g.hourlyFail[d];
    if (failed && Date.now() - failed < RETRY_MS) {
      const wait = new Promise(function (r) {
        setTimeout(r, RETRY_MS - (Date.now() - failed));
      }).then(function () {
        delete g.hourlyP[d];
        return null;
      });
      g.hourlyP[d] = wait;
      return wait;
    }
    if (!hass || !hass.callWS) return Promise.resolve(null);
    const r0 = dayRange(d);
    let HID;
    const p = loadConfig(hass)
      .then(function () {
        HID = hid();
        const ids = [].concat.apply([], Object.values(HID));
        if (!ids.length) return null;
        return hass.callWS({
          type: "recorder/statistics_during_period",
          start_time: r0[0].toISOString(),
          end_time: r0[1].toISOString(),
          statistic_ids: ids,
          period: "hour",
          types: ["change", "mean"],
        });
      })
      .then(function (r) {
        if (!r) {
          delete g.hourlyP[d];
          return null;
        }
        const out = {};
        Object.keys(HID).forEach(function (k) {
          const type = k === "soc" ? "mean" : "change";
          out[k] = null;
          for (const id of HID[k]) {
            const rows = r && r[id];
            if (rows && rows.length) {
              const arr = new Array(24).fill(null);
              const cnt = new Array(24).fill(0);
              rows.forEach(function (row) {
                const v = row[type];
                if (v === null || v === undefined) return;
                const hr = new Date(row.start).getHours();
                /* DST: an hour can occur twice – add energy, average the battery level */
                if (arr[hr] === null) arr[hr] = v;
                else
                  arr[hr] =
                    type === "mean"
                      ? (arr[hr] * cnt[hr] + v) / (cnt[hr] + 1)
                      : arr[hr] + v;
                cnt[hr]++;
              });
              out[k] = arr;
              break;
            }
          }
        });
        delete g.hourlyFail[d];
        g.hourly[d] = out;
        return out;
      })
      .catch(function () {
        g.hourlyFail[d] = Date.now();
        delete g.hourlyP[d];
        return null;
      });
    g.hourlyP[d] = p;
    return p;
  };

  /* Energy-dashboard solar forecast (all configured forecasts summed), cached 30 min */
  const loadFc = function (hass) {
    const g = G();
    const f = g.fc;
    if (f && f.p && Date.now() - f.t < 30 * 60e3) return f.p;
    const p = hass
      .callWS({ type: "energy/solar_forecast" })
      .then(function (r) {
        const hours = {};
        Object.values(r || {}).forEach(function (e) {
          Object.entries((e && e.wh_hours) || {}).forEach(function (kv) {
            const ms = new Date(kv[0]).getTime();
            hours[ms] = (hours[ms] || 0) + kv[1];
          });
        });
        g.fc = {
          state: Object.keys(hours).length ? "done" : "none",
          t: Date.now(),
          hours: hours,
          p: p,
        };
        return hours;
      })
      .catch(function () {
        g.fc = { state: "none", t: Date.now(), hours: {}, p: null };
        return {};
      });
    g.fc = {
      state: "pending",
      t: Date.now(),
      hours: (f && f.hours) || {},
      p: p,
    };
    return p;
  };
  const fcHourly = function (hours, d) {
    const r0 = dayRange(d);
    const arr = new Array(24).fill(null);
    let any = false;
    Object.entries(hours || {}).forEach(function (kv) {
      const ms = +kv[0];
      if (ms >= r0[0].getTime() && ms < r0[1].getTime()) {
        const hr = new Date(ms).getHours();
        arr[hr] = (arr[hr] || 0) + kv[1] / 1000;
        any = true;
      }
    });
    return any ? arr : null;
  };

  /* Per-string kWh per hour for one day: { 1: [24], 2: [24], 3: [24] }, from PowerOcean's
     per-string power. The recorder's hourly mean in kW is the hour's energy in kWh; the hour
     in progress comes from the five-minute statistics. */
  const stringHours = async function (hass, day, isToday, now) {
    const g = G();
    const zero = function () {
      return new Array(24).fill(0);
    };
    const cfg = await loadConfig(hass);
    const strings = (cfg && cfg.strings) || [];
    const out = {};
    strings.forEach(function (s) {
      out[s.n] = zero();
    });
    if (!strings.length) return out;
    const dayStart = new Date(day + "T00:00");
    const key =
      day +
      (isToday
        ? "@" + now.getHours() + ":" + Math.floor(now.getMinutes() / 5)
        : "");
    let c = g.strH[day];
    if (!c || c.key !== key) {
      const e = new Date(dayStart);
      e.setDate(e.getDate() + 1);
      const ids = strings.map(function (s) {
        return s.power;
      });
      const ask = function (from, to, period) {
        return hass.callWS({
          type: "recorder/statistics_during_period",
          start_time: from.toISOString(),
          end_time: to.toISOString(),
          statistic_ids: ids,
          period: period,
          types: ["mean"],
          units: { power: "kW" },
        });
      };
      const hourNow = new Date(now);
      hourNow.setMinutes(0, 0, 0);
      c = g.strH[day] = {
        key: key,
        p: Promise.all([
          ask(dayStart, e, "hour"),
          isToday ? ask(hourNow, now, "5minute") : Promise.resolve({}),
        ])
          .then(function (both) {
            const hours = both[0] || {},
              fine = both[1] || {},
              res = {};
            strings.forEach(function (s) {
              const a = zero();
              (hours[s.power] || []).forEach(function (x) {
                if (x.mean > 0) a[new Date(x.start).getHours()] += x.mean;
              });
              if (isToday) {
                const h = now.getHours();
                a[h] = (fine[s.power] || []).reduce(function (sum, x) {
                  return sum + (Math.max(x.mean || 0, 0) * 5) / 60;
                }, 0);
                for (let i = h + 1; i < 24; i++) a[i] = 0;
              }
              res[s.n] = a.map(function (x) {
                return Math.round(x * 100) / 100;
              });
            });
            return res;
          })
          .catch(function () {
            delete g.strH[day];
            return null;
          }),
      };
    }
    const v = await c.p;
    return v || out;
  };

  /* ── Day totals for a whole month: { 'YYYY-MM-DD': { y, h, i, e } } ─────────────
     y solar · h house · i bought · e exported (kWh), from the first day with statistics.
     Past months are cached for the session, the current month for 30 min. */
  const isoDay = function (d) {
    const p = function (n) {
      return String(n).padStart(2, "0");
    };
    return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate());
  };
  const monthDays = function (hass, y, m) {
    const g = G();
    g.month = g.month || {};
    g.monthP = g.monthP || {};
    const key = y + "-" + m,
      c = g.month[key];
    const now = new Date(),
      isCur = y === now.getFullYear() && m === now.getMonth();
    if (c && !c.failed && (!isCur || Date.now() - c.t < 30 * 60e3))
      return Promise.resolve(c.v);
    if (c && c.failed && Date.now() - c.t < 60e3) return Promise.resolve(c.v);
    if (g.monthP[key]) return g.monthP[key];
    const s = new Date(y, m, 1),
      e = new Date(y, m + 1, 1);
    if (s.getTime() > Date.now()) return Promise.resolve({});
    const p = (async function () {
      const out = {};
      await loadConfig(hass);
      const HID = hid(),
        START = start();
      const ids = [].concat(HID.solar, HID.house, HID.imp, HID.exp);
      if (ids.length && e.getTime() > START && hass && hass.callWS) {
        const st = new Date(Math.max(s.getTime(), START));
        const r = await hass.callWS({
          type: "recorder/statistics_during_period",
          start_time: st.toISOString(),
          end_time: e.toISOString(),
          statistic_ids: ids,
          period: "day",
          types: ["change"],
        });
        const K = { y: HID.solar, h: HID.house, i: HID.imp, e: HID.exp };
        Object.keys(K).forEach(function (k) {
          const id = K[k].find(function (x) {
            return r && r[x] && r[x].length;
          });
          if (!id) return;
          r[id].forEach(function (row) {
            if (row.change === null || row.change === undefined) return;
            const d = isoDay(new Date(row.start));
            (out[d] = out[d] || {})[k] = Math.max(row.change, 0);
          });
        });
      }
      g.month[key] = { t: Date.now(), v: out };
      return out;
    })()
      .catch(function () {
        g.month[key] = { t: Date.now(), v: (c && c.v) || {}, failed: true };
        return g.month[key].v;
      })
      .finally(function () {
        delete g.monthP[key];
      });
    g.monthP[key] = p;
    return p;
  };
  /* Synchronous view for button-card: returns cached data (possibly stale) or null, and calls
     onLoad once fresh data has arrived so the card can re-render. */
  const monthDaysSync = function (hass, y, m, onLoad) {
    const g = G();
    g.month = g.month || {};
    const c = g.month[y + "-" + m];
    const now = new Date(),
      isCur = y === now.getFullYear() && m === now.getMonth();
    const fresh = c && !c.failed && (!isCur || Date.now() - c.t < 30 * 60e3);
    if (!fresh && !(c && c.failed && Date.now() - c.t < 60e3)) {
      monthDays(hass, y, m).then(function () {
        if (onLoad) {
          try {
            onLoad();
          } catch (x) {}
        }
      });
    }
    return c ? c.v : null;
  };

  /* Tap handlers used from inline onclick="" in button-card HTML */
  const pickDay = function (d) {
    const ha = document.querySelector("home-assistant"),
      h = ha && ha.hass;
    if (h)
      h.callService("input_datetime", "set_datetime", {
        entity_id: "input_datetime.solar_day",
        date: d,
      });
  };
  const setCalMode = function (el, mode) {
    const g = G();
    g.calMode = mode;
    try {
      window.localStorage.setItem("solarDash.calMode", mode);
    } catch (x) {}
    const host = el && el.getRootNode && el.getRootNode().host;
    if (host && host.requestUpdate) {
      try {
        host.requestUpdate("_config");
      } catch (x) {}
    }
  };
  const calMode = function () {
    const g = G();
    if (!g.calMode) {
      try {
        g.calMode = window.localStorage.getItem("solarDash.calMode") || "y";
      } catch (x) {
        g.calMode = "y";
      }
    }
    return g.calMode;
  };

  /* ── Jump to a card on the current page ─────────────────────────────────────────────
     Target cards carry an invisible marker: <a data-solar-jump="battery"></a>. Cards live in
     nested shadow roots, so the search walks them. Fallback: a heading card with that text. */
  const deepFind = function (pred) {
    const stack = [document];
    while (stack.length) {
      const root = stack.pop();
      const all = root.querySelectorAll ? root.querySelectorAll("*") : [];
      for (let i = 0; i < all.length; i++) {
        const e = all[i];
        if (pred(e)) return e;
        if (e.shadowRoot) stack.push(e.shadowRoot);
      }
    }
    return null;
  };
  const JUMP_HEADINGS = {
    forecast: "Forecast",
    today: "Solar today",
    battery: "Battery",
  };
  const jump = function (key) {
    let el = deepFind(function (e) {
      return e.getAttribute && e.getAttribute("data-solar-jump") === key;
    });
    if (!el && JUMP_HEADINGS[key])
      el = deepFind(function (e) {
        return (
          e.tagName === "HUI-HEADING-CARD" &&
          e.shadowRoot &&
          (e.shadowRoot.textContent || "")
            .trim()
            .indexOf(JUMP_HEADINGS[key]) === 0
        );
      });
    if (!el) return;
    el.style.scrollMarginTop = "96px"; // clear the app header
    el.scrollIntoView({ behavior: "smooth", block: "start" });
    const card =
      (el.closest && el.closest("ha-card")) ||
      (el.shadowRoot && el.shadowRoot.querySelector("ha-card"));
    if (card) {
      // brief outline so you see where you landed
      const prev = card.style.boxShadow,
        prevT = card.style.transition;
      card.style.transition = "box-shadow .25s ease";
      setTimeout(function () {
        card.style.boxShadow = "0 0 0 2px #f5b83d";
      }, 350);
      setTimeout(function () {
        card.style.boxShadow = prev;
        setTimeout(function () {
          card.style.transition = prevT;
        }, 300);
      }, 1700);
    }
  };
  const jumpMark = function (key) {
    return (
      '<a data-solar-jump="' +
      key +
      '" style="display:block;height:0;overflow:hidden"></a>'
    );
  };

  /* Shared helpers for the cards: colours, reading states, small pieces of HTML,
     and a WebSocket cache shared by every card (re-rendered when data arrives). */
  /* Errors in words: the frontend's connection errors arrive as bare codes. */
  const RETRY_FIRST_S = 5;
  const CONNECTION = {
    1: "Home Assistant cannot be reached",
    2: "the login has expired",
    3: "the connection to Home Assistant was lost",
  };
  const readable = (e) => {
    const code = typeof e === "number" ? e : e?.code;
    if (CONNECTION[code]) return CONNECTION[code] + "; retrying";
    return String(e?.message || e);
  };
  const watchConnection = (hass) => {
    const conn = hass && hass.connection;
    if (!conn || conn.__seiWatched || !conn.addEventListener) return;
    conn.__seiWatched = true;
    conn.addEventListener("ready", () => {
      const W = window.__sei || {};
      Object.keys(W).forEach((k) => {
        if (W[k] && W[k].error) W[k] = { ...W[k], t: 0 };
      });
      (W.listeners || []).forEach((f) => f());
    });
  };
  const ui = (el, hass, states) => {
    const W = (window.__sei = window.__sei || {});
    const C = {
      text: "var(--primary-text-color, #fff)",
      muted: "var(--secondary-text-color, #8e8e93)",
      faint: "var(--disabled-text-color, #636366)",
      track: "var(--sei-track, var(--divider-color, #2c2c2e))",
      typical: "var(--sei-info, var(--sei-info-auto, #5ac8fa))",
      heavy: "var(--sei-warn, var(--sei-warn-auto, #ffb74d))",
      ok: "var(--sei-good, var(--sei-good-auto, #8be36a))",
      bad: "var(--sei-bad, var(--sei-bad-auto, #ff8a80))",
    };
    const st = (id) => states[id];
    const val = (id) => {
      const s = st(id);
      return !s || ["unknown", "unavailable", ""].includes(s.state)
        ? null
        : s.state;
    };
    const attr = (id, a) => st(id)?.attributes?.[a];
    const num = (v) =>
      v === null || v === undefined || v === "" || isNaN(+v) ? null : +v;
    const hm = (d) => (d ? new Date(d).toTimeString().slice(0, 5) : "–");
    const kwh = (v) => (num(v) === null ? "–" : num(v).toFixed(1) + " kWh");
    const sub = (t) =>
      `<div style="font-size:11px;color:${C.muted};margin-top:8px;white-space:normal;line-height:1.4">${t}</div>`;
    const head = (
      icon,
      title,
      right,
    ) => `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
      <div style="display:flex;align-items:center;gap:8px;font-size:15px;color:${
        C.text
      }"><ha-icon icon="${icon}" style="--mdc-icon-size:18px;color:${
        C.muted
      }"></ha-icon>${title}</div>
      <div style="font-size:12px;color:${C.muted}">${right || ""}</div></div>`;
    const pill = (text, color) =>
      `<span style="font-size:12px;padding:2px 10px;border-radius:10px;background:${color}22;color:${color}">${text}</span>`;
    if (el && !el.__seiListen) {
      el.__seiListen = true;
      (W.listeners = W.listeners || []).push(() => {
        try {
          el.requestUpdate("_config");
        } catch (x) {}
      });
    }
    // A failed request keeps the last good answer, so tiles show slightly older
    // data instead of an error while it retries.
    const ws = (key, type, maxAgeS) => {
      const c = W[key];
      const due =
        !c ||
        (!c.pending &&
          Date.now() - c.t > (c.error ? c.retryS : maxAgeS) * 1000);
      if (due && hass?.callWS) {
        W[key] = { ...(c || {}), t: Date.now(), pending: true };
        watchConnection(hass);
        hass
          .callWS({ type })
          .then((d) => {
            W[key] = { t: Date.now(), data: d };
          })
          .catch((e) => {
            const before = W[key] || {};
            const retryS = Math.min(
              before.error ? before.retryS * 2 : RETRY_FIRST_S,
              maxAgeS,
            );
            W[key] = {
              ...before,
              pending: false,
              t: Date.now(),
              error: readable(e),
              retryS,
            };
            setTimeout(
              () => (W.listeners || []).forEach((f) => f()),
              retryS * 1000 + 50,
            );
          })
          .finally(() => (W.listeners || []).forEach((f) => f()));
      }
      return W[key]?.data;
    };
    if (hass) loadConfig(hass);
    const waiting = (key, what) =>
      `<div style="font-size:12px;color:${C.muted}">${
        W[key]?.error
          ? "Could not read " + what + ": " + W[key].error
          : "Reading " + what + "…"
      }</div>`;
    return { W, C, st, val, attr, num, hm, kwh, sub, head, pill, ws, waiting };
  };

  window.SolarDash = {
    version: 13,
    ui,
    config,
    loadConfig,
    stringKwp,
    dayRange,
    loadHourly,
    loadFc,
    fcHourly,
    stringHours,
    isoDay,
    monthDays,
    monthDaysSync,
    pickDay,
    setCalMode,
    calMode,
    jump,
    jumpMark,
    get PFX() {
      return prefix();
    },
    get HID() {
      return hid();
    },
    get START() {
      return start();
    },
  };
  /* Ask for the settings straight away with the frontend's own connection, when there is one. */
  try {
    const ha = document.querySelector("home-assistant");
    if (ha && ha.hass) loadConfig(ha.hass);
  } catch (x) {}

  /* Real cards rather than button-card templates: taps and redraws work the ordinary way. */
  const CARD_STYLE =
    "display:block;background:var(--ha-card-background, var(--card-background-color, #1c1c1e));border-radius:18px;border:none;box-shadow:none;" +
    "padding:18px 20px 20px;color:var(--primary-text-color, #e5e5e7);text-align:left;white-space:nowrap;" +
    "font-family:var(--ha-font-family-body, Roboto, sans-serif);font-size:14px;line-height:1.4";
  // Status colours chosen for the theme: pastels read well on dark, deeper tones on
  // light. A --sei-good (and so on) set in a theme overrides both.
  const TONES = {
    dark: { good: "#8be36a", warn: "#ffb74d", bad: "#ff8a80", info: "#9ad7e6" },
    light: {
      good: "#2e7d32",
      warn: "#e65100",
      bad: "#c62828",
      info: "#0277bd",
    },
  };
  const toneDefaults = (hass) => {
    const set = hass?.themes?.darkMode === false ? TONES.light : TONES.dark;
    return Object.entries(set)
      .map(([tone, colour]) => `--sei-${tone}-auto:${colour}`)
      .join(";");
  };
  // SVG presentation attributes don't take var(): theme colours go into a style.
  const svgColours = (html) =>
    html.replace(
      /<(rect|line|path|circle|text|polyline|polygon)\b([^>]*)>/g,
      (tag, name, attrs) => {
        const moved = [];
        const rest = attrs.replace(
          /\s(fill|stroke)="((?:var|color-mix)\([^"]*)"/g,
          (_, prop, value) => {
            moved.push(`${prop}:${value}`);
            return "";
          },
        );
        return moved.length
          ? `<${name}${rest} style="${moved.join(";")}">`
          : tag;
      },
    );
  const APPLIANCES = {
    name: "appliances",
    selector: {
      object: {
        multiple: true,
        label_field: "name",
        fields: {
          name: { label: "Name", required: true, selector: { text: {} } },
          icon: { label: "Icon", selector: { icon: {} } },
          // Sliders, not boxes: some phone keypads can't type a decimal point.
          kwh: {
            label: "Typical energy per run",
            selector: {
              number: {
                min: 0.1,
                max: 10,
                step: 0.1,
                mode: "slider",
                unit_of_measurement: "kWh",
              },
            },
          },
          hours: {
            label: "Typical duration",
            selector: {
              number: {
                min: 0.5,
                max: 8,
                step: 0.5,
                mode: "slider",
                unit_of_measurement: "h",
              },
            },
          },
          energy_per_run: {
            label: "Sensor that learns energy per run",
            selector: { entity: { domain: "sensor" } },
          },
        },
      },
    },
  };
  const RUNNING = {
    name: "running",
    selector: {
      object: {
        fields: {
          entity: { label: "Entity", selector: { entity: {} } },
          state: { label: "State while running", selector: { text: {} } },
          name: { label: "Name", selector: { text: {} } },
        },
      },
    },
  };
  const choose = (options) => ({
    select: {
      multiple: true,
      options: options.map(([value, label]) => ({ value, label })),
    },
  });
  const NOW_ROWS = [
    ["battery", "Battery"],
    ["tonight", "Tonight"],
    ["next_day", "Today or tomorrow"],
    ["loads", "Heavy loads"],
    ["surplus", "Surplus"],
    ["last_night", "Last night"],
  ];
  const CHECKPOINTS = [
    ["18:00", "18:00 for tomorrow"],
    ["21:00", "21:00 for tomorrow"],
    ["06:00", "06:00 for today"],
    ["12:00", "12:00 for the afternoon"],
  ];
  const SECTIONS = [
    ["within_reach", "Within reach"],
    ["any_time", "Any time"],
    ["later", "Later"],
    ["complete", "Complete"],
  ];
  const FORMS = {
    now: {
      schema: [
        { name: "rows", selector: choose(NOW_ROWS) },
        { name: "header", selector: { boolean: {} } },
        {
          name: "tap",
          selector: {
            select: {
              mode: "dropdown",
              options: [
                { value: "jump", label: "Jump to the card that explains it" },
                { value: "none", label: "Nothing" },
              ],
            },
          },
        },
      ],
      labels: {
        rows: "Rows",
        header: "Show the time and today's own power",
        tap: "Tapping a row",
      },
      stub: { rows: NOW_ROWS.map(([k]) => k), header: true, tap: "jump" },
    },
    untilSunrise: {
      schema: [
        {
          name: "columns",
          selector: choose([
            ["typical", "Typical night"],
            ["heavy", "Heavy night"],
          ]),
        },
        { name: "footnotes", selector: { boolean: {} } },
        APPLIANCES,
        RUNNING,
      ],
      labels: {
        columns: "Columns",
        footnotes: "Show the notes underneath",
        appliances: "Appliances you might start",
        running: "An appliance that may already be running",
      },
      stub: { columns: ["typical", "heavy"], footnotes: true, appliances: [] },
    },
    bestTime: {
      schema: [{ name: "chart", selector: { boolean: {} } }, APPLIANCES],
      labels: {
        chart: "Show the chart of spare sun",
        appliances: "Appliances",
      },
      stub: { chart: true, appliances: [] },
    },
    forecastTrust: {
      schema: [
        { name: "checkpoints", selector: choose(CHECKPOINTS) },
        { name: "footnotes", selector: { boolean: {} } },
      ],
      labels: {
        checkpoints: "Checkpoints",
        footnotes: "Show the notes underneath",
      },
      stub: { checkpoints: CHECKPOINTS.map(([k]) => k), footnotes: true },
    },
    level: {
      schema: [
        { name: "quest", selector: { boolean: {} } },
        { name: "recap", selector: { boolean: {} } },
      ],
      labels: {
        quest: "Show the quest",
        recap: "Show yesterday and last week",
      },
      stub: { quest: true, recap: true },
    },
    badges: {
      schema: [{ name: "sections", selector: choose(SECTIONS) }],
      labels: { sections: "Sections" },
      stub: { sections: SECTIONS.map(([k]) => k) },
    },
  };

  FORMS.batteryFull = {
    schema: [APPLIANCES],
    labels: { appliances: "Appliances you might start" },
    stub: { appliances: [] },
  };
  FORMS.planTrust = {
    schema: [
      {
        name: "sections",
        selector: choose([
          ["performance", "How the plan has done"],
          ["learned", "What it has learned"],
          ["solcast", "Solcast vs your roof"],
        ]),
      },
    ],
    labels: { sections: "Sections" },
    stub: { sections: ["performance", "learned", "solcast"] },
  };
  FORMS.dayInsights = {
    schema: [
      {
        name: "day_entity",
        selector: { entity: { domain: "input_datetime" } },
      },
    ],
    labels: {
      day_entity: "Follow the date in this helper (otherwise yesterday)",
    },
    stub: {},
  };

  const defineCard = (tag, name, description, render, form) => {
    if (!window.customElements || customElements.get(tag)) return;
    class Card extends HTMLElement {
      setConfig(config) {
        this._config = config || {};
      }
      set hass(hass) {
        this._hass = hass;
        this.requestUpdate();
      }
      get hass() {
        return this._hass;
      }
      // Also how the shared helpers ask for a redraw when data arrives.
      requestUpdate() {
        if (this._queued) return;
        this._queued = true;
        Promise.resolve().then(() => {
          this._queued = false;
          this._render();
        });
      }
      getCardSize() {
        return 4;
      }
      getGridOptions() {
        return { columns: 12, min_columns: 6 };
      }
      _render() {
        if (!this._hass) return;
        if (!this.shadowRoot) this.attachShadow({ mode: "open" });
        let body;
        try {
          body = render.call(this, this._hass.states, this._hass);
        } catch (e) {
          body = `<div style="font-size:12px;color:var(--sei-bad, var(--sei-bad-auto, #ff8a80));white-space:normal">${String(
            (e && e.message) || e,
          )}</div>`;
        }
        body = svgColours(body);
        if (body === this._drawn) return;
        this._drawn = body;
        this.shadowRoot.innerHTML = `<ha-card style="${CARD_STYLE};${toneDefaults(
          this._hass,
        )}">${body}</ha-card>`;
      }
    }
    if (form) {
      Card.getConfigForm = () => ({
        schema: form.schema,
        computeLabel: (field) => form.labels[field.name],
      });
      Card.getStubConfig = () => structuredClone(form.stub);
    }
    customElements.define(tag, Card);
    (window.customCards = window.customCards || []).push({
      type: tag,
      name,
      description,
      preview: false,
    });
  };

  defineCard(
    "solar-energy-insights-level",
    "Solar level",
    "Your level, rank, today's score and quest.",
    function (states, hass) {
      const el = this;
      const {
        W,
        C,
        st,
        val,
        attr,
        num,
        hm,
        kwh,
        sub,
        head,
        pill,
        ws,
        waiting,
      } = window.SolarDash.ui(this, hass, states);

      const a = ws("ach", "solar_energy_insights/achievements", 300);
      const off = W.ach?.error && /disabled|off/i.test(W.ach.error);
      const TIER = [
        ["#cd7f32", "Bronze"],
        ["#c0c0c8", "Silver"],
        ["#f5b83d", "Gold"],
      ];
      const RANK = {
        Bronze: ["#cd7f32", "mdi:shield-outline"],
        Silver: ["#c0c0c8", "mdi:shield-half-full"],
        Gold: ["#f5b83d", "mdi:shield-star"],
        Platinum: [
          "var(--sei-info, var(--sei-info-auto, #9ad7e6))",
          "mdi:shield-crown",
        ],
        Diamond: ["#b9f2ff", "mdi:diamond-stone"],
      };
      const bar = (pct, color, h = 6) =>
        `<div style="height:${h}px;background:var(--sei-track, var(--divider-color, #2c2c2e));border-radius:${
          h / 2
        }px;overflow:hidden"><div style="width:${Math.max(
          0,
          Math.min(pct, 100),
        )}%;height:100%;background:${color};border-radius:${
          h / 2
        }px"></div></div>`;
      const dmy = (d) =>
        d
          ? new Date(d).toLocaleDateString("en-GB", {
              day: "numeric",
              month: "short",
              year: "2-digit",
            })
          : "";
      const notReady = () =>
        off
          ? sub(
              "Achievements are off: switch them on in the Solar Energy Insights options.",
            )
          : waiting("ach", "the trophy room");

      // The open quest leads: it is what's closest to happening.
      if (!a) return notReady();
      const L = a.level,
        R = a.rank,
        [rc, ricon] = RANK[R.name] || RANK.Bronze;
      const PARTS = [
        [
          "self_sufficiency",
          "Self-sufficiency",
          40,
          "var(--sei-good, var(--sei-good-auto, #8be36a))",
        ],
        ["kept_at_home", "Solar kept at home", 30, "#f5b83d"],
        ["battery_used", "Battery put to work", 15, "#4c7cf3"],
        ["performance", "Against the forecast", 15, "#b07cf0"],
      ];
      const parts = a.today.parts || {};
      const shown = PARTS.filter(([k]) => parts[k] !== undefined);
      const total = shown.reduce((s, [, , w]) => s + w, 0) || 1;
      const rows = shown
        .map(([k, label, w, c]) => {
          const pts = Math.round((parts[k] * w * 100) / total);
          return `<div style="margin-top:9px"><div style="display:flex;justify-content:space-between;font-size:12px">
    <span style="color:var(--secondary-text-color, #c7c7cc)">${label}</span><span style="color:var(--primary-text-color, #fff)">${pts}<span style="color:var(--disabled-text-color, #636366)"> / ${Math.round(
      (w * 100) / total,
    )}</span></span></div>
    <div style="margin-top:4px">${bar(parts[k] * 100, c)}</div></div>`;
        })
        .join("");
      const open = [...(a.quests || [])]
        .reverse()
        .find((q) => q.result === null || q.result === undefined);
      const last = [...(a.quests || [])]
        .reverse()
        .find((q) => q.result === true || q.result === false);
      const quest = open
        ? `<div style="background:color-mix(in srgb, var(--sei-good, var(--sei-good-auto, #8be36a)) 12%, transparent);border-radius:12px;padding:10px 12px;margin:12px 0 4px">
    <div style="display:flex;justify-content:space-between;align-items:center;font-size:11px;color:var(--sei-good, var(--sei-good-auto, #8be36a))">
      <span><ha-icon icon="mdi:flag-variant-outline" style="--mdc-icon-size:14px"></ha-icon> ${
        open.kind === "grid_free_nights"
          ? "This week"
          : open.kind === "grid_free_night"
            ? "Tonight"
            : "Quest for " +
              new Date(open.day).toLocaleDateString("en-GB", {
                weekday: "long",
              })
      }</span>
      <span style="font-weight:500">+${open.reward} XP</span></div>
    <div style="font-size:15px;color:var(--primary-text-color, #fff);font-weight:500;margin-top:4px;white-space:normal">${
      open.title
    }</div>${
      open.hint
        ? `<div style="font-size:12px;color:var(--sei-good, var(--sei-good-auto, #8be36a));margin-top:3px;white-space:normal">${open.hint}</div>`
        : ""
    }</div>`
        : "";
      const lastLine = last
        ? `<div style="font-size:11px;color:${
            last.result ? C.ok : C.muted
          };margin-top:4px">${last.result ? "✓" : "✗"} ${last.title}${
            last.result ? ` · +${last.reward} XP` : ""
          }</div>`
        : "";
      const month = (a.months || []).slice(-2, -1)[0];
      const dayMonth = (d) =>
        new Date(d + "T00:00").toLocaleDateString("en-GB", {
          day: "numeric",
          month: "short",
        });
      const NEWS = {
        best_day: ["best day", "best day on record"],
        self_sufficient: [
          "most self-sufficient",
          "most self-sufficient on record",
        ],
        thriftiest: ["thriftiest", "thriftiest on record"],
      };
      const news = (a.yesterday || [])
        .filter((x) => NEWS[x.key])
        .map((x) =>
          x.since
            ? `${NEWS[x.key][0]} since ${dayMonth(x.since)}`
            : NEWS[x.key][1],
        );
      const yesterdayLine = news.length
        ? `<div style="font-size:11px;color:var(--sei-good, var(--sei-good-auto, #8be36a));margin-top:12px;white-space:normal">Yesterday: ${news.join(
            " · ",
          )}</div>`
        : "";
      const lw = a.last_week;
      const money = (v) =>
        new Intl.NumberFormat("en-GB", {
          style: "currency",
          currency: hass?.config?.currency || "EUR",
        }).format(v);
      const weekLine = lw
        ? `<div style="font-size:11px;color:var(--secondary-text-color, #8e8e93);margin-top:${
            news.length ? 4 : 12
          }px;white-space:normal">Last week: ${[
            lw.nights
              ? `${lw.nights_lasted} of ${lw.nights} nights without the grid`
              : "",
            lw.best_day
              ? `best day ${new Date(lw.best_day + "T00:00").toLocaleDateString(
                  "en-GB",
                  { weekday: "long" },
                )} (${lw.best_kwh} kWh)`
              : "",
            lw.saved !== null && lw.saved !== undefined
              ? `${money(lw.saved)} kept at home`
              : "",
            lw.badges && lw.badges.length
              ? `${lw.badges.join(", ")} earned`
              : "",
          ]
            .filter(Boolean)
            .join(" · ")}</div>`
        : "";
      return `<div style="display:flex;align-items:center;gap:14px">
    <div style="width:56px;height:56px;border-radius:50%;background:var(--sei-track, var(--divider-color, #2c2c2e));display:flex;align-items:center;justify-content:center;box-shadow:0 0 0 2px ${rc}55;flex-shrink:0">
      <ha-icon icon="${ricon}" style="--mdc-icon-size:30px;color:${rc}"></ha-icon></div>
    <div style="flex:1;min-width:0">
      <div style="font-size:18px;color:var(--primary-text-color, #fff);font-weight:500;white-space:normal;line-height:1.25">Level ${
        L.level
      } · ${L.name}</div>
      <div style="font-size:12px;color:var(--secondary-text-color, #8e8e93);white-space:normal;line-height:1.35">${
        R.name
      } rank · 30-day average ${R.average}${
        R.next_name ? ` · ${R.next_name} at ${R.next_at}` : ""
      }</div>
      <div style="margin-top:6px">${bar(L.progress * 100, rc, 5)}</div>
      <div style="font-size:11px;color:var(--disabled-text-color, #636366);margin-top:3px">${(
        L.next_xp - L.xp
      ).toLocaleString("en-GB")} XP to level ${L.level + 1}</div>
    </div>
    <div style="text-align:center;flex-shrink:0"><div style="font-size:30px;color:var(--primary-text-color, #fff);font-weight:500;line-height:1">${
      a.today.score ?? "–"
    }</div>
      <div style="font-size:10px;color:var(--secondary-text-color, #8e8e93)">today</div></div>
  </div>${(this._config || {}).quest === false ? "" : quest + lastLine}${rows}${
    (this._config || {}).recap === false ? "" : yesterdayLine + weekLine
  }
  ${
    month
      ? `<div style="font-size:11px;color:var(--disabled-text-color, #636366);margin-top:12px;white-space:normal">${new Date(
          month.month + "-01",
        ).toLocaleDateString("en-GB", { month: "long" })}: average ${
          month.score
        }, ${month.nights_lasted} of ${month.nights} nights without the grid${
          month.saved !== null && month.saved !== undefined
            ? `, sun kept at home worth ${new Intl.NumberFormat("en-GB", {
                style: "currency",
                currency: hass?.config?.currency || "EUR",
              }).format(month.saved)}`
            : ""
        }${
          month.badges.length
            ? `, ${month.badges.length} new tier${
                month.badges.length > 1 ? "s" : ""
              }`
            : ""
        }</div>`
      : ""
  }`;
    },
    FORMS.level,
  );

  defineCard(
    "solar-energy-insights-streaks",
    "Solar streaks",
    "Your streaks, with their shields.",
    function (states, hass) {
      const el = this;
      const {
        W,
        C,
        st,
        val,
        attr,
        num,
        hm,
        kwh,
        sub,
        head,
        pill,
        ws,
        waiting,
      } = window.SolarDash.ui(this, hass, states);

      const a = ws("ach", "solar_energy_insights/achievements", 300);
      const off = W.ach?.error && /disabled|off/i.test(W.ach.error);
      const TIER = [
        ["#cd7f32", "Bronze"],
        ["#c0c0c8", "Silver"],
        ["#f5b83d", "Gold"],
      ];
      const RANK = {
        Bronze: ["#cd7f32", "mdi:shield-outline"],
        Silver: ["#c0c0c8", "mdi:shield-half-full"],
        Gold: ["#f5b83d", "mdi:shield-star"],
        Platinum: [
          "var(--sei-info, var(--sei-info-auto, #9ad7e6))",
          "mdi:shield-crown",
        ],
        Diamond: ["#b9f2ff", "mdi:diamond-stone"],
      };
      const bar = (pct, color, h = 6) =>
        `<div style="height:${h}px;background:var(--sei-track, var(--divider-color, #2c2c2e));border-radius:${
          h / 2
        }px;overflow:hidden"><div style="width:${Math.max(
          0,
          Math.min(pct, 100),
        )}%;height:100%;background:${color};border-radius:${
          h / 2
        }px"></div></div>`;
      const dmy = (d) =>
        d
          ? new Date(d).toLocaleDateString("en-GB", {
              day: "numeric",
              month: "short",
              year: "2-digit",
            })
          : "";
      const notReady = () =>
        off
          ? sub(
              "Achievements are off: switch them on in the Solar Energy Insights options.",
            )
          : waiting("ach", "the trophy room");

      if (!a) return notReady();
      const rows = a.streaks
        .map((s) => {
          const hot = s.current > 0;
          const shields = s.shields
            ? ` <span style="color:var(--sei-info, var(--sei-info-auto, #9ad7e6));font-size:11px">${'<ha-icon icon="mdi:shield-half-full" style="--mdc-icon-size:13px"></ha-icon>'.repeat(
                s.shields,
              )}</span>`
            : "";
          return `<div style="display:grid;grid-template-columns:36px 1fr auto;gap:12px;align-items:center;margin-top:12px">
    <div style="width:36px;height:36px;border-radius:50%;background:${
      hot
        ? "color-mix(in srgb, var(--sei-warn, var(--sei-warn-auto, #ffb74d)) 18%, transparent)"
        : "var(--sei-track, var(--divider-color, #2c2c2e))"
    };display:flex;align-items:center;justify-content:center">
      <ha-icon icon="${s.icon}" style="--mdc-icon-size:19px;color:${
        hot
          ? "var(--sei-warn, var(--sei-warn-auto, #ffb74d))"
          : "var(--disabled-text-color, #636366)"
      }"></ha-icon></div>
    <div style="min-width:0"><div style="font-size:14px;color:var(--primary-text-color, #fff);white-space:normal">${
      s.name
    }${shields}</div><div style="font-size:11px;color:var(--secondary-text-color, #8e8e93);white-space:normal;line-height:1.35">${
      s.description
    }</div></div>
    <div style="text-align:right"><div style="font-size:22px;color:${
      hot
        ? "var(--primary-text-color, #fff)"
        : "var(--disabled-text-color, #636366)"
    }">${s.current}${
      hot
        ? ' <ha-icon icon="mdi:fire" style="--mdc-icon-size:16px;color:var(--sei-warn, var(--sei-warn-auto, #ffb74d))"></ha-icon>'
        : ""
    }</div>
      <div style="font-size:11px;color:${
        s.personal_best ? "#f5b83d" : "var(--secondary-text-color, #8e8e93)"
      }">${
        s.personal_best ? "personal best!" : `best ${s.best}`
      }</div></div></div>`;
        })
        .join("");
      return `<div style="font-size:12px;color:var(--secondary-text-color, #8e8e93);white-space:normal;line-height:1.4">Shields come with each week-long run and absorb one break the weather caused.</div>${rows}`;
    },
  );

  defineCard(
    "solar-energy-insights-records",
    "Solar records",
    "Your best days, all time and this season.",
    function (states, hass) {
      const el = this;
      const {
        W,
        C,
        st,
        val,
        attr,
        num,
        hm,
        kwh,
        sub,
        head,
        pill,
        ws,
        waiting,
      } = window.SolarDash.ui(this, hass, states);

      const a = ws("ach", "solar_energy_insights/achievements", 300);
      const off = W.ach?.error && /disabled|off/i.test(W.ach.error);
      const TIER = [
        ["#cd7f32", "Bronze"],
        ["#c0c0c8", "Silver"],
        ["#f5b83d", "Gold"],
      ];
      const RANK = {
        Bronze: ["#cd7f32", "mdi:shield-outline"],
        Silver: ["#c0c0c8", "mdi:shield-half-full"],
        Gold: ["#f5b83d", "mdi:shield-star"],
        Platinum: [
          "var(--sei-info, var(--sei-info-auto, #9ad7e6))",
          "mdi:shield-crown",
        ],
        Diamond: ["#b9f2ff", "mdi:diamond-stone"],
      };
      const bar = (pct, color, h = 6) =>
        `<div style="height:${h}px;background:var(--sei-track, var(--divider-color, #2c2c2e));border-radius:${
          h / 2
        }px;overflow:hidden"><div style="width:${Math.max(
          0,
          Math.min(pct, 100),
        )}%;height:100%;background:${color};border-radius:${
          h / 2
        }px"></div></div>`;
      const dmy = (d) =>
        d
          ? new Date(d).toLocaleDateString("en-GB", {
              day: "numeric",
              month: "short",
              year: "2-digit",
            })
          : "";
      const notReady = () =>
        off
          ? sub(
              "Achievements are off: switch them on in the Solar Energy Insights options.",
            )
          : waiting("ach", "the trophy room");

      if (!a) return notReady();
      const clock = (m) =>
        `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(
          Math.round(m % 60),
        ).padStart(2, "0")}`;
      const fmt = (r) => (v) =>
        r.unit === "min"
          ? clock(v)
          : r.unit === "kW"
            ? v.toFixed(2) + " kW"
            : r.unit === "h"
              ? v.toFixed(0) + " h"
              : v.toFixed(1) + " kWh";
      const yieldToday = num(a.today.solar_kwh);
      const rows = a.records
        .map((r) => {
          const f = fmt(r);
          let chase = "";
          if (r.id === "best_day" && yieldToday !== null && r.value > 0) {
            const p = Math.min((yieldToday / r.value) * 100, 100);
            chase = `<div style="margin-top:5px">${bar(
              p,
              p >= 100
                ? "var(--sei-good, var(--sei-good-auto, #8be36a))"
                : "#f5b83d",
              4,
            )}</div><div style="font-size:10px;color:var(--disabled-text-color, #636366);margin-top:2px">today ${Math.round(
              p,
            )}% of the record</div>`;
          }
          const season =
            r.season_value !== null &&
            r.season_value !== undefined &&
            r.season_value !== r.value
              ? `this season ${f(r.season_value)}`
              : "";
          const top =
            r.last_top_pct !== null &&
            r.last_top_pct !== undefined &&
            r.last_top_pct <= 20
              ? `yesterday: top ${Math.max(1, Math.round(r.last_top_pct))}%`
              : "";
          return `<div style="display:grid;grid-template-columns:22px 1fr auto;gap:10px;align-items:start;margin-top:12px">
    <ha-icon icon="${
      r.icon
    }" style="--mdc-icon-size:18px;color:#f5b83d;margin-top:1px"></ha-icon>
    <div style="min-width:0"><div style="font-size:14px;color:var(--primary-text-color, #fff);white-space:normal">${
      r.name
    }</div>
      <div style="font-size:11px;color:var(--secondary-text-color, #8e8e93);white-space:normal;line-height:1.35">${dmy(
        r.day,
      )}${season ? " · " + season : ""}${
        top
          ? ` · <span style="color:var(--sei-good, var(--sei-good-auto, #8be36a))">${top}</span>`
          : ""
      }</div>${chase}</div>
    <div style="font-size:16px;color:var(--primary-text-color, #fff);text-align:right">${f(
      r.value,
    )}</div></div>`;
        })
        .join("");
      return rows || sub("No records yet.");
    },
  );

  defineCard(
    "solar-energy-insights-badges",
    "Solar badges",
    "Badges within reach, later in the year and complete; tap one to see what it takes.",
    function (states, hass) {
      const el = this;
      const {
        W,
        C,
        st,
        val,
        attr,
        num,
        hm,
        kwh,
        sub,
        head,
        pill,
        ws,
        waiting,
      } = window.SolarDash.ui(this, hass, states);

      const a = ws("ach", "solar_energy_insights/achievements", 300);
      const off = W.ach?.error && /disabled|off/i.test(W.ach.error);
      const TIER = [
        ["#cd7f32", "Bronze"],
        ["#c0c0c8", "Silver"],
        ["#f5b83d", "Gold"],
      ];
      const RANK = {
        Bronze: ["#cd7f32", "mdi:shield-outline"],
        Silver: ["#c0c0c8", "mdi:shield-half-full"],
        Gold: ["#f5b83d", "mdi:shield-star"],
        Platinum: [
          "var(--sei-info, var(--sei-info-auto, #9ad7e6))",
          "mdi:shield-crown",
        ],
        Diamond: ["#b9f2ff", "mdi:diamond-stone"],
      };
      const bar = (pct, color, h = 6) =>
        `<div style="height:${h}px;background:var(--sei-track, var(--divider-color, #2c2c2e));border-radius:${
          h / 2
        }px;overflow:hidden"><div style="width:${Math.max(
          0,
          Math.min(pct, 100),
        )}%;height:100%;background:${color};border-radius:${
          h / 2
        }px"></div></div>`;
      const dmy = (d) =>
        d
          ? new Date(d).toLocaleDateString("en-GB", {
              day: "numeric",
              month: "short",
              year: "2-digit",
            })
          : "";
      const notReady = () =>
        off
          ? sub(
              "Achievements are off: switch them on in the Solar Energy Insights options.",
            )
          : waiting("ach", "the trophy room");

      // Every badge appears in exactly one place.
      if (!a) return notReady();
      // In shared state, so a redraw from new data keeps the selection.
      const UI = (W.badgeUi = W.badgeUi || { spot: null });
      if (!el.__seiBadgeTaps) {
        el.__seiBadgeTaps = true;
        // A tap is a pointer down and up in about the same place, so a scroll isn't one.
        const hitOf = (ev) =>
          ev.composedPath().find((n) => n.dataset && n.dataset.badge);
        let down = null;
        el.addEventListener(
          "pointerdown",
          (ev) => {
            down = { x: ev.clientX, y: ev.clientY, hit: hitOf(ev) };
          },
          true,
        );
        el.addEventListener(
          "pointerup",
          (ev) => {
            const d = down;
            down = null;
            if (
              !d ||
              !d.hit ||
              Math.hypot(ev.clientX - d.x, ev.clientY - d.y) > 10
            )
              return;
            ev.stopPropagation();
            UI.spot = d.hit.dataset.badge;
            (W.listeners || []).forEach((f) => f());
          },
          true,
        );
        el.addEventListener(
          "click",
          (ev) => {
            if (hitOf(ev)) ev.stopPropagation();
          },
          true,
        );
      }
      const PIP = ["#cd7f32", "#c0c0c8", "#f5b83d"];
      const dm = (d) =>
        new Date(d + "T00:00").toLocaleDateString("en-GB", {
          day: "numeric",
          month: "short",
        });
      const named = (b) => b.name || "Hidden";
      const what = (b) => (b.name ? b.description : `“${b.riddle}”`);
      const tierOf = (b) => (b.tier ? ` (${b.tier} of ${b.tiers.length})` : "");
      const close = (b) => (b.next_tier_at ? b.count / b.next_tier_at : 0);
      const fmt = (n) => (n % 1 ? n.toFixed(1) : n);
      const now = a.badges
        .filter((b) => !b.complete && (!b.season || b.in_season))
        .sort((x, y) => !!y.season - !!x.season || close(y) - close(x));
      const later = a.badges
        .filter((b) => !b.complete && b.season && !b.in_season)
        .sort((x, y) =>
          (x.season_from || "").localeCompare(y.season_from || ""),
        );
      const done = a.badges
        .filter((b) => b.complete)
        .sort((x, y) =>
          (y.last_earned || "").localeCompare(x.last_earned || ""),
        );
      const SHOW = 3,
        rows = now.slice(0, SHOW),
        anyTime = now.slice(SHOW);
      const all = [...a.badges];
      const lit = all.find((b) => b.id === UI.spot) || done[0] || null;
      const icon = (b, bg, fg, size = 32) =>
        `<span style="width:${size}px;height:${size}px;border-radius:50%;background:${bg};display:flex;align-items:center;justify-content:center;flex-shrink:0;${
          b === lit ? "box-shadow:0 0 0 2px " + fg : ""
        }"><ha-icon icon="${b.icon}" style="--mdc-icon-size:${Math.round(
          size * 0.56,
        )}px;color:${fg}"></ha-icon></span>`;
      const reach = (b) => {
        const pct = Math.round(close(b) * 100);
        const when = b.season ? `until ${dm(b.season_until)} · ` : "";
        const progress =
          b.next_tier_at && b.count > 0
            ? `<div style="display:flex;gap:8px;align-items:center;margin-top:4px"><div style="flex:1;min-width:0">${bar(
                pct,
                "var(--sei-info, var(--sei-info-auto, #9ad7e6))",
                4,
              )}</div><span style="font-size:11px;color:${
                C.muted
              };white-space:nowrap">${fmt(b.count)} of ${
                b.next_tier_at
              }</span></div>`
            : "";
        return `<div style="display:flex;gap:10px;align-items:flex-start;padding:7px 0">${icon(
          b,
          b.season
            ? "color-mix(in srgb, var(--sei-warn, var(--sei-warn-auto, #ffb74d)) 18%, transparent)"
            : "color-mix(in srgb, var(--sei-info, var(--sei-info-auto, #9ad7e6)) 18%, transparent)",
          b.season
            ? "var(--sei-warn, var(--sei-warn-auto, #ffb74d))"
            : "var(--sei-info, var(--sei-info-auto, #9ad7e6))",
        )}
    <div style="flex:1;min-width:0"><div style="font-size:14px;color:${
      C.text
    }">${named(b)}${tierOf(b)}</div>
    <div style="font-size:11px;color:${
      C.muted
    };line-height:1.35;white-space:normal">${when}${what(
      b,
    )}</div>${progress}</div></div>`;
      };
      const strip = (label, badges, bg, fg, note, pips) =>
        !badges.length
          ? ""
          : `<div style="display:flex;gap:10px;align-items:flex-start;margin-top:12px">
  <div style="width:62px;font-size:11px;color:${C.muted};padding-top:8px;flex-shrink:0">${label}</div>
  <div style="display:flex;flex-wrap:wrap;gap:10px">` +
            badges
              .map(
                (b) =>
                  `<div data-badge="${
                    b.id
                  }" style="display:flex;flex-direction:column;align-items:center;gap:3px;cursor:pointer;min-width:32px">${icon(
                    b,
                    bg,
                    fg,
                  )}` +
                  (pips
                    ? `<span style="display:flex;gap:2px">${PIP.map(
                        (c) =>
                          `<i style="width:5px;height:5px;border-radius:50%;background:${c};display:block"></i>`,
                      ).join("")}</span>`
                    : "") +
                  (note
                    ? `<span style="font-size:9px;color:${
                        C.muted
                      };white-space:nowrap">${note(b)}</span>`
                    : "") +
                  `</div>`,
              )
              .join("") +
            `</div></div>`;
      const spot = (b) => {
        if (!b) return "";
        const name = `<b style="color:${C.text};font-weight:500">${named(
          b,
        )}</b>${b.complete ? "" : tierOf(b)} · ${what(b)}.`;
        if (b.complete)
          return (
            name +
            ` You've done it <b style="color:${C.text};font-weight:500">${fmt(
              b.count,
            )} time${b.count === 1 ? "" : "s"}</b>${
              b.last_earned ? ", most recently " + dm(b.last_earned) : ""
            } · all ${b.tiers.length} tiers.`
          );
        if (b.season && !b.in_season)
          return (
            name +
            ` Can be earned ${dm(b.season_from)} – ${dm(b.season_until)}.`
          );
        return (
          name + (b.count ? ` ${fmt(b.count)} so far.` : " Not earned yet.")
        );
      };
      let out = head(
        "mdi:medal-outline",
        "Badges",
        `${done.length} of ${a.badges.length} complete`,
      );
      const SECTIONS = (this._config || {}).sections || [
        "within_reach",
        "any_time",
        "later",
        "complete",
      ];
      if (SECTIONS.includes("within_reach")) out += rows.map(reach).join("");
      if (SECTIONS.includes("any_time"))
        out += strip(
          "Any time",
          anyTime,
          "color-mix(in srgb, var(--sei-info, var(--sei-info-auto, #9ad7e6)) 18%, transparent)",
          "var(--sei-info, var(--sei-info-auto, #9ad7e6))",
        );
      if (SECTIONS.includes("later"))
        out += strip(
          "Later",
          later,
          "var(--sei-track, var(--divider-color, #2c2c2e))",
          "var(--secondary-text-color, #8e8e93)",
          (b) => dm(b.season_from),
        );
      if (SECTIONS.includes("complete"))
        out += strip(
          "Complete",
          done,
          "color-mix(in srgb, var(--sei-good, var(--sei-good-auto, #8be36a)) 18%, transparent)",
          "var(--sei-good, var(--sei-good-auto, #8be36a))",
          null,
          true,
        );
      out += `<div style="font-size:12px;color:${
        C.muted
      };line-height:1.45;margin-top:12px;padding-top:10px;border-top:1px solid ${
        C.track
      };white-space:normal">${
        lit === done[0] && !UI.spot
          ? '<span style="font-size:11px;color:var(--disabled-text-color, #636366)">Latest · </span>'
          : ""
      }${spot(lit)}</div>`;
      return out;
    },
    FORMS.badges,
  );

  defineCard(
    "solar-energy-insights-now",
    "Solar now",
    "The day at a glance: battery, tonight, heavy loads and surplus, worded for the time of day.",
    function (states, hass) {
      // Solar status: four answers that change with the time of day, from Solar Energy Insights.
      // Tap a row → the card that explains it.
      const el = this;
      window.__tapHook =
        window.__tapHook && window.__tapHook.v === 3
          ? window.__tapHook
          : Object.assign(
              (el) => {
                if (!el || el.__tap) return;
                el.__tap = true;
                try {
                  const st = document.createElement("style");
                  st.textContent =
                    "[data-more-info],[data-tap]{pointer-events:auto!important;cursor:pointer}";
                  (el.shadowRoot || el).appendChild(st);
                } catch (x) {}
                let sx = 0,
                  sy = 0,
                  hit = null;
                const find = (ev) =>
                  ev
                    .composedPath()
                    .find(
                      (n) =>
                        n && n.dataset && (n.dataset.moreInfo || n.dataset.tap),
                    );
                const run = (h) => {
                  const d = h.dataset,
                    SD = window.SolarDash;
                  if (d.moreInfo)
                    el.dispatchEvent(
                      new CustomEvent("hass-more-info", {
                        bubbles: true,
                        composed: true,
                        detail: { entityId: d.moreInfo },
                      }),
                    );
                  else if (d.tap === "jump" && SD) SD.jump(d.arg);
                  else if (d.tap === "pick" && SD) SD.pickDay(d.arg);
                  else if (d.tap === "calmode" && SD) SD.setCalMode(h, d.arg);
                };
                el.addEventListener(
                  "touchstart",
                  (ev) => {
                    hit = find(ev);
                    if (!hit) return;
                    const t = ev.touches[0];
                    sx = t.clientX;
                    sy = t.clientY;
                    ev.stopPropagation();
                  },
                  { capture: true, passive: true },
                );
                el.addEventListener(
                  "touchend",
                  (ev) => {
                    const h = hit;
                    hit = null;
                    if (!h) return;
                    ev.stopPropagation();
                    const t = ev.changedTouches[0];
                    if (
                      Math.abs(t.clientX - sx) > 10 ||
                      Math.abs(t.clientY - sy) > 10
                    )
                      return;
                    if (ev.cancelable) ev.preventDefault();
                    run(h);
                  },
                  { capture: true },
                );
                el.addEventListener(
                  "click",
                  (ev) => {
                    const h = find(ev);
                    if (!h) return;
                    ev.stopPropagation();
                    ev.preventDefault();
                    run(h);
                  },
                  { capture: true },
                );
              },
              { v: 3 },
            );
      window.__tapHook(this);

      // The words come from the integration's Summary sensor: rows of a headline and
      // at most two clauses, chosen by the time of day. Here they are only drawn.
      const P = "solar_energy_insights_";
      const st = (id) => states[id];
      const val = (id) => {
        const s = st(id);
        return !s || ["unknown", "unavailable", ""].includes(s.state)
          ? null
          : s.state;
      };
      const attr = (id, a) => st(id)?.attributes?.[a];
      const num = (v) =>
        v === null || v === undefined || v === "" || isNaN(+v) ? null : +v;
      const summary = st("sensor." + P + "summary")?.attributes || null;
      const lines = (summary?.rows || []).map((r) => ({
        ...r,
        detail: (r.detail || []).join(" · "),
      }));
      // The rows' keys, for the card's `rows` setting: which, in what order.
      const ROW_KEY = {
        Battery: "battery",
        Tonight: "tonight",
        Today: "next_day",
        Tomorrow: "next_day",
        "Heavy loads": "loads",
        Surplus: "surplus",
        "Last night": "last_night",
      };
      const wanted = (this._config || {}).rows;
      if (Array.isArray(wanted) && wanted.length)
        lines.splice(
          0,
          lines.length,
          ...wanted.flatMap((k) => lines.filter((l) => ROW_KEY[l.label] === k)),
        );
      const targets = lines.map((r) => r.target);
      const SDX = window.SolarDash,
        ENT = ((SDX.config && SDX.config()) || {}).entities || {};
      const soc = summary?.soc ?? num(val(ENT.soc || ""));
      const g = summary
        ? {
            phase: summary.phase || st("sensor." + P + "summary")?.state,
            lines,
            targets,
            ring: summary.ring,
            home: summary.home,
            soc,
            label: summary.label,
            time: summary.time,
          }
        : null;

      if (!g)
        return `<div style="font-size:13px;color:var(--secondary-text-color, #8e8e93);white-space:normal">Waiting for Solar Energy Insights’ summary.</div>`;
      const C = {
        good: [
          "var(--sei-good, var(--sei-good-auto, #8be36a))",
          "color-mix(in srgb, var(--sei-good, var(--sei-good-auto, #8be36a)) 18%, transparent)",
        ],
        warn: [
          "var(--sei-warn, var(--sei-warn-auto, #ffb74d))",
          "color-mix(in srgb, var(--sei-warn, var(--sei-warn-auto, #ffb74d)) 18%, transparent)",
        ],
        bad: [
          "var(--sei-bad, var(--sei-bad-auto, #ff8a80))",
          "color-mix(in srgb, var(--sei-bad, var(--sei-bad-auto, #ff8a80)) 18%, transparent)",
        ],
        neutral: [
          "var(--secondary-text-color, #c7c7cc)",
          "var(--sei-track, var(--divider-color, #2c2c2e))",
        ],
      };
      // Explicit stretch + left alignment: button-card otherwise centres flex rows inside custom fields
      const ROW =
        "display:flex;align-items:center;justify-content:flex-start;justify-self:stretch;width:100%;box-sizing:border-box;text-align:left;gap:12px";
      const pill = (txt, c) =>
        `<span style="font-size:12px;color:${c[0]};background:${c[1]};border-radius:8px;padding:3px 9px;white-space:nowrap">${txt}</span>`;
      // Today's energy from the configured counters, hour by hour.
      const W = (window.__sei = window.__sei || {});
      if (!el.__seiListen) {
        el.__seiListen = true;
        (W.listeners = W.listeners || []).push(
          () => el.requestUpdate && el.requestUpdate(),
        );
      }
      const d0 = new Date(),
        todayIso = `${d0.getFullYear()}-${String(d0.getMonth() + 1).padStart(
          2,
          "0",
        )}-${String(d0.getDate()).padStart(2, "0")}`;
      if (
        (!W.todayH ||
          W.todayH.day !== todayIso ||
          Date.now() - W.todayH.t > 300e3) &&
        !W.todayHP &&
        hass?.callWS &&
        SDX.loadHourly
      ) {
        W.todayHP = SDX.loadHourly(hass, todayIso)
          .then((r) => {
            W.todayH = { day: todayIso, t: Date.now(), r };
          })
          .finally(() => {
            W.todayHP = null;
            (W.listeners || []).forEach((f) => f());
          });
      }
      const dayTotal = (k) => {
        const a = W.todayH?.r?.[k];
        return a ? a.reduce((s, v) => s + (v || 0), 0) : null;
      };
      const house = dayTotal("house");
      const imported = dayTotal("imp");
      const own =
        house && imported !== null
          ? Math.max(0, Math.min(100, ((house - imported) / house) * 100))
          : null;
      const ownC =
        own === null
          ? C.neutral
          : own >= 90
            ? C.good
            : own >= 60
              ? C.warn
              : C.bad;
      const gridKw = /^kW$/i.test(
        st(ENT.grid_power || "")?.attributes?.unit_of_measurement || "",
      );
      const gridW = (num(val(ENT.grid_power || "")) || 0) * (gridKw ? 1000 : 1);
      const yieldT = dayTotal("solar");
      const right =
        gridW > 100
          ? pill(`importing ${(gridW / 1000).toFixed(1)} kW`, C.bad)
          : g.phase === "late" && yieldT !== null
            ? pill(
                `today ${yieldT.toFixed(1)} kWh${
                  own !== null ? " · " + Math.round(own) + "% own" : ""
                }`,
                C.neutral,
              )
            : own !== null
              ? pill(`${Math.round(own)}% own power today`, ownC)
              : "";
      const rows = g.lines
        .map((l, i) => {
          const c = C[l.tone] || C.neutral;
          return `<div ${
            (this._config || {}).tap === "none" ? "" : 'data-tap="jump"'
          } data-arg="${
            g.targets[i]
          }" style="${ROW};cursor:pointer;padding:10px 0;${
            i
              ? "border-top:1px solid var(--sei-track, var(--divider-color, #2c2c2e))"
              : ""
          }">
    <div style="width:36px;height:36px;border-radius:50%;background:${
      c[1]
    };display:flex;align-items:center;justify-content:center;flex-shrink:0">
      <ha-icon icon="${l.icon}" style="--mdc-icon-size:19px;color:${
        c[0]
      }"></ha-icon></div>
    <div style="flex:1;min-width:0;text-align:left">
      <div style="font-size:11px;color:var(--secondary-text-color, #8e8e93);line-height:1.3">${
        l.label
      }</div>
      <div style="font-size:15px;color:var(--primary-text-color, #fff);font-weight:500;line-height:1.3;white-space:normal">${
        l.was
          ? `<span style="text-decoration:line-through;color:var(--disabled-text-color, #636366);font-weight:400;font-size:13px;margin-right:6px">${l.was}</span>`
          : ""
      }${l.main}</div>
      ${
        l.detail
          ? `<div style="font-size:12px;color:var(--secondary-text-color, #8e8e93);line-height:1.35;white-space:normal">${l.detail}</div>`
          : ""
      }
      ${
        l.change
          ? `<div style="display:inline-flex;align-items:center;gap:4px;margin-top:5px;font-size:11px;color:var(--sei-warn, var(--sei-warn-auto, #ffb74d));background:color-mix(in srgb, var(--sei-warn, var(--sei-warn-auto, #ffb74d)) 18%, transparent);border-radius:6px;padding:2px 7px;white-space:normal"><ha-icon icon="mdi:history" style="--mdc-icon-size:13px"></ha-icon>${l.change}</div>`
          : ""
      }
    </div>
    <ha-icon icon="mdi:chevron-right" style="--mdc-icon-size:18px;color:var(--disabled-text-color, #48484a);flex-shrink:0"></ha-icon></div>`;
        })
        .join("");
      return `${
        (this._config || {}).header === false
          ? ""
          : `<div style="${ROW};justify-content:space-between;margin-bottom:2px">
    <span style="font-size:13px;color:var(--secondary-text-color, #8e8e93)">${g.label} · ${g.time}</span>${right}</div>`
      }
  <div style="display:grid;justify-items:stretch;width:100%">${rows}</div>`;
    },
    FORMS.now,
  );

  defineCard(
    "solar-energy-insights-until-sunrise",
    "Until sunrise",
    "Your recent nights replayed from the battery as it is now, with your appliances.",
    function (states, hass) {
      const el = this;
      const {
        W,
        C,
        st,
        val,
        attr,
        num,
        hm,
        kwh,
        sub,
        head,
        pill,
        ws,
        waiting,
      } = window.SolarDash.ui(this, hass, states);

      // Appliances from the card's settings; a sensor that learns energy per run beats the typical figures.
      const APPS = ((el._config || {}).appliances || []).map((a, i) => {
        const e = a.energy_per_run ? st(a.energy_per_run) : null;
        const typical = +a.kwh || 1;
        let k = parseFloat(e?.state);
        const est = !(k > 0.05 && k <= 4 * typical);
        if (est) k = typical;
        const hrs = (e?.attributes?.hours || [])
          .filter((h) => h > 0.2 && h < 8)
          .slice(-5);
        return {
          id: "a" + i,
          name: a.name || "Appliance",
          icon: a.icon || "mdi:power-plug-outline",
          kwh: k,
          est: est && !!a.energy_per_run,
          h: hrs.length
            ? hrs.reduce((s, h) => s + h, 0) / hrs.length
            : +a.hours || 2,
        };
      });
      const sims = () => {
        if (!APPS.length) return null;
        const socNow = num(attr("sensor.solar_energy_insights_summary", "soc"));
        const key = [
          Math.round(socNow ?? -1),
          ...APPS.map((a) => a.kwh.toFixed(2)),
          val("sensor.solar_energy_insights_night_outlook"),
        ].join("|");
        const c = W.applianceRuns;
        if (
          (!c || c.key !== key || (!c.pending && Date.now() - c.t > 300e3)) &&
          !(c && c.pending && c.key === key) &&
          hass?.callWS
        ) {
          W.applianceRuns = {
            key,
            t: Date.now(),
            pending: true,
            data: c?.data,
          };
          const call = (list) =>
            hass
              .callWS({
                type: "call_service",
                domain: "solar_energy_insights",
                service: "simulate",
                service_data: {
                  extra_kwh: +list.reduce((s, a) => s + a.kwh, 0).toFixed(2),
                  extra_hours: +Math.max(...list.map((a) => a.h)).toFixed(2),
                },
                return_response: true,
              })
              .then((r) => r.response);
          Promise.all([
            ...APPS.map((a) => call([a])),
            APPS.length > 1 ? call(APPS) : null,
          ])
            .then((res) => {
              W.applianceRuns = {
                key,
                t: Date.now(),
                data: {
                  each: res.slice(0, APPS.length),
                  all: APPS.length > 1 ? res[APPS.length] : null,
                },
              };
            })
            .catch((e) => {
              W.applianceRuns = {
                key,
                t: Date.now(),
                error: String(e?.message || e),
              };
            })
            .finally(() => (W.listeners || []).forEach((f) => f()));
        }
        return W.applianceRuns?.data;
      };

      // Until sunrise, from Solar Energy Insights: your recent nights replayed from the battery as it
      // is now. Columns: a typical night and a heavy one; rows: with an appliance started now.
      const mark = window.SolarDash?.jumpMark
        ? window.SolarDash.jumpMark("sunrise")
        : "";
      const plan = ws("plan", "solar_energy_insights/plan", 60);
      if (!plan)
        return (
          mark +
          head("mdi:weather-night", "Until sunrise") +
          waiting("plan", "the plan")
        );
      if (!plan.night)
        return (
          mark +
          head("mdi:weather-night", "Until sunrise") +
          sub("Waiting for a day of history.")
        );
      const S = sims();
      const ROWS = [
        { label: "House only", icon: "mdi:home-outline", night: plan.night },
        ...APPS.map((a, i) => ({
          label: "+ " + a.name,
          icon: a.icon,
          night: S?.each?.[i]?.night,
        })),
        ...(APPS.length > 1
          ? [
              {
                label: APPS.length === 2 ? "+ Both" : "+ All",
                icon: "mdi:plus-circle-multiple-outline",
                night: S?.all?.night,
              },
            ]
          : []),
      ];
      const heavyPct =
        num(
          attr(
            "sensor.solar_energy_insights_night_prediction_error",
            "heavy_share_pct",
          ),
        ) ?? 80;
      const cell = (o) => {
        const base =
          "border-radius:8px;padding:6px 4px;text-align:center;font-size:13px;";
        if (!o)
          return `<div style="${base}background:${C.track};color:${C.faint}">…</div>`;
        return o.lasts
          ? `<div style="${base}background:color-mix(in srgb, var(--sei-good, var(--sei-good-auto, #8be36a)) 18%, transparent);color:${
              C.ok
            }">✓ ${o.spare_kwh.toFixed(1)}</div>`
          : `<div style="${base}background:color-mix(in srgb, var(--sei-bad, var(--sei-bad-auto, #ff8a80)) 18%, transparent);color:${
              C.bad
            }">grid ${hm(o.reserve_at)}</div>`;
      };
      const n = plan.night,
        outlook = n.outlook;
      const tag = (c, i, t) =>
        `<span style="color:${c}"><ha-icon icon="${i}" style="--mdc-icon-size:15px"></ha-icon> ${t}</span>`;
      const headline =
        outlook === "lasts"
          ? tag(C.ok, "mdi:check-circle", "Covered, even a heavy night")
          : outlook === "borderline"
            ? tag(
                C.heavy,
                "mdi:help-circle",
                "Covered unless it is a heavy night",
              )
            : tag(C.bad, "mdi:close-circle", "Grid needed before sunrise");
      const rise = new Date(plan.sunrise),
        nowD = new Date(plan.at),
        hours = Math.max((rise - nowD) / 3600e3, 0);
      const socNow = plan.curve?.[0]?.[1];
      const setPct = plan.charge?.sunset_pct,
        beforeSunset = new Date(plan.sunset) < rise;
      const colHead = (
        label,
        note,
      ) => `<div style="text-align:center;font-size:12px;color:var(--secondary-text-color, #c7c7cc)">${label}
  <div style="font-size:10px;color:${C.faint}">${note}</div></div>`;
      const COLS = (
        (this._config || {}).columns || ["typical", "heavy"]
      ).filter((c) => c === "typical" || c === "heavy");
      const rowHtml = (
        r,
      ) => `<div style="display:flex;align-items:center;justify-content:flex-start;justify-self:stretch;gap:8px;font-size:13px;color:var(--primary-text-color, #fff);min-width:0;text-align:left">
    <ha-icon icon="${r.icon}" style="--mdc-icon-size:18px;width:18px;color:${
      C.muted
    };flex-shrink:0"></ha-icon>
    <span style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${
      r.label
    }</span></div>
  ${COLS.map((k) => cell(r.night?.[k])).join("")}`;
      const RUN = (el._config || {}).running;
      const washerOn = !!RUN && val(RUN.entity) === String(RUN.state ?? "on");
      const est = APPS.filter((a) => a.est).map((a) => a.name.toLowerCase());
      const foot = [
        "✓ = kWh left above the reserve at the lowest point · grid = when the reserve is reached",
        APPS.map(
          (a) => `${a.name} ${a.kwh.toFixed(1)} kWh over ${a.h.toFixed(1)} h`,
        ).join(" · ") +
          (est.length
            ? ` (${est.join(" and ")} estimated until first run)`
            : ""),
        washerOn
          ? `${
              RUN.name || "An appliance"
            } is running – already in the battery level`
          : "",
        (() => {
          const sh =
            attr(
              "sensor.solar_energy_insights_night_outlook",
              "short_nights",
            ) || [];
          return sh.length
            ? "Ran short: " +
                sh
                  .slice(0, 4)
                  .map(
                    (x) =>
                      `${new Date(x.date).toLocaleDateString("en-GB", {
                        weekday: "short",
                        day: "numeric",
                        month: "short",
                      })} (${x.evening_kwh} kWh by midnight)`,
                  )
                  .join(" · ")
            : "";
        })(),
        S &&
        beforeSunset &&
        ROWS.slice(1).every(
          (r) =>
            r.night &&
            r.night.typical.spare_kwh === n.typical.spare_kwh &&
            r.night.heavy.lasts === n.heavy.lasts,
        )
          ? "Started now, in daylight, the sun covers them and the battery still fills: tonight is the same."
          : "",
        `Replays your last ${n.nights} nights from the battery as it is now; ${n.lasting} of them last`,
        S === undefined && W.applianceRuns?.error
          ? "Could not simulate the appliances: " + W.applianceRuns.error
          : "",
      ]
        .filter(Boolean)
        .map((t) => `<div>${t}</div>`)
        .join("");
      return `${mark}
  <div style="display:flex;justify-content:space-between;align-items:baseline;gap:8px;flex-wrap:wrap">
    <div style="font-size:17px;color:var(--primary-text-color, #fff);font-weight:500">Until sunrise</div>
    <div style="font-size:13px">${headline}</div></div>
  <div style="font-size:12px;color:${C.muted};margin-top:2px">
    ${hm(rise)} · ${Math.floor(hours)} h ${String(
      Math.round((hours % 1) * 60),
    ).padStart(2, "0")} min · ${
      socNow !== undefined ? Math.round(socNow) + "% now" : ""
    }${
      beforeSunset && setPct !== null && setPct !== undefined
        ? ` · ${setPct}% at sunset`
        : ""
    }</div>
  <div style="display:grid;grid-template-columns:minmax(0,1.4fr)${" minmax(0,1fr)".repeat(
    COLS.length,
  )};gap:6px 8px;align-items:center;justify-items:stretch;margin-top:14px">
    <div style="font-size:11px;color:${
      C.muted
    };text-align:left">If you run…</div>
    ${
      COLS.includes("typical") ? colHead("Typical night", "the middle one") : ""
    }${
      COLS.includes("heavy")
        ? colHead("Heavy night", `heavier than ${heavyPct}%`)
        : ""
    }
    ${ROWS.map(rowHtml).join("")}
  </div>${
    (this._config || {}).footnotes === false
      ? ""
      : `<div style="font-size:10px;color:${C.faint};margin-top:12px;line-height:1.5;white-space:normal;text-align:left">${foot}</div>`
  }`;
    },
    FORMS.untilSunrise,
  );

  defineCard(
    "solar-energy-insights-best-time",
    "Best time to run",
    "When spare sun is coming, and when to start your appliances.",
    function (states, hass) {
      const el = this;
      const {
        W,
        C,
        st,
        val,
        attr,
        num,
        hm,
        kwh,
        sub,
        head,
        pill,
        ws,
        waiting,
      } = window.SolarDash.ui(this, hass, states);

      // Appliances from the card's settings; a sensor that learns energy per run beats the typical figures.
      const APPS = ((el._config || {}).appliances || []).map((a, i) => {
        const e = a.energy_per_run ? st(a.energy_per_run) : null;
        const typical = +a.kwh || 1;
        let k = parseFloat(e?.state);
        const est = !(k > 0.05 && k <= 4 * typical);
        if (est) k = typical;
        const hrs = (e?.attributes?.hours || [])
          .filter((h) => h > 0.2 && h < 8)
          .slice(-5);
        return {
          id: "a" + i,
          name: a.name || "Appliance",
          icon: a.icon || "mdi:power-plug-outline",
          kwh: k,
          est: est && !!a.energy_per_run,
          h: hrs.length
            ? hrs.reduce((s, h) => s + h, 0) / hrs.length
            : +a.hours || 2,
        };
      });
      const sims = () => {
        if (!APPS.length) return null;
        const socNow = num(attr("sensor.solar_energy_insights_summary", "soc"));
        const key = [
          Math.round(socNow ?? -1),
          ...APPS.map((a) => a.kwh.toFixed(2)),
          val("sensor.solar_energy_insights_night_outlook"),
        ].join("|");
        const c = W.applianceRuns;
        if (
          (!c || c.key !== key || (!c.pending && Date.now() - c.t > 300e3)) &&
          !(c && c.pending && c.key === key) &&
          hass?.callWS
        ) {
          W.applianceRuns = {
            key,
            t: Date.now(),
            pending: true,
            data: c?.data,
          };
          const call = (list) =>
            hass
              .callWS({
                type: "call_service",
                domain: "solar_energy_insights",
                service: "simulate",
                service_data: {
                  extra_kwh: +list.reduce((s, a) => s + a.kwh, 0).toFixed(2),
                  extra_hours: +Math.max(...list.map((a) => a.h)).toFixed(2),
                },
                return_response: true,
              })
              .then((r) => r.response);
          Promise.all([
            ...APPS.map((a) => call([a])),
            APPS.length > 1 ? call(APPS) : null,
          ])
            .then((res) => {
              W.applianceRuns = {
                key,
                t: Date.now(),
                data: {
                  each: res.slice(0, APPS.length),
                  all: APPS.length > 1 ? res[APPS.length] : null,
                },
              };
            })
            .catch((e) => {
              W.applianceRuns = {
                key,
                t: Date.now(),
                error: String(e?.message || e),
              };
            })
            .finally(() => (W.listeners || []).forEach((f) => f()));
        }
        return W.applianceRuns?.data;
      };

      // Best time to run: spare sun per half hour until sunset, and for each appliance the start that
      // takes the most from sun that would otherwise be exported, against starting now.
      const plan = ws("plan", "solar_energy_insights/plan", 60);
      if (!plan)
        return (
          head("mdi:clock-check-outline", "Best time to run") +
          waiting("plan", "the plan")
        );
      const slots = (plan.day?.slots || []).map(([t, pv, load]) => ({
        t: new Date(t).getTime(),
        spare: pv - load,
      }));
      if (!slots.length)
        return (
          head("mdi:clock-check-outline", "Best time to run") +
          sub("No sun left before sunset.")
        );
      const limit = plan.charge_limit_pct ?? 100;
      const curve = (plan.curve || []).map(([t, v]) => [
        new Date(t).getTime(),
        v,
      ]);
      const levelAt = (t) => {
        let v = curve.length ? curve[0][1] : 0;
        for (const [ct, cv] of curve) {
          if (ct <= t) v = cv;
          else break;
        }
        return v;
      };
      // Sun to spare once the battery is full would be exported: that is the free part.
      slots.forEach((s) => {
        s.full = levelAt(s.t + 1) >= limit - 0.5;
        s.free = s.full ? Math.max(s.spare, 0) : 0;
      });
      const nowMs = new Date(plan.at).getTime();
      const tomorrow =
        new Date(slots[0].t).toDateString() !==
        new Date(plan.at).toDateString();
      // Before sunrise there is no "now" to compare with: the day has not started.
      const beforeSun = nowMs < slots[0].t;
      const share = (a, k) => {
        const n = Math.max(1, Math.round(a.h * 2)),
          kw = a.kwh / (n / 2);
        let got = 0;
        for (let j = k; j < Math.min(k + n, slots.length); j++)
          got += Math.min(slots[j].free, kw) / 2;
        return Math.min(got / a.kwh, 1);
      };
      const firstNow = Math.max(
        0,
        slots.findIndex((s) => s.t + 1800e3 > nowMs),
      );
      const plans = APPS.map((a) => {
        let best = firstNow,
          bestShare = -1;
        for (let k = firstNow; k < slots.length; k++) {
          const v = share(a, k);
          if (v > bestShare + 1e-9) {
            best = k;
            bestShare = v;
          }
        }
        return {
          a,
          best: slots[best].t,
          bestShare,
          nowShare: tomorrow || beforeSun ? null : share(a, firstNow),
        };
      });
      const maxAbs = Math.max(...slots.map((s) => Math.abs(s.spare)), 0.5);
      const Wd = 340,
        H = 90,
        mid = 58,
        bw = Wd / slots.length;
      const win = plan.best_window
        ? [
            new Date(plan.best_window.start).getTime(),
            new Date(plan.best_window.end).getTime(),
          ]
        : null;
      const X = (t) => ((t - slots[0].t) / 1800e3) * bw;
      const bars = slots
        .map((s, k) => {
          const h =
            (Math.abs(s.spare) / maxAbs) *
            (s.spare >= 0 ? mid - 6 : H - mid - 14);
          const color = s.spare < 0 ? C.faint : s.full ? C.ok : C.heavy;
          return `<rect x="${(k * bw + 1).toFixed(1)}" y="${(s.spare >= 0
            ? mid - h
            : mid
          ).toFixed(1)}" width="${Math.max(bw - 2, 1).toFixed(
            1,
          )}" height="${Math.max(h, 1).toFixed(1)}" rx="1.5" fill="${color}"/>`;
        })
        .join("");
      const shade = win
        ? `<rect x="${X(win[0]).toFixed(1)}" y="0" width="${(
            X(win[1]) - X(win[0])
          ).toFixed(1)}" height="${mid}" fill="${C.typical}" opacity=".12"/>`
        : "";
      const nowX =
        !tomorrow && nowMs > slots[0].t
          ? `<line x1="${X(nowMs)}" y1="0" x2="${X(nowMs)}" y2="${
              H - 12
            }" stroke="var(--primary-text-color, #fff)" stroke-opacity=".5" stroke-dasharray="2 2"/>`
          : "";
      const svg = `<svg viewBox="0 0 ${Wd} ${H}" style="width:100%;display:block;margin-top:4px">${shade}
  <line x1="0" y1="${mid}" x2="${Wd}" y2="${mid}" stroke="${
    C.track
  }"/>${bars}${nowX}
  <text x="0" y="${H - 1}" fill="${C.muted}" font-size="10">${hm(
    slots[0].t,
  )}</text>
  <text x="${Wd}" y="${H - 1}" fill="${
    C.muted
  }" font-size="10" text-anchor="end">sunset ${hm(plan.sunset)}</text></svg>`;
      const pct = (v) => Math.round(v * 100) + "%";
      const anyFree = slots.some((s) => s.free > 0.2);
      const rows = plans
        .map(
          (
            p,
          ) => `<div style="display:grid;grid-template-columns:24px minmax(0,1fr) auto;gap:8px;align-items:center;margin:8px 0;font-size:13px;color:${
            C.text
          }">
  <ha-icon icon="${p.a.icon}" style="--mdc-icon-size:18px;color:${
    C.muted
  }"></ha-icon>
  <div style="min-width:0"><div>${p.a.name} <span style="color:${
    C.muted
  };font-size:11px">${p.a.kwh.toFixed(1)} kWh · ${p.a.h.toFixed(
    1,
  )} h</span></div>
    ${
      p.nowShare !== null
        ? `<div style="font-size:11px;color:${C.muted}">now: ${pct(
            p.nowShare,
          )} from free sun</div>`
        : ""
    }</div>
  <div style="text-align:right">${
    p.bestShare > 0.05
      ? `<div>start ${hm(p.best)}${
          tomorrow ? " tmrw" : ""
        }</div><div style="font-size:11px;color:${C.ok}">${pct(
          p.bestShare,
        )} free</div>`
      : `<div style="color:${C.muted}">any time</div>`
  }</div></div>`,
        )
        .join("");
      return (
        head(
          "mdi:clock-check-outline",
          "Best time to run",
          tomorrow ? "tomorrow" : beforeSun ? "from sunrise" : "until sunset",
        ) +
        ((this._config || {}).chart === false
          ? ""
          : svg +
            sub(
              `<span style="color:${C.ok}">Green</span>: sun to spare with the battery full, which would be exported · <span style="color:${C.heavy}">amber</span>: it would charge the battery · shaded: the best window.`,
            )) +
        rows +
        (!APPS.length
          ? ""
          : anyFree
            ? APPS.length > 1
              ? sub(
                  "Each on its own; running them together shares the same spare sun.",
                )
              : ""
            : sub(
                "No free sun expected: whenever they run, they use energy the battery would otherwise keep for tonight.",
              ))
      );
    },
    FORMS.bestTime,
  );

  defineCard(
    "solar-energy-insights-forecast-trust",
    "Can I trust the forecast?",
    "How far the forecast held at 18:00, 21:00, 06:00 and 12:00.",
    function (states, hass) {
      const el = this;
      const { C, attr, head, sub } = window.SolarDash.ui(this, hass, states);
      const ID = "sensor.solar_energy_insights_forecast_checkpoints";
      const v = states[ID]?.attributes;
      if (!v)
        return (
          head("mdi:target-account", "Can I trust the forecast?") +
          sub("Waiting for Solar Energy Insights.")
        );
      if (v.needs_solar_counter)
        return (
          head("mdi:target-account", "Can I trust the forecast?") +
          sub(
            "Needs the solar energy counter: Settings › Devices & services › Solar Energy Insights › Configure › Solar energy.",
          )
        );
      const k = (x) =>
        x === null || x === undefined
          ? "–"
          : x >= 10
            ? x.toFixed(0)
            : x.toFixed(1);
      const pct = (x) => Math.round(x * 100) + "%";
      const S = v.scores || {};
      const ROWS = [
        ["18:00", "for tomorrow"],
        ["21:00", "for tomorrow"],
        ["06:00", "for today"],
        ["12:00", "for the afternoon"],
      ];
      const only = (this._config || {}).checkpoints;
      const SHOWN =
        Array.isArray(only) && only.length
          ? ROWS.filter(([n]) => only.includes(n))
          : ROWS;
      const lean = (b) =>
        Math.abs(b) < 0.3
          ? "no clear lean"
          : b > 0
            ? `${k(b)} kWh optimistic`
            : `${k(-b)} kWh pessimistic`;
      const row = ([name, what]) => {
        const s = S[name] || { days: 0 };
        if (s.typical_kwh === null || s.typical_kwh === undefined)
          return `<div style="display:flex;justify-content:space-between;padding:7px 0;border-top:1px solid ${
            C.track
          }">
      <span style="color:${C.text}">${name} <span style="color:${
        C.muted
      };font-size:12px">${what}</span></span>
      <span style="color:${C.muted};font-size:12px">collecting: ${
        s.days || 0
      } of 10 days</span></div>`;
        const groups = ["sunny", "mixed", "dull"]
          .map((g) =>
            s.groups?.[g]?.typical_kwh != null
              ? `${g} ±${k(s.groups[g].typical_kwh)}`
              : "",
          )
          .filter(Boolean)
          .join(" · ");
        const ly =
          s.last_year?.typical_kwh != null
            ? `this time last year ±${k(s.last_year.typical_kwh)}`
            : "";
        return `<div style="padding:7px 0;border-top:1px solid ${C.track}">
    <div style="display:flex;justify-content:space-between;align-items:baseline">
      <span style="color:${C.text}">${name} <span style="color:${
        C.muted
      };font-size:12px">${what}</span></span>
      <span style="color:${C.text};font-size:15px">±${k(s.typical_kwh)} kWh${
        s.typical_share != null
          ? ` <span style="color:${C.muted};font-size:11px">(${pct(
              s.typical_share,
            )} of a day)</span>`
          : ""
      }</span></div>
    <div style="font-size:11px;color:${
      C.muted
    };line-height:1.45;margin-top:2px;white-space:normal">
      ${[lean(s.bias_kwh), `${pct(s.in_range)} inside its range`, groups, ly]
        .filter(Boolean)
        .join(" · ")}</div></div>`;
      };
      const wait = (label, w, early, late) =>
        !w || w.pays === null
          ? ""
          : w.pays
            ? `Waiting from ${early} to ${late} cuts the typical miss from ±${k(
                w.early_kwh,
              )} to ±${k(w.late_kwh)} kWh.`
            : `Waiting from ${early} to ${late} barely helps (±${k(
                w.early_kwh,
              )} → ±${k(w.late_kwh)} kWh).`;
      const waits = [
        wait("", S.waiting?.["21:00 vs 18:00"], "18:00", "21:00"),
        wait("", S.waiting?.["12:00 vs 06:00"], "06:00", "12:00"),
      ].filter(Boolean);
      const frozen = (when, label) =>
        Object.entries(v[when] || {})
          .sort()
          .map(
            ([n, f]) =>
              `${n} ${k(f.forecast_kwh)} kWh${
                n === "12:00" ? " from noon" : ""
              }`,
          )
          .join(" · ");
      const today = frozen("today"),
        tomorrow = frozen("tomorrow");
      const days = (S["06:00"] || {}).days || 0;
      return (
        head(
          "mdi:target-account",
          "Can I trust the forecast?",
          days ? `${days} days scored` : "",
        ) +
        SHOWN.map(row).join("") +
        ((this._config || {}).footnotes === false
          ? ""
          : (waits.length ? sub(waits.join("<br>")) : "") +
            sub(
              [
                today ? `Frozen for today: ${today}` : "",
                tomorrow ? `for tomorrow: ${tomorrow}` : "",
              ]
                .filter(Boolean)
                .join(" · ") +
                "<br>± is the typical miss over the last 30 days; the range is Solcast’s low-to-high.",
            ))
      );
    },
    FORMS.forecastTrust,
  );

  defineCard(
    "solar-energy-insights-battery-full",
    "Battery full",
    "When the battery fills, the export after, the cloudy case and your appliances started now.",
    function (states, hass) {
      const el = this;
      const {
        W,
        C,
        st,
        val,
        attr,
        num,
        hm,
        kwh,
        sub,
        head,
        pill,
        ws,
        waiting,
      } = window.SolarDash.ui(this, hass, states);

      // Appliances from the card's settings; a sensor that learns energy per run beats the typical figures.
      const APPS = ((el._config || {}).appliances || []).map((a, i) => {
        const e = a.energy_per_run ? st(a.energy_per_run) : null;
        const typical = +a.kwh || 1;
        let k = parseFloat(e?.state);
        const est = !(k > 0.05 && k <= 4 * typical);
        if (est) k = typical;
        const hrs = (e?.attributes?.hours || [])
          .filter((h) => h > 0.2 && h < 8)
          .slice(-5);
        return {
          id: "a" + i,
          name: a.name || "Appliance",
          icon: a.icon || "mdi:power-plug-outline",
          kwh: k,
          est: est && !!a.energy_per_run,
          h: hrs.length
            ? hrs.reduce((s, h) => s + h, 0) / hrs.length
            : +a.hours || 2,
        };
      });
      const sims = () => {
        if (!APPS.length) return null;
        const socNow = num(attr("sensor.solar_energy_insights_summary", "soc"));
        const key = [
          Math.round(socNow ?? -1),
          ...APPS.map((a) => a.kwh.toFixed(2)),
          val("sensor.solar_energy_insights_night_outlook"),
        ].join("|");
        const c = W.applianceRuns;
        if (
          (!c || c.key !== key || (!c.pending && Date.now() - c.t > 300e3)) &&
          !(c && c.pending && c.key === key) &&
          hass?.callWS
        ) {
          W.applianceRuns = {
            key,
            t: Date.now(),
            pending: true,
            data: c?.data,
          };
          const call = (list) =>
            hass
              .callWS({
                type: "call_service",
                domain: "solar_energy_insights",
                service: "simulate",
                service_data: {
                  extra_kwh: +list.reduce((s, a) => s + a.kwh, 0).toFixed(2),
                  extra_hours: +Math.max(...list.map((a) => a.h)).toFixed(2),
                },
                return_response: true,
              })
              .then((r) => r.response);
          Promise.all([
            ...APPS.map((a) => call([a])),
            APPS.length > 1 ? call(APPS) : null,
          ])
            .then((res) => {
              W.applianceRuns = {
                key,
                t: Date.now(),
                data: {
                  each: res.slice(0, APPS.length),
                  all: APPS.length > 1 ? res[APPS.length] : null,
                },
              };
            })
            .catch((e) => {
              W.applianceRuns = {
                key,
                t: Date.now(),
                error: String(e?.message || e),
              };
            })
            .finally(() => (W.listeners || []).forEach((f) => f()));
        }
        return W.applianceRuns?.data;
      };

      // Battery full forecast, from Solar Energy Insights: Solcast minus the house as your recent
      // days used power, until the next sunset. In the evening it runs through the night into tomorrow.
      const mark = window.SolarDash?.jumpMark
        ? window.SolarDash.jumpMark("battery")
        : "";
      const plan = ws("plan", "solar_energy_insights/plan", 60);
      if (!plan) return mark + waiting("plan", "the plan");
      const ch = plan.charge,
        day = plan.day || {},
        limit = plan.charge_limit_pct ?? 100;
      const curve = (plan.curve || []).map(([t, v]) => [
        new Date(t).getTime(),
        v,
      ]);
      const cloudy = (day.curve_cloudy || []).map(([t, v]) => [
        new Date(t).getTime(),
        v,
      ]);
      if (curve.length < 2) return mark + sub("No Solcast forecast available.");
      const soc = curve[0][1],
        end = new Date(plan.sunset),
        nowD = new Date(plan.at);
      const tomorrow = end.toDateString() !== nowD.toDateString();
      const full = soc >= limit - 0.5;
      let headTxt, headCol, icon;
      if (full) {
        headTxt = "Battery full now";
        headCol = C.ok;
        icon = "mdi:battery-check";
      } else if (ch.full_at) {
        headTxt = `Battery full ~${hm(ch.full_at)}${
          tomorrow ? " tomorrow" : ""
        }`;
        headCol = C.ok;
        icon = "mdi:battery-charging-high";
      } else {
        headTxt = `Won’t fill ${tomorrow ? "tomorrow" : "today"} · peaks at ${
          ch.peak_pct
        }%`;
        headCol = C.heavy;
        icon = "mdi:battery-charging-50";
      }
      const s = plan.surplus || {};
      const expTxt =
        ch.full_at || full
          ? s.after_full_kwh > 0.05
            ? `then ~${
                s.after_full_kwh >= 10
                  ? s.after_full_kwh.toFixed(0)
                  : s.after_full_kwh.toFixed(1)
              } kWh export until sunset`
            : "no export after full"
          : s.while_charging_kwh > 0.3
            ? `~${s.while_charging_kwh.toFixed(1)} kWh exported while charging`
            : "no export expected";
      const floor = num(plan.floor_pct) ?? 5;
      const Wc = 300,
        Hc = 56,
        t0 = curve[0][0],
        t1 = curve[curve.length - 1][0];
      const X = (ms) => ((ms - t0) / Math.max(t1 - t0, 1)) * Wc,
        Y = (v) => Hc - (Math.max(Math.min(v, 100), 0) / 100) * Hc;
      const line = (pts) =>
        pts
          .map(([ms, v]) => `${X(ms).toFixed(1)},${Y(v).toFixed(1)}`)
          .join(" ");
      const fx = ch.full_at ? X(new Date(ch.full_at).getTime()) : null;
      const chart = `<svg viewBox="0 -4 ${Wc} ${
        Hc + 20
      }" style="width:100%;height:auto;margin-top:10px;overflow:visible">
  <line x1="0" y1="${Y(floor)}" x2="${Wc}" y2="${Y(
    floor,
  )}" stroke="#ff6b6b" stroke-opacity=".5" stroke-dasharray="3 3"/>
  <line x1="0" y1="${Y(limit)}" x2="${Wc}" y2="${Y(limit)}" stroke="#3a3a3c"/>
  ${
    cloudy.length > 1
      ? `<polyline points="${line(
          cloudy,
        )}" fill="none" stroke="#8be36a" stroke-opacity=".35" stroke-width="1.5" stroke-dasharray="4 3"/>`
      : ""
  }
  <polyline points="${line(
    curve,
  )}" fill="none" stroke="#8be36a" stroke-width="2.5" stroke-linejoin="round"/>
  ${
    fx !== null && fx >= 0 && fx <= Wc
      ? `<circle cx="${fx.toFixed(1)}" cy="${Y(limit)}" r="4" fill="#8be36a"/>`
      : ""
  }
  <text x="0" y="${Hc + 15}" fill="#8e8e93" font-size="10">now ${Math.round(
    soc,
  )}%</text>
  <text x="${Wc}" y="${
    Hc + 15
  }" fill="#8e8e93" font-size="10" text-anchor="end">sunset ${hm(end)}${
    tomorrow ? " tmrw" : ""
  } · ${ch.sunset_pct ?? "–"}%</text>
</svg>`;
      const S = sims();
      const both = (APPS.length > 1 ? S?.all : S?.each?.[0])?.charge;
      const started = `with ${APPS.map((a) => a.name.toLowerCase()).join(
        " + ",
      )} started now`;
      const withApp =
        full || !APPS.length
          ? ""
          : !both
            ? W.applianceRuns?.error
              ? ""
              : `${started}: working it out…`
            : both.full_at
              ? `${started}: full ~${hm(both.full_at)}`
              : `${started}: peaks at ${both.peak_pct}%`;
      // Overnight the low case's peak is just the level now: it means the battery does not fill.
      const cloudyFills = ch.peak_pct_cloudy > soc + 0.5;
      const cloudyTxt = full
        ? ""
        : ch.full_at
          ? ch.full_at_cloudy
            ? `if cloudier (Solcast low case): full ~${hm(ch.full_at_cloudy)}`
            : cloudyFills
              ? `if cloudier (Solcast low case): only ${ch.peak_pct_cloudy}%`
              : "if cloudier (Solcast low case): it would not fill"
          : cloudyFills && ch.peak_pct_cloudy < ch.peak_pct - 1
            ? `low case: peaks at ${ch.peak_pct_cloudy}%`
            : "";
      const rows = [
        day.pv_kwh !== undefined
          ? `Solar still to come <span style="color:#fff">${day.pv_kwh.toFixed(
              1,
            )} kWh</span> · house needs ~<span style="color:#fff">${day.load_kwh.toFixed(
              1,
            )} kWh</span> until sunset`
          : "",
        (ch.full_at || full) && s.while_charging_kwh > 0.3
          ? `+ ~${s.while_charging_kwh.toFixed(
              1,
            )} kWh exported before full (charging power-limited)`
          : "",
        day.reserve_at
          ? `<span style="color:${C.bad}">Reserve reached ~${hm(
              day.reserve_at,
            )} · from the grid until the sun takes over</span>`
          : "",
        cloudyTxt,
        withApp,
      ]
        .filter(Boolean)
        .map((t) => `<div>${t}</div>`)
        .join("");
      const hidden = attr(
        "sensor.solar_energy_insights_night_prediction_error",
        "hidden_load_w",
      );
      const nights = plan.night?.nights;
      return `${mark}<div style="display:flex;align-items:center;gap:10px">
    <ha-icon icon="${icon}" style="--mdc-icon-size:22px;color:${headCol};flex-shrink:0"></ha-icon>
    <div style="min-width:0"><div style="font-size:17px;color:#fff;font-weight:500">${headTxt}</div>
      <div style="font-size:13px;color:${
        ch.full_at ? "#c7c7cc" : C.muted
      }">${expTxt}</div></div></div>
  ${chart}
  <div style="display:grid;gap:4px;font-size:12px;color:${
    C.muted
  };margin-top:8px;white-space:normal;line-height:1.4">${rows}</div>
  <div style="font-size:10px;color:${
    C.faint
  };margin-top:8px;white-space:normal">Solar Energy Insights · Solcast minus the house as your last ${
    nights ?? "–"
  } days used power${
    hidden ? ` · ${hidden} W the house meter does not see` : ""
  }${
    limit < 100 ? ` · charge limit ${Math.round(limit)}%` : ""
  } · dashed = Solcast low case</div>`;
    },
    FORMS.batteryFull,
  );

  defineCard(
    "solar-energy-insights-tonight-replayed",
    "Tonight, replayed",
    "Your recent nights drawn from the battery as it is now.",
    function (states, hass) {
      const el = this;
      const {
        W,
        C,
        st,
        val,
        attr,
        num,
        hm,
        kwh,
        sub,
        head,
        pill,
        ws,
        waiting,
      } = window.SolarDash.ui(this, hass, states);

      const plan = ws("plan", "solar_energy_insights/plan", 60);
      const outlook = val("sensor.solar_energy_insights_night_outlook");
      const outlookPill =
        outlook === "lasts"
          ? pill("Lasts", C.ok)
          : outlook === "borderline"
            ? pill("Borderline", C.heavy)
            : outlook === "grid"
              ? pill("Needs the grid", C.bad)
              : "";
      if (!plan)
        return (
          head("mdi:weather-night", "Tonight, replayed", outlookPill) +
          waiting("plan", "the plan")
        );
      const night = plan.night;
      if (!night || !night.curves)
        return (
          head("mdi:weather-night", "Tonight, replayed", outlookPill) +
          sub("Waiting for a day of history.")
        );
      const times = night.curves.times.map((t) => new Date(t).getTime());
      const rows = night.curves.levels,
        each = night.each;
      const t0 = times[0],
        t1 = times[times.length - 1];
      const Wd = 340,
        H = 150,
        L = 4,
        R = 4,
        T = 8,
        B = 22;
      const x = (t) => L + ((t - t0) / (t1 - t0)) * (Wd - L - R);
      const y = (p) => T + ((100 - p) / 100) * (H - T - B);
      const path = (row) =>
        row
          .map(
            (p, k) =>
              `${k ? "L" : "M"}${x(times[k]).toFixed(1)} ${y(p).toFixed(1)}`,
          )
          .join(" ");
      const failed = rows.filter((_, k) => !each[k].lasts);
      const floor = failed.length
        ? Math.min(...failed.map((r) => Math.min(...r)))
        : null;
      const typicalK = each.findIndex(
        (o) => o.days_ago === night.typical.days_ago,
      );
      const heavyK = each.findIndex((o) => o.days_ago === night.heavy.days_ago);
      const sunset = new Date(plan.sunset).getTime();
      const marks = [];
      if (sunset > t0 && sunset < t1)
        marks.push(`<line x1="${x(sunset)}" y1="${T}" x2="${x(sunset)}" y2="${
          H - B
        }" stroke="${C.faint}" stroke-dasharray="2 3"/>
  <text x="${x(sunset) + 3}" y="${T + 10}" fill="${
    C.muted
  }" font-size="10">sunset</text>`);
      if (floor !== null)
        marks.push(`<line x1="${L}" y1="${y(floor)}" x2="${Wd - R}" y2="${y(
          floor,
        )}" stroke="${C.bad}" stroke-dasharray="4 3"/>
  <text x="${L + 2}" y="${y(floor) - 4}" fill="${
    C.bad
  }" font-size="10">reserve ${Math.round(floor)} %</text>`);
      const thin = rows
        .map((r, k) =>
          k === typicalK || k === heavyK
            ? ""
            : `<path d="${path(r)}" fill="none" stroke="${
                each[k].lasts ? C.muted : C.bad
              }" stroke-width="1" opacity="0.45"/>`,
        )
        .join("");
      const svg = `<svg viewBox="0 0 ${Wd} ${H}" style="width:100%;display:block">
  ${[0, 50, 100]
    .map(
      (p) =>
        `<line x1="${L}" y1="${y(p)}" x2="${Wd - R}" y2="${y(p)}" stroke="${
          C.track
        }"/>`,
    )
    .join("")}
  ${marks.join("")}${thin}
  ${
    heavyK >= 0
      ? `<path d="${path(rows[heavyK])}" fill="none" stroke="${
          C.heavy
        }" stroke-width="2.5"/>`
      : ""
  }
  ${
    typicalK >= 0
      ? `<path d="${path(rows[typicalK])}" fill="none" stroke="${
          C.typical
        }" stroke-width="2.5"/>`
      : ""
  }
  <text x="${L}" y="${H - 6}" fill="${C.muted}" font-size="10">now ${hm(
    t0,
  )}</text>
  <text x="${Wd - R}" y="${H - 6}" fill="${
    C.muted
  }" font-size="10" text-anchor="end">sunrise ${hm(t1)}</text>
</svg>`;
      const covered = val("sensor.solar_energy_insights_nights_covered");
      const heavyNote = night.heavy.lasts
        ? `keeps ${kwh(night.heavy.spare_kwh)}`
        : `hits the reserve ${hm(night.heavy.reserve_at)}`;
      return (
        head("mdi:weather-night", "Tonight, replayed", outlookPill) +
        svg +
        sub(
          `<span style="color:${C.typical}">Typical</span> keeps ${kwh(
            night.typical.spare_kwh,
          )} · <span style="color:${C.heavy}">heavy</span> ${heavyNote} · ${
            covered ?? "–"
          } % of nights last. Each line is a past night replayed from now.`,
        )
      );
    },
  );

  defineCard(
    "solar-energy-insights-enough-for-tonight",
    "Enough for tonight?",
    "The charge at sunset against what a typical and a heavy night need.",
    function (states, hass) {
      const el = this;
      const {
        W,
        C,
        st,
        val,
        attr,
        num,
        hm,
        kwh,
        sub,
        head,
        pill,
        ws,
        waiting,
      } = window.SolarDash.ui(this, hass, states);

      const needed = num(
        val("sensor.solar_energy_insights_charge_needed_for_the_night"),
      );
      const forWhen = attr(
        "sensor.solar_energy_insights_charge_needed_for_the_night",
        "for",
      );
      const typical = num(
        attr(
          "sensor.solar_energy_insights_charge_needed_for_the_night",
          "typical_night",
        ),
      );
      const fullLasts = attr(
        "sensor.solar_energy_insights_charge_needed_for_the_night",
        "full_battery_lasts",
      );
      const short = num(
        attr(
          "sensor.solar_energy_insights_charge_needed_for_the_night",
          "grid_even_when_full_kwh",
        ),
      );
      const atSunset = forWhen === "sunset";
      const level = atSunset
        ? num(val("sensor.solar_energy_insights_battery_at_sunset"))
        : num(attr("sensor.solar_energy_insights_summary", "soc"));
      if (needed === null || level === null)
        return (
          head("mdi:battery-heart-variant", "Enough for tonight?") +
          sub("Waiting for the plan.")
        );
      const mark = (
        p,
        color,
        w,
        label,
        up,
      ) => `<div style="position:absolute;left:calc(${Math.min(p, 100)}% - ${
        w / 2
      }px);top:-6px;width:${w}px;height:26px;background:${color}"></div>
  <div style="position:absolute;left:min(calc(${Math.min(
    p,
    100,
  )}% - 30px), calc(100% - 60px));${
    up ? "top:-22px" : "top:24px"
  };width:60px;text-align:center;font-size:10px;color:${color}">${label}</div>`;
      const heavyOk = fullLasts && level >= needed,
        typOk = typical !== null && level >= typical;
      const fill = heavyOk ? C.ok : typOk ? C.heavy : C.bad;
      const bar = `<div style="position:relative;height:14px;border-radius:7px;background:${
        C.track
      };margin:26px 0 26px">
  <div style="position:absolute;left:0;top:0;height:14px;width:${Math.min(
    level,
    100,
  )}%;border-radius:7px;background:${fill}"></div>
  ${
    typical !== null
      ? mark(
          typical,
          C.typical,
          2,
          "typical " + Math.round(typical) + "%",
          true,
        )
      : ""
  }
  ${mark(
    needed,
    C.heavy,
    3,
    fullLasts ? "heavy " + Math.round(needed) + "%" : "heavy: full+",
    false,
  )}
</div>`;
      const what = atSunset
        ? `Forecast at sunset ${Math.round(level)} %`
        : `Battery now ${Math.round(level)} %`;
      const verdict = heavyOk
        ? "enough even for a heavy night."
        : typOk
          ? "enough for a typical night, not a heavy one."
          : "not enough for a typical night.";
      const extra =
        !fullLasts && short
          ? ` Even a full battery would need ${kwh(
              short,
            )} from the grid on a heavy night.`
          : "";
      return (
        head(
          "mdi:battery-heart-variant",
          "Enough for tonight?",
          atSunset ? "at sunset" : "from now",
        ) +
        bar +
        sub(`${what}: ${verdict}${extra}`)
      );
    },
  );

  defineCard(
    "solar-energy-insights-plan-trust",
    "How sure is the plan",
    "How the plan has done, what it has learned, and Solcast against your roof.",
    function (states, hass) {
      const PARTS = {
        performance: () => {
          const el = this;
          const {
            W,
            C,
            st,
            val,
            attr,
            num,
            hm,
            kwh,
            sub,
            head,
            pill,
            ws,
            waiting,
          } = window.SolarDash.ui(this, hass, states);

          const bt = ws("backtest", "solar_energy_insights/backtest", 600);
          if (!bt)
            return (
              head("mdi:target-arrow", "How the plan has done") +
              waiting("backtest", "the scored evenings")
            );
          const nights = bt.nights || [];
          if (!nights.length)
            return (
              head("mdi:target-arrow", "How the plan has done") +
              sub("No scored evenings yet: they need the battery's history.")
            );
          const cells = nights
            .map((n) => {
              const color = !n.contested
                ? C.track
                : n.predicted_lasts === n.actual_lasts
                  ? C.ok
                  : C.bad;
              return `<div title="${n.day}" style="aspect-ratio:1;border-radius:3px;background:${color}"></div>`;
            })
            .join("");
          const contested = nights.filter((n) => n.contested);
          const maxErr = Math.max(
            ...contested.map((n) =>
              Math.abs(n.predicted_spare_kwh - n.actual_spare_kwh),
            ),
            0.5,
          );
          const strip = contested
            .map((n) => {
              const e = n.predicted_spare_kwh - n.actual_spare_kwh,
                h = (Math.abs(e) / maxErr) * 26;
              return `<div style="flex:1;display:flex;flex-direction:column;justify-content:center;height:56px">
    <div style="height:26px;display:flex;align-items:flex-end"><div style="width:100%;height:${
      e > 0 ? h : 0
    }px;background:${C.heavy};border-radius:2px 2px 0 0"></div></div>
    <div style="height:26px"><div style="width:100%;height:${
      e < 0 ? h : 0
    }px;background:${C.typical};border-radius:0 0 2px 2px"></div></div></div>`;
            })
            .join("");
          const err = bt.honest_error_kwh;
          const live = bt.live?.full_at;
          const right = bt.lasts_right;
          return (
            head(
              "mdi:target-arrow",
              "How the plan has done",
              nights.length + " evenings",
            ) +
            `<div style="display:grid;grid-template-columns:repeat(15,minmax(0,1fr));gap:3px">${cells}</div>` +
            sub(
              `<span style="color:${C.ok}">Right</span> · <span style="color:${C.bad}">wrong</span> · grey: ran out either way, so it says nothing.`,
            ) +
            (contested.length
              ? `<div style="display:flex;gap:2px;margin-top:12px;border-top:1px solid ${C.track};border-bottom:1px solid ${C.track}">${strip}</div>` +
                sub(
                  `Each bar is an evening that could have gone either way: <span style="color:${C.heavy}">up</span> = predicted more left than there was, <span style="color:${C.typical}">down</span> = less.`,
                )
              : "") +
            `<div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin-top:12px">
    ${[
      [
        "Error",
        err === null || err === undefined ? "–" : err.toFixed(2) + " kWh",
      ],
      [
        "Lasts the night right",
        right === null || right === undefined
          ? "–"
          : Math.round(right * 100) + " %",
      ],
      [
        "Bias",
        bt.bias_kwh === null || bt.bias_kwh === undefined
          ? "–"
          : (Math.abs(bt.bias_kwh) < 0.005
              ? "0.00"
              : (bt.bias_kwh > 0 ? "+" : "") + bt.bias_kwh.toFixed(2)) + " kWh",
      ],
      [
        "Full at, live",
        live && live.error_minutes !== null
          ? Math.round(live.error_minutes) + " min · " + live.days + " d"
          : "learning",
      ],
    ]
      .map(
        ([k, v]) =>
          `<div style="background:${C.track};border-radius:10px;padding:8px 10px"><div style="font-size:11px;color:${C.muted}">${k}</div><div style="font-size:16px;color:${C.text}">${v}</div></div>`,
      )
      .join("")}
  </div>`
          );
        },
        learned: () => {
          const el = this;
          const {
            W,
            C,
            st,
            val,
            attr,
            num,
            hm,
            kwh,
            sub,
            head,
            pill,
            ws,
            waiting,
          } = window.SolarDash.ui(this, hass, states);

          const E = "sensor.solar_energy_insights_night_prediction_error",
            K = "sensor.solar_energy_insights_solcast_correction_gain";
          const a = (k) => attr(E, k);
          const tile = (k, v) =>
            `<div style="background:${C.track};border-radius:10px;padding:8px 10px"><div style="font-size:11px;color:${C.muted}">${k}</div><div style="font-size:16px;color:${C.text}">${v}</div></div>`;
          const days = attr(K, "days") ?? 0,
            active = attr(K, "active");
          return (
            head("mdi:lightbulb-on-outline", "What it has learned") +
            `<div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px">
    ${tile(
      "Hidden load",
      a("hidden_load_w") === null || a("hidden_load_w") === undefined
        ? "–"
        : a("hidden_load_w") + " W",
    )}
    ${tile(
      "Heavy night",
      a("heavy_share_pct") === null || a("heavy_share_pct") === undefined
        ? "–"
        : a("heavy_share_pct") + "th percentile",
    )}
    ${tile("Battery readings fixed", a("corrected_readings") ?? "–")}
    ${tile("Solcast correction", active ? "in use" : days + " / 14 mornings")}
    ${tile(
      "Night error",
      a("honest_error_kwh") === null || a("honest_error_kwh") === undefined
        ? "–"
        : a("honest_error_kwh").toFixed(2) + " kWh",
    )}
  </div>` +
            sub(
              "Each is learned from your own evenings and kept only if it predicts evenings it was not fitted on better.",
            )
          );
        },
        solcast: () => {
          const el = this;
          const {
            W,
            C,
            st,
            val,
            attr,
            num,
            hm,
            kwh,
            sub,
            head,
            pill,
            ws,
            waiting,
          } = window.SolarDash.ui(this, hass, states);

          const K = "sensor.solar_energy_insights_solcast_correction_gain";
          const factors = attr(K, "factors_by_hour") || [];
          const days = attr(K, "days") ?? 0,
            active = attr(K, "active");
          const gain = num(val(K));
          if (!st(K))
            return (
              head("mdi:white-balance-sunny", "Solcast vs your roof") +
              sub(
                "Add a solar energy counter in the integration's options to start learning.",
              )
            );
          if (!active && days < 14) {
            return (
              head(
                "mdi:white-balance-sunny",
                "Solcast vs your roof",
                days + " / 14 mornings",
              ) +
              `<div style="height:10px;border-radius:5px;background:${
                C.track
              };margin:8px 0 4px"><div style="height:10px;border-radius:5px;width:${Math.round(
                (days / 14) * 100,
              )}%;background:${C.typical}"></div></div>` +
              sub(
                "Learning: each morning's forecast is kept and compared with what the panels made. The correction is shown, and used if it helps, after 14 mornings.",
              )
            );
          }
          const hours = [...Array(24).keys()].filter((h) => h >= 5 && h <= 21);
          const Wd = 340,
            H = 90,
            mid = 45,
            scale = 80;
          const bw = (Wd - 8) / hours.length;
          const bars = hours
            .map((h, k) => {
              const f = factors[h] ?? 1,
                d = (f - 1) * scale,
                top = d > 0 ? mid - d : mid;
              return `<rect x="${(4 + k * bw + 2).toFixed(1)}" y="${top.toFixed(
                1,
              )}" width="${(bw - 4).toFixed(1)}" height="${Math.max(
                Math.abs(d),
                1,
              ).toFixed(1)}" rx="2" fill="${
                d >= 0 ? C.ok : C.heavy
              }" opacity="${active ? 1 : 0.35}"/>`;
            })
            .join("");
          const svg = `<svg viewBox="0 0 ${Wd} ${H}" style="width:100%;display:block">
  <line x1="4" y1="${mid}" x2="${Wd - 4}" y2="${mid}" stroke="${
    C.faint
  }" stroke-dasharray="3 3"/>${bars}
  <text x="4" y="${H - 2}" fill="${
    C.muted
  }" font-size="10">5 h</text><text x="${Wd - 4}" y="${H - 2}" fill="${
    C.muted
  }" font-size="10" text-anchor="end">21 h</text></svg>`;
          const note = active
            ? `In use: it cuts Solcast's error by ${
                gain !== null ? Math.round(gain) : "–"
              } % on days it was not learned from.`
            : days < 14
              ? `Learning: ${days} of 14 mornings with a forecast and what the panels made.`
              : `Not in use: on your days it does not beat Solcast as it is${
                  gain !== null ? ` (${Math.round(gain)} %)` : ""
                }.`;
          return (
            head("mdi:white-balance-sunny", "Solcast vs your roof", "by hour") +
            svg +
            sub(
              `<span style="color:${C.ok}">Up</span>: your roof makes more than Solcast says · <span style="color:${C.heavy}">down</span>: less. ${note}`,
            )
          );
        },
      };
      const SECTIONS = (this._config || {}).sections || [
        "performance",
        "learned",
        "solcast",
      ];
      return SECTIONS.filter((k) => PARTS[k])
        .map(
          (k, i) =>
            `<div style="${
              i
                ? "margin-top:16px;padding-top:14px;border-top:1px solid var(--sei-track, var(--divider-color, #2c2c2e))"
                : ""
            }">${PARTS[k]()}</div>`,
        )
        .join("");
    },
    FORMS.planTrust,
  );

  defineCard(
    "solar-energy-insights-day-insights",
    "Day insights",
    "A day in hours off the grid, its best window, shortfall against the forecast and more.",
    function (states, hass) {
      // The day: yesterday, or the date a helper holds (to follow a history page).
      const SD = window.SolarDash,
        el = this;
      const pad = (n) => String(n).padStart(2, "0");
      const iso = (d) =>
        `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
      const yest = new Date();
      yest.setDate(yest.getDate() - 1);
      const latest = iso(yest);
      const sel = (el._config || {}).day_entity
        ? states[el._config.day_entity]?.state
        : null;
      const date =
        sel && /^\d{4}-\d\d-\d\d$/.test(sel) && sel <= latest ? sel : latest;
      const t1 = new Date(date + "T12:00");
      const daysBack = Math.round(
        (new Date(iso(new Date()) + "T12:00") - t1) / 864e5,
      );
      const label =
        daysBack === 1
          ? "Yesterday"
          : t1.toLocaleDateString("en-GB", {
              weekday: "short",
              day: "numeric",
              month: "short",
            });
      const W = (window.__sei = window.__sei || {});
      const C2 = (W.dayInsights = W.dayInsights || {});
      if (!el.__seiListen) {
        el.__seiListen = true;
        (W.listeners = W.listeners || []).push(
          () => el.requestUpdate && el.requestUpdate(),
        );
      }
      const wake = () => (W.listeners || []).forEach((f) => f());
      const once = (key, start) => {
        if (!C2[key] && hass?.callWS && SD) {
          C2[key] = { pending: true };
          start()
            .then((v) => {
              C2[key] = { v };
            })
            .catch(() => {
              delete C2[key];
            })
            .finally(wake);
        }
        return C2[key]?.v;
      };
      const hours = once("h" + date, () => SD.loadHourly(hass, date));
      const fcAll = once("fc", () => SD.loadFc(hass).then(() => true));
      const fcH =
        fcAll && SD.fcHourly && (window.__solarDash || {}).fc?.hours
          ? SD.fcHourly(window.__solarDash.fc.hours, date)
          : null;
      const week = once("w" + date, () => {
        const ws = new Date(date + "T00:00");
        ws.setDate(ws.getDate() - 7);
        const HID = SD.HID;
        return hass
          .callWS({
            type: "recorder/statistics_during_period",
            start_time: ws.toISOString(),
            end_time: new Date(date + "T00:00").toISOString(),
            statistic_ids: [...HID.solar, ...HID.house],
            period: "day",
            types: ["change"],
          })
          .then((r) => {
            const pick = (ids) => {
              for (const id of ids)
                if (r?.[id]?.length)
                  return r[id]
                    .map((x) => x.change)
                    .filter((v) => v !== null && v !== undefined);
              return [];
            };
            return { y: pick(HID.solar), h: pick(HID.house) };
          });
      });
      const sum = (a) => (a || []).reduce((s, v) => s + (v || 0), 0);
      const D = {
        label,
        hours: hours === undefined ? null : hours,
        fc: fcH ? { hourly: fcH, total: sum(fcH) } : null,
        week: week || null,
        rec: hours ? { y: sum(hours.solar), h: sum(hours.house) } : null,
      };
      const head = `<div style="display:flex;justify-content:space-between;align-items:baseline">
  <div style="font-size:17px;color:#fff;font-weight:500">Day insights</div>
  <div style="font-size:12px;color:#8e8e93">${D.label}</div></div>`;
      const H = D.hours;
      if (!H || !H.house)
        return (
          head +
          `<div style="font-size:12px;color:#8e8e93;margin-top:8px">${
            H === null ? "Loading…" : "No hourly data for this day."
          }</div>`
        );
      const v = (a, i) =>
        a && a[i] !== null && a[i] !== undefined ? a[i] : null;
      const z = (a, i) => v(a, i) ?? 0;
      const hh = (i) => String((i + 24) % 24).padStart(2, "0");

      // 1 · Grid-free hours strip
      let free = 0,
        known = 0;
      const cells = Array.from({ length: 24 }, (_, i) => {
        const house = v(H.house, i),
          imp = z(H.imp, i);
        if (house === null)
          return `<div style="height:14px;border-radius:3px;background:#2c2c2e"></div>`;
        known++;
        const share = house > 0.01 ? imp / house : 0;
        const col =
          imp < 0.02 ? "#8be36a" : share < 0.5 ? "#ffb74d" : "#ff8a80";
        if (imp < 0.02) free++;
        return `<div title="${hh(i)}h · ${house.toFixed(
          2,
        )} kWh used · ${imp.toFixed(
          2,
        )} from grid" style="height:14px;border-radius:3px;background:${col}"></div>`;
      }).join("");
      const ticks = Array.from(
        { length: 24 },
        (_, i) =>
          `<div style="font-size:9px;color:#636366;text-align:center">${
            i % 6 === 0 ? hh(i) : ""
          }</div>`,
      ).join("");

      // 2 · Insight rows
      const rows = [];
      const row = (icon, color, text, val) =>
        rows.push(`
  <div style="display:grid;grid-template-columns:22px 1fr auto;gap:10px;align-items:center;padding:9px 0;border-top:1px solid #2c2c2e">
    <ha-icon icon="${icon}" style="--mdc-icon-size:19px;color:${color}"></ha-icon>
    <div style="font-size:13px;color:#e5e5e7;line-height:1.35;white-space:normal">${text}</div>
    <div style="font-size:13px;color:#fff;text-align:right;white-space:nowrap">${val}</div></div>`);

      // Best 2-hour surplus window
      if (H.solar) {
        let best = -1e9,
          bi = -1;
        for (let i = 0; i < 23; i++) {
          const s =
            z(H.solar, i) -
            z(H.house, i) +
            z(H.solar, i + 1) -
            z(H.house, i + 1);
          if (s > best) {
            best = s;
            bi = i;
          }
        }
        if (best > 0.5)
          row(
            "mdi:washing-machine",
            "#f5b83d",
            `Best window for heavy loads<br><span style="color:#8e8e93;font-size:11px">${hh(
              bi,
            )}–${hh(bi + 2)} h had the most spare solar</span>`,
            `+${best.toFixed(1)} kWh`,
          );
      }
      // Forecast hits & misses
      const F = D.fc?.hourly;
      if (F && H.solar) {
        let lo = 1e9,
          li = -1,
          hi = -1e9,
          hiI = -1;
        for (let i = 0; i < 24; i++) {
          if (F[i] === null && v(H.solar, i) === null) continue;
          const d = z(H.solar, i) - (F[i] || 0);
          if (d < lo) {
            lo = d;
            li = i;
          }
          if (d > hi) {
            hi = d;
            hiI = i;
          }
        }
        if (lo < -0.3)
          row(
            "mdi:weather-cloudy",
            "#ff8a80",
            `Biggest shortfall vs forecast<br><span style="color:#8e8e93;font-size:11px">${hh(
              li,
            )}–${hh(li + 1)} h · clouds or shade</span>`,
            `${lo.toFixed(1)} kWh`,
          );
        if (hi > 0.3)
          row(
            "mdi:weather-sunny",
            "#8be36a",
            `Biggest beat vs forecast<br><span style="color:#8e8e93;font-size:11px">${hh(
              hiI,
            )}–${hh(hiI + 1)} h</span>`,
            `+${hi.toFixed(1)} kWh`,
          );
      }
      // Busiest consumption hour
      {
        let m = -1,
          mi = -1;
        for (let i = 0; i < 24; i++)
          if (z(H.house, i) > m) {
            m = z(H.house, i);
            mi = i;
          }
        if (mi >= 0)
          row(
            "mdi:home-lightning-bolt-outline",
            "#4c7cf3",
            `Busiest hour at home<br><span style="color:#8e8e93;font-size:11px">${hh(
              mi,
            )}–${hh(mi + 1)} h</span>`,
            `${m.toFixed(2)} kWh`,
          );
      }
      // Night use 22–06 and how much came from the grid
      {
        let n = 0,
          g = 0;
        [22, 23, 0, 1, 2, 3, 4, 5].forEach((i) => {
          n += z(H.house, i);
          g += z(H.imp, i);
        });
        if (n > 0.05)
          row(
            "mdi:weather-night",
            "#9ad7e6",
            `Night use 22–06 h<br><span style="color:#8e8e93;font-size:11px">${
              g < 0.05
                ? "all from the battery"
                : `${g.toFixed(1)} kWh from the grid`
            }</span>`,
            `${n.toFixed(1)} kWh`,
          );
      }
      // Versus the 7 days before
      if (D.week && D.rec) {
        const avg = (a) =>
          a.length ? a.reduce((s, x) => s + x, 0) / a.length : NaN;
        const ya = avg(D.week.y),
          ha = avg(D.week.h);
        const tag = (x, a, goodUp) => {
          if (!(a > 0)) return "–";
          const p = ((x - a) / a) * 100;
          const good = goodUp ? p >= 0 : p <= 0;
          return `<span style="color:${good ? "#8be36a" : "#ff8a80"}">${
            p >= 0 ? "+" : ""
          }${Math.round(p)}%</span>`;
        };
        if (ya > 0 || ha > 0)
          row(
            "mdi:calendar-week",
            "#c7c7cc",
            `Compared with the 7 days before<br><span style="color:#8e8e93;font-size:11px">solar ${ya.toFixed(
              1,
            )} · home ${ha.toFixed(1)} kWh/day on average</span>`,
            `☀ ${tag(D.rec.y, ya, true)} · ⌂ ${tag(D.rec.h, ha, false)}`,
          );
      }

      return (
        head +
        `
  <div style="margin-top:4px"></div>
  ${rows.join("")}`
      );
    },
    FORMS.dayInsights,
  );
})();
