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
      text: "#fff",
      muted: "#8e8e93",
      faint: "#636366",
      track: "#2c2c2e",
      typical: "#5ac8fa",
      heavy: "#ffb74d",
      ok: "#8be36a",
      bad: "#ff8a80",
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
    "display:block;background:#1c1c1e;border-radius:18px;border:none;box-shadow:none;" +
    "padding:18px 20px 20px;color:#e5e5e7;text-align:left;white-space:nowrap;" +
    "font-family:var(--ha-font-family-body, Roboto, sans-serif);font-size:14px;line-height:1.4";
  const defineCard = (tag, name, description, render) => {
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
          body = `<div style="font-size:12px;color:#ff8a80;white-space:normal">${String(
            (e && e.message) || e,
          )}</div>`;
        }
        if (body === this._drawn) return;
        this._drawn = body;
        this.shadowRoot.innerHTML = `<ha-card style="${CARD_STYLE}">${body}</ha-card>`;
      }
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
        Platinum: ["#9ad7e6", "mdi:shield-crown"],
        Diamond: ["#b9f2ff", "mdi:diamond-stone"],
      };
      const bar = (pct, color, h = 6) =>
        `<div style="height:${h}px;background:#000;border-radius:${
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
        ["self_sufficiency", "Self-sufficiency", 40, "#8be36a"],
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
    <span style="color:#c7c7cc">${label}</span><span style="color:#fff">${pts}<span style="color:#636366"> / ${Math.round(
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
        ? `<div style="background:#1f2a1f;border-radius:12px;padding:10px 12px;margin:12px 0 4px">
    <div style="display:flex;justify-content:space-between;align-items:center;font-size:11px;color:#8be36a">
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
    <div style="font-size:15px;color:#fff;font-weight:500;margin-top:4px;white-space:normal">${
      open.title
    }</div>${
      open.hint
        ? `<div style="font-size:12px;color:#a8d5a0;margin-top:3px;white-space:normal">${open.hint}</div>`
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
        ? `<div style="font-size:11px;color:#8be36a;margin-top:12px;white-space:normal">Yesterday: ${news.join(
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
        ? `<div style="font-size:11px;color:#8e8e93;margin-top:${
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
    <div style="width:56px;height:56px;border-radius:50%;background:#2c2c2e;display:flex;align-items:center;justify-content:center;box-shadow:0 0 0 2px ${rc}55;flex-shrink:0">
      <ha-icon icon="${ricon}" style="--mdc-icon-size:30px;color:${rc}"></ha-icon></div>
    <div style="flex:1;min-width:0">
      <div style="font-size:18px;color:#fff;font-weight:500;white-space:normal;line-height:1.25">Level ${
        L.level
      } · ${L.name}</div>
      <div style="font-size:12px;color:#8e8e93;white-space:normal;line-height:1.35">${
        R.name
      } rank · 30-day average ${R.average}${
        R.next_name ? ` · ${R.next_name} at ${R.next_at}` : ""
      }</div>
      <div style="margin-top:6px">${bar(L.progress * 100, rc, 5)}</div>
      <div style="font-size:11px;color:#636366;margin-top:3px">${(
        L.next_xp - L.xp
      ).toLocaleString("en-GB")} XP to level ${L.level + 1}</div>
    </div>
    <div style="text-align:center;flex-shrink:0"><div style="font-size:30px;color:#fff;font-weight:500;line-height:1">${
      a.today.score ?? "–"
    }</div>
      <div style="font-size:10px;color:#8e8e93">today</div></div>
  </div>${quest}${lastLine}${rows}${yesterdayLine}${weekLine}
  ${
    month
      ? `<div style="font-size:11px;color:#636366;margin-top:12px;white-space:normal">${new Date(
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
        Platinum: ["#9ad7e6", "mdi:shield-crown"],
        Diamond: ["#b9f2ff", "mdi:diamond-stone"],
      };
      const bar = (pct, color, h = 6) =>
        `<div style="height:${h}px;background:#000;border-radius:${
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
            ? ` <span style="color:#9ad7e6;font-size:11px">${'<ha-icon icon="mdi:shield-half-full" style="--mdc-icon-size:13px"></ha-icon>'.repeat(
                s.shields,
              )}</span>`
            : "";
          return `<div style="display:grid;grid-template-columns:36px 1fr auto;gap:12px;align-items:center;margin-top:12px">
    <div style="width:36px;height:36px;border-radius:50%;background:${
      hot ? "#3a2412" : "#2c2c2e"
    };display:flex;align-items:center;justify-content:center">
      <ha-icon icon="${s.icon}" style="--mdc-icon-size:19px;color:${
        hot ? "#ff9f43" : "#636366"
      }"></ha-icon></div>
    <div style="min-width:0"><div style="font-size:14px;color:#fff;white-space:normal">${
      s.name
    }${shields}</div><div style="font-size:11px;color:#8e8e93;white-space:normal;line-height:1.35">${
      s.description
    }</div></div>
    <div style="text-align:right"><div style="font-size:22px;color:${
      hot ? "#fff" : "#636366"
    }">${s.current}${
      hot
        ? ' <ha-icon icon="mdi:fire" style="--mdc-icon-size:16px;color:#ff9f43"></ha-icon>'
        : ""
    }</div>
      <div style="font-size:11px;color:${
        s.personal_best ? "#f5b83d" : "#8e8e93"
      }">${
        s.personal_best ? "personal best!" : `best ${s.best}`
      }</div></div></div>`;
        })
        .join("");
      return `<div style="font-size:12px;color:#8e8e93;white-space:normal;line-height:1.4">Shields come with each week-long run and absorb one break the weather caused.</div>${rows}`;
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
        Platinum: ["#9ad7e6", "mdi:shield-crown"],
        Diamond: ["#b9f2ff", "mdi:diamond-stone"],
      };
      const bar = (pct, color, h = 6) =>
        `<div style="height:${h}px;background:#000;border-radius:${
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
              p >= 100 ? "#8be36a" : "#f5b83d",
              4,
            )}</div><div style="font-size:10px;color:#636366;margin-top:2px">today ${Math.round(
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
    <div style="min-width:0"><div style="font-size:14px;color:#fff;white-space:normal">${
      r.name
    }</div>
      <div style="font-size:11px;color:#8e8e93;white-space:normal;line-height:1.35">${dmy(
        r.day,
      )}${season ? " · " + season : ""}${
        top ? ` · <span style="color:#8be36a">${top}</span>` : ""
      }</div>${chase}</div>
    <div style="font-size:16px;color:#fff;text-align:right">${f(
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
        Platinum: ["#9ad7e6", "mdi:shield-crown"],
        Diamond: ["#b9f2ff", "mdi:diamond-stone"],
      };
      const bar = (pct, color, h = 6) =>
        `<div style="height:${h}px;background:#000;border-radius:${
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
                "#9ad7e6",
                4,
              )}</div><span style="font-size:11px;color:${
                C.muted
              };white-space:nowrap">${fmt(b.count)} of ${
                b.next_tier_at
              }</span></div>`
            : "";
        return `<div style="display:flex;gap:10px;align-items:flex-start;padding:7px 0">${icon(
          b,
          b.season ? "#3a2c12" : "#16303a",
          b.season ? "#ffb74d" : "#9ad7e6",
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
      out += rows.map(reach).join("");
      out += strip("Any time", anyTime, "#16303a", "#9ad7e6");
      out += strip("Later", later, "#2c2c2e", "#8e8e93", (b) =>
        dm(b.season_from),
      );
      out += strip("Complete", done, "#1f3317", "#8be36a", null, true);
      out += `<div style="font-size:12px;color:${
        C.muted
      };line-height:1.45;margin-top:12px;padding-top:10px;border-top:1px solid ${
        C.track
      };white-space:normal">${
        lit === done[0] && !UI.spot
          ? '<span style="font-size:11px;color:#636366">Latest · </span>'
          : ""
      }${spot(lit)}</div>`;
      return out;
    },
  );
})();
