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
              // Energy hours without counter jumps (see plausible below)
              (type === "mean" ? rows : plausible(rows, 1)).forEach(
                function (row) {
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
                },
              );
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

  /* A counter that comes back after reporting 0 (or appears for the first time)
     makes the recorder count its whole total as one period. As in
     core/nights.py plausible(): changes above both 25 kWh an hour and ten times
     the counter's typical change are dropped. rows: [{ start, change }]; hours:
     the length of one row in hours. */
  const JUMP_FLOOR_KWH = 25,
    JUMP_FACTOR = 10;
  const plausible = function (rows, hours) {
    const ok = (rows || []).filter(function (r) {
      return r.change !== null && r.change !== undefined && r.change >= 0;
    });
    const pos = ok
      .map(function (r) {
        return r.change;
      })
      .filter(function (v) {
        return v > 0;
      })
      .sort(function (a, b) {
        return a - b;
      });
    const typical = pos.length ? pos[Math.floor(pos.length / 2)] : 0;
    const limit = Math.max(
      JUMP_FLOOR_KWH * (hours || 1),
      JUMP_FACTOR * typical,
    );
    return ok.filter(function (r) {
      return r.change <= limit;
    });
  };

  /* ── Day totals for a whole month: { 'YYYY-MM-DD': { y, h, i, e } } ─────────────
     y solar · h house · i bought · e exported (kWh), from the first day with statistics.
     Summed from hourly statistics so that counter jumps can be dropped; the daily
     ones keep them. Past months are cached for the session, the current month for 30 min. */
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
          period: "hour",
          types: ["change"],
        });
        const K = { y: HID.solar, h: HID.house, i: HID.imp, e: HID.exp };
        Object.keys(K).forEach(function (k) {
          const id = K[k].find(function (x) {
            return r && r[x] && r[x].length;
          });
          if (!id) return;
          // Days with statistics, even when all their hours were dropped
          r[id].forEach(function (row) {
            const d = isoDay(new Date(row.start));
            const o = (out[d] = out[d] || {});
            if (o[k] === undefined) o[k] = 0;
          });
          plausible(r[id], 1).forEach(function (row) {
            out[isoDay(new Date(row.start))][k] += row.change;
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
    plausible,
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
})();

/* ── Solar history by week, month and year (window.SolarPeriod) ─────────────────────
   For a history page that switches between Day, Week, Month and Year. The page keeps
   its state in two helpers the user creates: input_select.solar_history_period
   (Day, Week, Month, Year) and input_datetime.solar_day (any date in the period).
   Day totals come from the recorder's daily statistics, a month at a time, so a week,
   a month and a year share one cache; today's row comes from the five-minute
   statistics, since the daily ones lag by up to an hour. Nothing is fetched before the integration's
   settings arrive: without them SolarDash.HID is empty and empty months would stick. */
(function () {
  "use strict";
  // A copy kept in /config/www from before this shipped is older: the shipped one wins.
  const VER = 2;
  if (window.SolarPeriod && window.SolarPeriod.v >= VER) return;

  const PERIOD_ENTITY = "input_select.solar_history_period";
  const DAY_ENTITY = "input_datetime.solar_day";
  const PERIODS = ["Day", "Week", "Month", "Year"];
  const TTL = 5 * 60e3; // refresh data of the running month every 5 min
  const KEYS = ["y", "h", "i", "e", "p1", "p2", "p3", "bc", "bd"];
  // A typical year, kWh per month (PVGIS, say), is the home's own: a dashboard
  // passes it with setTypical(). Without it a year compares with the forecasts.
  let TYPICAL = null;
  const setTypical = (a) => {
    TYPICAL = Array.isArray(a) && a.length === 12 ? a.map(Number) : null;
  };

  // ── dates ────────────────────────────────────────────────────────────────
  const pad = (n) => String(n).padStart(2, "0");
  const iso = (d) =>
    d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
  const at12 = (s) => new Date(s + "T12:00");
  const addD = (s, k) => {
    const d = at12(s);
    d.setDate(d.getDate() + k);
    return iso(d);
  };
  const diffD = (a, b) => Math.round((at12(b) - at12(a)) / 864e5);
  const todayIso = () => iso(new Date());
  const mKey = (s) => s.slice(0, 7);
  const minS = (a, b) => (a < b ? a : b);

  function isoWeek(s) {
    const d = at12(s);
    d.setDate(d.getDate() + 3 - ((d.getDay() + 6) % 7));
    const w1 = new Date(d.getFullYear(), 0, 4, 12);
    return 1 + Math.round(((d - w1) / 864e5 - 3 + ((w1.getDay() + 6) % 7)) / 7);
  }

  function periodOf(states) {
    const s = states && states[PERIOD_ENTITY] && states[PERIOD_ENTITY].state;
    return PERIODS.includes(s) ? s : "Day";
  }
  function dateOf(states) {
    const t = todayIso(),
      s = (states && states[DAY_ENTITY] && states[DAY_ENTITY].state) || "";
    return /^\d{4}-\d\d-\d\d$/.test(s) && s <= t ? s : t;
  }

  function range(P, date) {
    const d = at12(date);
    let s, e;
    if (P === "Week") {
      s = new Date(d);
      s.setDate(d.getDate() - ((d.getDay() + 6) % 7));
      e = new Date(s);
      e.setDate(s.getDate() + 7);
    } else if (P === "Month") {
      s = new Date(d.getFullYear(), d.getMonth(), 1, 12);
      e = new Date(d.getFullYear(), d.getMonth() + 1, 1, 12);
    } else if (P === "Year") {
      s = new Date(d.getFullYear(), 0, 1, 12);
      e = new Date(d.getFullYear() + 1, 0, 1, 12);
    } else {
      s = new Date(d);
      e = new Date(d);
      e.setDate(e.getDate() + 1);
    }
    const start = iso(s),
      end = iso(e);
    return { P, start, end, days: diffD(start, end) };
  }

  /** Move the anchor date k periods; never past today. */
  function step(P, date, k) {
    const d = at12(date);
    if (P === "Week") d.setDate(d.getDate() + 7 * k);
    else if (P === "Month" || P === "Year") {
      const day = d.getDate();
      d.setDate(1);
      if (P === "Month") d.setMonth(d.getMonth() + k);
      else d.setFullYear(d.getFullYear() + k);
      d.setDate(
        Math.min(day, new Date(d.getFullYear(), d.getMonth() + 1, 0).getDate()),
      );
    } else d.setDate(d.getDate() + k);
    return minS(iso(d), todayIso());
  }

  /** Current selection: period, anchor date, range, elapsed part. */
  function sel(states) {
    const P = periodOf(states),
      date = dateOf(states),
      today = todayIso();
    const R = range(P, date);
    const inProg = R.end > today;
    const upto = inProg ? addD(today, 1) : R.end; // exclusive end of the elapsed part
    return {
      ...R,
      date,
      today,
      inProg,
      upto,
      elapsed: diffD(R.start, upto),
      canNext: R.end <= today,
    };
  }

  function labels(S) {
    const t = S.today,
      f = (s, o) => at12(s).toLocaleDateString("en-GB", o);
    const nowR = range(S.P, t),
      isNow = nowR.start === S.start;
    const lastR = range(S.P, addD(nowR.start, -1)),
      isLast = lastR.start === S.start;
    const last = addD(S.end, -1);
    if (S.P === "Week") {
      const sameM = mKey(S.start) === mKey(last);
      const span = sameM
        ? f(S.start, { day: "numeric" }) +
          "–" +
          f(last, { day: "numeric", month: "short", year: "numeric" })
        : f(S.start, { day: "numeric", month: "short" }) +
          " – " +
          f(last, { day: "numeric", month: "short", year: "numeric" });
      return {
        label: isNow
          ? "This week"
          : isLast
            ? "Last week"
            : "Week " + isoWeek(S.start),
        full: "Week " + isoWeek(S.start) + " · " + span,
        isNow,
        word: "week",
        back: "tap for this week",
      };
    }
    if (S.P === "Month") {
      const full = f(S.start, { month: "long", year: "numeric" });
      return {
        label: isNow
          ? "This month"
          : isLast
            ? "Last month"
            : f(S.start, { month: "long" }),
        full,
        isNow,
        word: "month",
        back: "tap for this month",
      };
    }
    if (S.P === "Year") {
      const y = S.start.slice(0, 4);
      return {
        label: isNow ? "This year" : isLast ? "Last year" : y,
        full: y,
        isNow,
        word: "year",
        back: "tap for this year",
      };
    }
    const back = diffD(S.start, t);
    return {
      label:
        back === 0
          ? "Today"
          : back === 1
            ? "Yesterday"
            : f(S.start, { weekday: "short", day: "numeric", month: "short" }),
      full: f(S.start, {
        weekday: "short",
        day: "numeric",
        month: "short",
        year: "numeric",
      }),
      isNow: back === 0,
      word: "day",
      back: "tap for today",
    };
  }

  // ── cache / wake-up ──────────────────────────────────────────────────────
  const C = (window.__solarPeriod = window.__solarPeriod || {
    m: {},
    yrs: null,
    waiters: new Set(),
    hass: null,
    fcT: 0,
  });
  function wake() {
    const w = [...C.waiters];
    C.waiters.clear();
    w.forEach((e) => {
      try {
        e.requestUpdate("_config");
      } catch (x) {
        /* gone */
      }
    });
  }
  // The timer is cleared once the request settles, so none is left running.
  const timeout = (p, ms) => {
    let timer;
    return Promise.race([
      p,
      new Promise((_, rej) => {
        timer = setTimeout(() => rej(new Error("timeout")), ms);
      }),
    ]).finally(() => clearTimeout(timer));
  };
  const startMs = (x) =>
    typeof x.start === "number" ? x.start : Date.parse(x.start);

  // The integration's settings (entities, prefix, strings, history start) arrive over a
  // WebSocket call; until then SolarDash.HID is empty, so nothing may be fetched or cached.
  function cfg() {
    const SD = window.SolarDash;
    const c = SD && SD.config ? SD.config() : null;
    if (c) return c;
    const hass =
      C.hass || (document.querySelector("home-assistant") || {}).hass;
    if (SD && SD.loadConfig && hass && !C.cfgAsk) {
      C.cfgAsk = true;
      Promise.resolve(SD.loadConfig(hass)).then(
        () => {
          C.cfgAsk = false;
          wake();
        },
        () => {
          C.cfgAsk = false;
        },
      );
    }
    return null;
  }
  function ids() {
    const SD = window.SolarDash,
      c = cfg();
    if (!SD || !c) return null;
    const PFX = SD.PFX,
      HID = SD.HID,
      E = c.entities || {};
    if (!HID.solar.length || !HID.house.length) return null;
    const str = (n) => {
      const s = (c.strings || []).find((x) => x.n === n);
      return [
        (s && s.energy) ||
          "sensor.solar_energy_insights_pv" + n + "_energy_today",
      ];
    };
    return {
      y: HID.solar,
      h: HID.house,
      i: HID.imp,
      e: HID.exp,
      p1: str(1),
      p2: str(2),
      p3: str(3),
      bc: PFX
        ? [PFX + "battery_charged_today", PFX + "battery_charged_total"]
        : [],
      bd: PFX
        ? [PFX + "battery_discharged_today", PFX + "battery_discharged_total"]
        : [],
      pk: E.solar_power ? [E.solar_power] : PFX ? [PFX + "solar_power"] : [],
    };
  }
  const ready = () => !!ids();
  const historyStart = () => {
    const t = window.SolarDash && window.SolarDash.START;
    // Unknown (no house counter set): two full years back keeps the year view bounded
    return t ? iso(new Date(t)) : new Date().getFullYear() - 2 + "-01-01";
  };

  function monthsIn(start, end) {
    // month keys touching [start, end)
    const out = [],
      d = at12(start.slice(0, 7) + "-01");
    while (iso(d) < end) {
      out.push(iso(d).slice(0, 7));
      d.setMonth(d.getMonth() + 1);
    }
    return out;
  }
  function monthBounds(mk) {
    const [y, m] = mk.split("-").map(Number);
    return [new Date(y, m - 1, 1), new Date(y, m, 1)];
  }

  /** Hourly statistics into day rows { 'YYYY-MM-DD': {k: kWh, pk: kW} } and hour
      arrays { 'YYYY-MM-DD': {k: [24]} }, first id with data per key, counter jumps
      dropped (SolarDash.plausible). */
  const HOURLY = ["y", "h", "i", "p1", "p2", "p3"];
  function parseHours(r, I) {
    const rows = {},
      hours = {};
    const clean = window.SolarDash && window.SolarDash.plausible;
    for (const k of Object.keys(I)) {
      const id = I[k].find((x) => r && r[x] && r[x].length);
      if (!id) continue;
      if (k === "pk") {
        r[id].forEach((x) => {
          if (x.max === null || x.max === undefined) return;
          const o = (rows[iso(new Date(startMs(x)))] =
            rows[iso(new Date(startMs(x)))] || {});
          o.pk = Math.max(o.pk || 0, x.max / 1000);
        });
        continue;
      }
      r[id].forEach((x) => {
        const d = iso(new Date(startMs(x))),
          o = (rows[d] = rows[d] || {});
        if (o[k] === undefined) o[k] = 0;
      });
      const kept = clean ? clean(r[id], 1) : r[id].filter((x) => x.change >= 0);
      kept.forEach((x) => {
        const t = new Date(startMs(x)),
          d = iso(t);
        rows[d][k] += x.change;
        if (!HOURLY.includes(k)) return;
        const hd = (hours[d] = hours[d] || {}),
          a = (hd[k] = hd[k] || new Array(24).fill(null));
        a[t.getHours()] = (a[t.getHours()] || 0) + x.change; // the 25-hour DST day adds up
      });
    }
    return { rows, hours };
  }

  /** Month-chunk loader: hourly statistics for one month, kept as day rows and
      hour arrays. The current month is refreshed every TTL. */
  function ensure(hass, mk) {
    const box = C.m,
      c = box[mk],
      now = Date.now(),
      cur = mk === mKey(todayIso());
    if (c && c.p) return c.p;
    if (c && c.rows && (!cur || now - c.t < TTL)) return Promise.resolve(c);
    if (c && c.err && now - c.t < 15e3)
      return Promise.resolve(c.rows ? c : null);
    const I = ids();
    if (!I || !hass || !hass.callWS) return Promise.resolve(null);
    const [s, e] = monthBounds(mk);
    if (s > new Date() || mk < historyStart().slice(0, 7)) {
      box[mk] = { rows: {}, hours: {}, t: now };
      return Promise.resolve(box[mk]);
    }
    const p = timeout(
      hass.callWS({
        type: "recorder/statistics_during_period",
        start_time: s.toISOString(),
        end_time: e.toISOString(),
        statistic_ids: [...new Set(Object.values(I).flat())],
        period: "hour",
        types: ["change", "max"],
      }),
      30000,
    )
      .then((r) => {
        box[mk] = { ...parseHours(r, I), t: Date.now() };
        wake();
        return box[mk];
      })
      .catch(() => {
        box[mk] = {
          rows: c && c.rows,
          hours: c && c.hours,
          t: Date.now(),
          err: true,
        };
        wake();
        return c && c.rows ? box[mk] : null;
      });
    box[mk] = { rows: c && c.rows, hours: c && c.hours, t: c ? c.t : 0, p };
    return p;
  }

  // Today, from the recorder's five-minute statistics: the daily ones lag by up to
  // an hour, and a counter's state can't be read as today's energy, since the
  // configured counters are usually lifetime totals.
  const TODAY_TTL = 2 * 60e3;
  function ensureToday(hass) {
    const t = todayIso(),
      c = C.today;
    if (c && c.d === t && (c.p || Date.now() - c.t < TODAY_TTL))
      return c.p || Promise.resolve(c.row);
    const I = ids();
    if (!I || !hass || !hass.callWS) return Promise.resolve(null);
    const p = timeout(
      hass.callWS({
        type: "recorder/statistics_during_period",
        start_time: new Date(t + "T00:00").toISOString(),
        end_time: new Date().toISOString(),
        statistic_ids: [...new Set(Object.values(I).flat())],
        period: "5minute",
        types: ["change", "max"],
      }),
      30000,
    )
      .then((r) => {
        const row = {},
          clean = window.SolarDash && window.SolarDash.plausible;
        Object.keys(I).forEach((k) => {
          const id = I[k].find((x) => r && r[x] && r[x].length);
          if (!id) return;
          if (k === "pk") {
            r[id].forEach((x) => {
              if (x.max !== null && x.max !== undefined)
                row.pk = Math.max(row.pk || 0, x.max / 1000);
            });
            return;
          }
          row[k] = 0;
          (clean
            ? clean(r[id], 1 / 12)
            : r[id].filter((x) => x.change >= 0)
          ).forEach((x) => {
            row[k] += x.change;
          });
        });
        C.today = { d: t, row, t: Date.now() };
        wake();
        return row;
      })
      .catch(() => {
        C.today = { d: t, row: c && c.d === t ? c.row : null, t: Date.now() };
        return C.today.row;
      });
    C.today = { d: t, row: c && c.d === t ? c.row : null, t: 0, p };
    return p;
  }
  function powerNow(states) {
    const c =
      (window.SolarDash &&
        window.SolarDash.config &&
        window.SolarDash.config()) ||
      {};
    const id = (c.entities || {}).solar_power;
    const v = parseFloat(id && states && states[id] && states[id].state);
    return isNaN(v) ? null : v;
  }
  function patchToday(r, states) {
    // Until the five-minute statistics are in, the daily row (an hour behind) stands
    const T = C.today && C.today.d === todayIso() ? C.today.row : null,
      o = { ...(r || {}), ...(T || {}) };
    const w = powerNow(states);
    if (w !== null) o.pk = Math.max(o.pk || 0, w / 1000);
    o.live = !!T;
    return o;
  }

  // kind "rows": day rows (today from the five-minute statistics); "hours": hour arrays
  function assemble(kind, start, end, states) {
    const out = {},
      t = todayIso();
    monthsIn(start, end).forEach((mk) => {
      const got = C.m[mk] && C.m[mk][kind];
      Object.entries(got || {}).forEach(([d, r]) => {
        if (d >= start && d < end) out[d] = r;
      });
    });
    if (kind === "rows" && t >= start && t < end)
      out[t] = patchToday(out[t], states);
    return out;
  }

  /** Day rows for [start, end) – sync (null while loading; el is re-rendered when ready). */
  function daysSync(hass, start, end, el, kind) {
    kind = kind || "rows";
    C.hass = hass || C.hass;
    const t = mKey(todayIso());
    let missing = false;
    monthsIn(start, end).forEach((mk) => {
      if (mk > t) return;
      const c = C.m[mk];
      if (!c || !c.rows || c.p || (mk === t && Date.now() - c.t > TTL))
        ensure(hass, mk);
      if (!C.m[mk] || !C.m[mk].rows) missing = true;
    });
    const t0 = todayIso();
    if (kind === "rows" && t0 >= start && t0 < end) {
      const c = C.today;
      if (!c || c.d !== t0 || (!c.p && Date.now() - c.t > TODAY_TTL))
        ensureToday(hass);
      if (el && !(C.today && C.today.row)) C.waiters.add(el);
    }
    if (missing) {
      if (el) C.waiters.add(el);
      return null;
    }
    return assemble(kind, start, end, hass && hass.states);
  }
  async function daysAsync(hass, start, end, kind) {
    kind = kind || "rows";
    const t = mKey(todayIso());
    await Promise.all(
      monthsIn(start, end)
        .filter((mk) => mk <= t)
        .map((mk) => ensure(hass, mk)),
    );
    const t0 = todayIso();
    if (kind === "rows" && t0 >= start && t0 < end) await ensureToday(hass);
    return assemble(kind, start, end, hass && hass.states);
  }
  const hoursSync = (hass, start, end, el) =>
    daysSync(hass, start, end, el, "hours");
  const hoursAsync = (hass, start, end) => daysAsync(hass, start, end, "hours");

  /** Totals per year since the first statistics, from the same day rows, so a
      year adds up to its months and days: { 'YYYY': { y, h, i, e, m: [12] } }. */
  function yearsSync(hass, el) {
    const rows = daysSync(hass, historyStart(), addD(todayIso(), 1), el);
    if (!rows) return null;
    const out = {};
    Object.entries(rows).forEach(([d, r]) => {
      const o = (out[d.slice(0, 4)] = out[d.slice(0, 4)] || {
        y: 0,
        h: 0,
        i: 0,
        e: 0,
        m: new Array(12).fill(null),
      });
      ["y", "h", "i", "e"].forEach((k) => {
        o[k] += r[k] || 0;
      });
      const mi = +d.slice(5, 7) - 1;
      o.m[mi] = (o.m[mi] || 0) + (r.y || 0);
    });
    return out;
  }

  // ── aggregation ──────────────────────────────────────────────────────────
  function sum(rows) {
    const t = { n: 0, pk: 0, pkD: null };
    KEYS.forEach((k) => {
      t[k] = 0;
    });
    Object.entries(rows || {}).forEach(([d, r]) => {
      if (!r || !KEYS.some((k) => r[k] !== undefined && r[k] !== null)) return;
      t.n++;
      KEYS.forEach((k) => {
        t[k] += r[k] || 0;
      });
      if ((r.pk || 0) > t.pk) {
        t.pk = r.pk;
        t.pkD = d;
      }
    });
    return t;
  }
  function byMonth(rows) {
    const g = {};
    Object.entries(rows || {}).forEach(([d, r]) => {
      (g[mKey(d)] = g[mKey(d)] || {})[d] = r;
    });
    const out = {};
    Object.keys(g).forEach((mk) => {
      out[mk] = sum(g[mk]);
    });
    return out;
  }
  const autarky = (t) =>
    t && t.h > 0
      ? Math.max(0, Math.min(100, (1 - (t.i || 0) / t.h) * 100))
      : null;
  const selfUse = (t) =>
    t && t.y > 0
      ? Math.max(0, Math.min(100, (1 - (t.e || 0) / t.y) * 100))
      : null;

  // ── forecast per day (kWh) ───────────────────────────────────────────────
  function loadFcOnce(hass) {
    const SD = window.SolarDash,
      G = window.__solarDash;
    if (!SD || !SD.loadFc || !hass || !hass.callWS) return;
    if (G && G.fc && G.fc.state === "pending") return;
    if (G && G.fc && G.fc.t && Date.now() - G.fc.t < 30 * 60e3) return;
    if (Date.now() - C.fcT < 60e3) return;
    C.fcT = Date.now();
    try {
      Promise.resolve(SD.loadFc(hass)).then(wake, () => {});
    } catch (x) {
      /* ignore */
    }
  }
  function fcDay(hass, d) {
    const st = (hass && hass.states) || {},
      t = todayIso();
    const A =
      (st["sensor.solar_energy_insights_morning_forecast"] || {}).attributes ||
      {};
    const sumA = (a) =>
      Array.isArray(a) ? a.reduce((s, v) => s + (+v || 0), 0) : null;
    const num = (id) => {
      const v = parseFloat(st[id] && st[id].state);
      return isNaN(v) ? null : v;
    };
    if (d > t) {
      const k = diffD(t, d);
      if (k > 6) return null;
      const v = num(
        "sensor.solcast_pv_forecast_forecast_" +
          (k === 1 ? "tomorrow" : "day_" + (k + 1)),
      );
      return v === null ? null : { v, src: "solcast" };
    }
    if (d === t) {
      if (A.today && A.today.d === d) {
        if (A.today.mo != null) return { v: A.today.mo, src: "06" };
        const s = sumA(A.today.moh);
        if (s) return { v: s, src: "06" };
      }
      const v = num("sensor.solcast_pv_forecast_forecast_today");
      return v === null ? null : { v, src: "solcast" };
    }
    const e = (A.history || []).find((x) => x.d === d);
    if (e && e.mo != null) return { v: e.mo, src: "06" };
    if (e && e.mh) {
      const s = sumA(e.mh);
      if (s) return { v: s, src: "06" };
    }
    const G = window.__solarDash,
      SD = window.SolarDash;
    if (G && G.fc && G.fc.hours && SD && SD.fcHourly) {
      const a = SD.fcHourly(G.fc.hours, d),
        s = sumA(a);
      if (s > 0) return { v: s, src: "energy" };
    }
    return null;
  }

  // ── the object the period tiles render from ──────────────────────────────
  function view(hass, states, el) {
    C.hass = hass || C.hass;
    const S = sel(states || (hass && hass.states)),
      L = labels(S);
    const base = { ...S, ...L, loading: false };
    if (S.P === "Day") return base;
    loadFcOnce(hass);
    const rows = daysSync(hass, S.start, S.upto, el);
    // previous period and same period last year, both cut to the same number of elapsed days
    const pS = range(S.P, addD(S.start, -1)).start;
    const pE = minS(addD(pS, S.elapsed), S.start);
    // week: the week 52 weeks earlier (same weekdays); month/year: same month/year one year earlier
    const lyStart =
      S.P === "Week"
        ? addD(S.start, -364)
        : Number(S.start.slice(0, 4)) - 1 + S.start.slice(4);
    const lyEndFull = range(S.P, lyStart).end;
    const lyE = minS(addD(lyStart, S.elapsed), lyEndFull);
    const prevRows = daysSync(hass, pS, pE, el);
    const lyRows = S.P === "Year" ? null : daysSync(hass, lyStart, lyE, el);
    const lyFullRows =
      S.P === "Year" ? daysSync(hass, pS, range("Year", pS).end, el) : null;
    if (!rows) return { ...base, loading: true };
    const tot = sum(rows);
    // forecast over the elapsed days that have one
    // (today is left out: a full-day forecast against a half-finished day would always look bad)
    let f = 0,
      a = 0,
      n = 0;
    const fcSrc = {},
      fcEnd = S.inProg ? S.today : S.upto;
    for (let d = S.start; d < fcEnd; d = addD(d, 1)) {
      const F = fcDay(hass, d);
      if (!F || !(F.v > 0)) continue;
      const r = rows[d];
      if (!r || r.y === undefined) continue;
      f += F.v;
      a += r.y || 0;
      n++;
      fcSrc[F.src] = (fcSrc[F.src] || 0) + 1;
    }
    const prevWord = {
      Week: "last week",
      Month: "last month",
      Year: "last year",
    }[S.P];
    return {
      ...base,
      rows,
      tot,
      months: S.P === "Year" ? byMonth(rows) : null,
      prev: prevRows ? sum(prevRows) : null,
      prevStart: pS,
      prevEnd: pE,
      prevWord,
      ly: lyRows ? sum(lyRows) : null,
      lyStart,
      lyEnd: lyE,
      lyPartial: lyE < lyEndFull,
      lyFull: lyFullRows ? sum(lyFullRows) : null,
      fc: n
        ? {
            f,
            a,
            n,
            N: diffD(S.start, fcEnd),
            src: Object.keys(fcSrc).sort((x, y) => fcSrc[y] - fcSrc[x])[0],
          }
        : null,
      aut: autarky(tot),
      self: selfUse(tot),
    };
  }

  // ── chart series (used by apexcharts data_generators) ────────────────────
  const xDay = (d) => at12(d).getTime();
  const xMonth = (mk) =>
    new Date(+mk.slice(0, 4), +mk.slice(5, 7) - 1, 15, 0).getTime();
  const r2 = (v) => Math.round(v * 100) / 100;
  const VAL = {
    y: (r) => r.y,
    p1: (r) => r.p1,
    p2: (r) => r.p2,
    p3: (r) => r.p3,
    own: (r) => -Math.max((r.h || 0) - (r.i || 0), 0),
    grid: (r) => -Math.min(r.i || 0, r.h || 0),
    bc: (r) => r.bc,
    bd: (r) => -(r.bd || 0),
    pk: (r) => r.pk,
  };

  async function series(hass, key) {
    const S = sel(hass.states);
    if (S.P === "Day") return [];
    if (key === "fc") {
      const out = [];
      if (S.P === "Year" && TYPICAL) {
        for (let m = 0; m < 12; m++)
          out.push([
            new Date(+S.start.slice(0, 4), m, 15).getTime(),
            TYPICAL[m],
          ]);
        return out;
      }
      loadFcOnce(hass);
      if (S.P === "Year") {
        // Months whose every past day had a forecast; partial months would mislead
        const g = {};
        for (let d = S.start; d < S.end; d = addD(d, 1)) {
          const F = fcDay(hass, d),
            o = (g[mKey(d)] = g[mKey(d)] || { v: 0, n: 0, all: 0 });
          o.all++;
          if (F && F.v > 0) {
            o.v += F.v;
            o.n++;
          }
        }
        return Object.keys(g)
          .filter((mk) => g[mk].n && g[mk].n === g[mk].all)
          .map((mk) => [xMonth(mk), r2(g[mk].v)]);
      }
      for (let d = S.start; d < S.end; d = addD(d, 1)) {
        const F = fcDay(hass, d);
        if (F && F.v > 0) out.push([xDay(d), r2(F.v)]);
      }
      return out;
    }
    const rows = await daysAsync(hass, S.start, S.upto);
    const f = VAL[key];
    if (!f) return [];
    if (S.P === "Year") {
      if (key === "pk")
        return Object.entries(byMonth(rows)).map(([mk, t]) => [
          xMonth(mk),
          r2(t.pk),
        ]);
      const g = {};
      Object.entries(rows).forEach(([d, r]) => {
        const v = f(r);
        if (v === undefined || v === null) return;
        g[mKey(d)] = (g[mKey(d)] || 0) + v;
      });
      return Object.keys(g)
        .sort()
        .map((mk) => [xMonth(mk), r2(g[mk])]);
    }
    return Object.keys(rows)
      .sort()
      .map((d) => {
        const v = f(rows[d]);
        return [xDay(d), v === undefined || v === null ? null : r2(v)];
      });
  }

  /** Typical day: average kW per hour over the elapsed days (completed days only if there are any). */
  async function profile(hass, key) {
    const S = sel(hass.states);
    if (S.P === "Day") return [];
    const H = await hoursAsync(hass, S.start, S.upto);
    let days = Object.keys(H).sort();
    if (days.length > 1) days = days.filter((d) => d !== S.today);
    const out = [],
      base = new Date();
    base.setHours(0, 0, 0, 0);
    for (let h = 0; h < 24; h++) {
      let s = 0,
        n = 0;
      days.forEach((d) => {
        const R = H[d] || {};
        const v =
          key === "y" ? (R.y ? R.y[h] : null) : R[key] ? R[key][h] : null;
        if (v !== null && v !== undefined) {
          s += v;
          n++;
        }
      });
      const t = new Date(base);
      t.setHours(h, 30, 0, 0);
      out.push([t.getTime(), n ? r2(s / n) : null]);
    }
    return out;
  }

  /** Hour-of-day averages for insights: {solar:[24], house:[24], imp:[24], n} */
  function hourAvg(H, today) {
    let days = Object.keys(H || {}).sort();
    if (days.length > 1) days = days.filter((d) => d !== today);
    const o = { solar: [], house: [], imp: [], n: days.length };
    for (let h = 0; h < 24; h++) {
      [
        ["solar", "y"],
        ["house", "h"],
        ["imp", "i"],
      ].forEach(([k, s]) => {
        let t = 0,
          n = 0;
        days.forEach((d) => {
          const v = H[d] && H[d][s] ? H[d][s][h] : null;
          if (v !== null && v !== undefined) {
            t += v;
            n++;
          }
        });
        o[k].push(n ? t / n : null);
      });
    }
    return o;
  }

  // ── navigation (from taps) ───────────────────────────────────────────────
  /** Tap handling for HTML inside button-card custom fields (data-tap="go|calmode|pick", data-more-info). */
  function tapHook(el) {
    if (!el || el.__tapP) return;
    el.__tapP = true;
    try {
      const st = document.createElement("style");
      st.textContent =
        "[data-more-info],[data-tap]{pointer-events:auto!important;cursor:pointer}";
      (el.shadowRoot || el).appendChild(st);
    } catch (x) {
      /* ignore */
    }
    let sx = 0,
      sy = 0,
      hit = null;
    const find = (ev) =>
      ev
        .composedPath()
        .find((n) => n && n.dataset && (n.dataset.moreInfo || n.dataset.tap));
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
      else if (d.tap === "go") go(d.arg);
      else if (d.tap === "calmode" && SD && SD.setCalMode)
        SD.setCalMode(h, d.arg);
      else if (d.tap === "pick" && SD && SD.pickDay) SD.pickDay(d.arg);
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
        if (Math.abs(t.clientX - sx) > 10 || Math.abs(t.clientY - sy) > 10)
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
  }

  function go(arg) {
    const hass =
      C.hass || (document.querySelector("home-assistant") || {}).hass;
    if (!hass) return;
    const [P, d] = String(arg).split("|");
    if (P && PERIODS.includes(P) && periodOf(hass.states) !== P)
      hass.callService("input_select", "select_option", {
        entity_id: PERIOD_ENTITY,
        option: P,
      });
    if (d)
      hass.callService("input_datetime", "set_datetime", {
        entity_id: DAY_ENTITY,
        date: minS(d, todayIso()),
      });
  }

  window.SolarPeriod = {
    v: VER,
    PERIODS,
    PERIOD_ENTITY,
    DAY_ENTITY,
    get typical() {
      return TYPICAL;
    },
    setTypical,
    KEYS,
    pad,
    iso,
    at12,
    addD,
    diffD,
    todayIso,
    isoWeek,
    mKey,
    ready,
    range,
    step,
    sel,
    labels,
    view,
    fcDay,
    daysSync,
    daysAsync,
    hoursSync,
    hoursAsync,
    yearsSync,
    sum,
    byMonth,
    autarky,
    selfUse,
    hourAvg,
    series,
    profile,
    go,
    wake,
    tapHook,
  };
})();
