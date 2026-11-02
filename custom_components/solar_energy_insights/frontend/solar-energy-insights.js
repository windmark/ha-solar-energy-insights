/* Solar Energy Insights – shared helpers for dashboards (button-card and apexcharts).
   Shipped with the integration and loaded on every page: nothing to install, no resource to add.
   This home's counters, history start and strings come from the integration's own settings
   (WebSocket solar_energy_insights/config). Exposed as window.SolarDash; caches live in
   window.__solarDash, shared with the cards. */
(function () {
  const G = () => {
    const g = (window.__solarDash = window.__solarDash || { stats: {}, fc: null, waiters: new Set() });
    g.hourlyP = g.hourlyP || {}; g.hourly = g.hourly || {}; g.weekP = g.weekP || {}; g.week = g.week || {};
    g.hourlyFail = g.hourlyFail || {}; g.strH = g.strH || {};
    return g;
  };
  /* This home's settings, from the integration, fetched once and shared. */
  const RETRY_CONFIG_MS = 30000;
  const notify = function () {
    G().waiters.forEach(function (f) { try { f(); } catch (x) {} });
    ((window.__sei || {}).listeners || []).forEach(function (f) { try { f(); } catch (x) {} });
  };
  const config = function () { return G().cfg || null; };
  const loadConfig = function (hass) {
    const g = G();
    if (g.cfg) return Promise.resolve(g.cfg);
    if (g.cfgP) return g.cfgP;
    if (!hass || !hass.callWS || (g.cfgFail && Date.now() - g.cfgFail < RETRY_CONFIG_MS)) return Promise.resolve(null);
    g.cfgP = hass.callWS({ type: 'solar_energy_insights/config' })
      .then(function (c) { g.cfg = c; notify(); return c; })
      .catch(function () { g.cfgFail = Date.now(); return null; })
      .finally(function () { delete g.cfgP; });
    return g.cfgP;
  };
  /* The counters by role; [] where not configured. */
  const hid = function () {
    const e = (config() || {}).entities || {};
    const one = function (x) { return x ? [x] : []; };
    return { solar: one(e.solar), house: one(e.house), imp: one(e.import), exp: one(e.export), soc: one(e.soc) };
  };
  /* What the configured counters' entity ids share, up to an underscore, for cards that build on it. */
  const prefix = function () { return (config() || {}).prefix || ''; };
  /* The first day with statistics; 0 until the settings are in. */
  const start = function () { const d = (config() || {}).history_start; return d ? new Date(d + 'T00:00').getTime() : 0; };
  /* Each string's size in kWp, by number, for those that have one: { 1: 5.06, … }. */
  const stringKwp = function () {
    const out = {};
    ((config() || {}).strings || []).forEach(function (s) { if (s.kwp) out[s.n] = s.kwp; });
    return out;
  };
  const dayRange = function (d) { const s = new Date(d + 'T00:00'); const e = new Date(s); e.setDate(e.getDate() + 1); return [s, e]; };


  /* Hourly energy + battery level for one local day. Failed requests are retried after 30 s (no tight loop). */
  const RETRY_MS = 30000;
  const loadHourly = function (hass, d) {
    const g = G();
    if (g.hourlyP[d]) return g.hourlyP[d];
    const failed = g.hourlyFail[d];
    if (failed && Date.now() - failed < RETRY_MS) {
      const wait = new Promise(function (r) { setTimeout(r, RETRY_MS - (Date.now() - failed)); })
        .then(function () { delete g.hourlyP[d]; return null; });
      g.hourlyP[d] = wait; return wait;
    }
    if (!hass || !hass.callWS) return Promise.resolve(null);
    const r0 = dayRange(d);
    let HID;
    const p = loadConfig(hass).then(function () {
      HID = hid();
      const ids = [].concat.apply([], Object.values(HID));
      if (!ids.length) return null;
      return hass.callWS({ type: 'recorder/statistics_during_period', start_time: r0[0].toISOString(),
        end_time: r0[1].toISOString(), statistic_ids: ids, period: 'hour', types: ['change', 'mean'] });
    })
      .then(function (r) {
        if (!r) { delete g.hourlyP[d]; return null; }
        const out = {};
        Object.keys(HID).forEach(function (k) {
          const type = k === 'soc' ? 'mean' : 'change';
          out[k] = null;
          for (const id of HID[k]) {
            const rows = r && r[id];
            if (rows && rows.length) {
              const arr = new Array(24).fill(null);
              const cnt = new Array(24).fill(0);
              rows.forEach(function (row) { const v = row[type]; if (v === null || v === undefined) return;
                const hr = new Date(row.start).getHours();
                /* DST: an hour can occur twice – add energy, average the battery level */
                if (arr[hr] === null) arr[hr] = v; else arr[hr] = type === 'mean' ? (arr[hr] * cnt[hr] + v) / (cnt[hr] + 1) : arr[hr] + v;
                cnt[hr]++; });
              out[k] = arr; break;
            }
          }
        });
        delete g.hourlyFail[d];
        g.hourly[d] = out; return out;
      })
      .catch(function () { g.hourlyFail[d] = Date.now(); delete g.hourlyP[d]; return null; });
    g.hourlyP[d] = p; return p;
  };

  /* Energy-dashboard solar forecast (all configured forecasts summed), cached 30 min */
  const loadFc = function (hass) {
    const g = G();
    const f = g.fc;
    if (f && f.p && Date.now() - f.t < 30 * 60e3) return f.p;
    const p = hass.callWS({ type: 'energy/solar_forecast' }).then(function (r) {
      const hours = {};
      Object.values(r || {}).forEach(function (e) { Object.entries((e && e.wh_hours) || {}).forEach(function (kv) {
        const ms = new Date(kv[0]).getTime(); hours[ms] = (hours[ms] || 0) + kv[1]; }); });
      g.fc = { state: Object.keys(hours).length ? 'done' : 'none', t: Date.now(), hours: hours, p: p };
      return hours;
    }).catch(function () { g.fc = { state: 'none', t: Date.now(), hours: {}, p: null }; return {}; });
    g.fc = { state: 'pending', t: Date.now(), hours: (f && f.hours) || {}, p: p };
    return p;
  };
  const fcHourly = function (hours, d) {
    const r0 = dayRange(d); const arr = new Array(24).fill(null); let any = false;
    Object.entries(hours || {}).forEach(function (kv) { const ms = +kv[0];
      if (ms >= r0[0].getTime() && ms < r0[1].getTime()) { const hr = new Date(ms).getHours(); arr[hr] = (arr[hr] || 0) + kv[1] / 1000; any = true; } });
    return any ? arr : null;
  };

  /* Per-string kWh per hour for one day: { 1: [24], 2: [24], 3: [24] }, from PowerOcean's
     per-string power. The recorder's hourly mean in kW is the hour's energy in kWh; the hour
     in progress comes from the five-minute statistics. */
  const stringHours = async function (hass, day, isToday, now) {
    const g = G();
    const zero = function () { return new Array(24).fill(0); };
    const cfg = await loadConfig(hass);
    const strings = (cfg && cfg.strings) || [];
    const out = {};
    strings.forEach(function (s) { out[s.n] = zero(); });
    if (!strings.length) return out;
    const dayStart = new Date(day + 'T00:00');
    const key = day + (isToday ? '@' + now.getHours() + ':' + Math.floor(now.getMinutes() / 5) : '');
    let c = g.strH[day];
    if (!c || c.key !== key) {
      const e = new Date(dayStart); e.setDate(e.getDate() + 1);
      const ids = strings.map(function (s) { return s.power; });
      const ask = function (from, to, period) {
        return hass.callWS({ type: 'recorder/statistics_during_period', start_time: from.toISOString(), end_time: to.toISOString(),
          statistic_ids: ids, period: period, types: ['mean'], units: { power: 'kW' } });
      };
      const hourNow = new Date(now); hourNow.setMinutes(0, 0, 0);
      c = g.strH[day] = { key: key, p: Promise.all([ask(dayStart, e, 'hour'), isToday ? ask(hourNow, now, '5minute') : Promise.resolve({})])
        .then(function (both) { const hours = both[0] || {}, fine = both[1] || {}, res = {};
          strings.forEach(function (s) {
            const a = zero();
            (hours[s.power] || []).forEach(function (x) { if (x.mean > 0) a[new Date(x.start).getHours()] += x.mean; });
            if (isToday) {
              const h = now.getHours();
              a[h] = (fine[s.power] || []).reduce(function (sum, x) { return sum + Math.max(x.mean || 0, 0) * 5 / 60; }, 0);
              for (let i = h + 1; i < 24; i++) a[i] = 0;
            }
            res[s.n] = a.map(function (x) { return Math.round(x * 100) / 100; });
          });
          return res; })
        .catch(function () { delete g.strH[day]; return null; }) };
    }
    const v = await c.p;
    return v || out;
  };


  /* ── Day totals for a whole month: { 'YYYY-MM-DD': { y, h, i, e } } ─────────────
     y solar · h house · i bought · e exported (kWh), from the first day with statistics.
     Past months are cached for the session, the current month for 30 min. */
  const isoDay = function (d) { const p = function (n) { return String(n).padStart(2, '0'); };
    return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()); };
  const monthDays = function (hass, y, m) {
    const g = G(); g.month = g.month || {}; g.monthP = g.monthP || {};
    const key = y + '-' + m, c = g.month[key];
    const now = new Date(), isCur = y === now.getFullYear() && m === now.getMonth();
    if (c && !c.failed && (!isCur || Date.now() - c.t < 30 * 60e3)) return Promise.resolve(c.v);
    if (c && c.failed && Date.now() - c.t < 60e3) return Promise.resolve(c.v);
    if (g.monthP[key]) return g.monthP[key];
    const s = new Date(y, m, 1), e = new Date(y, m + 1, 1);
    if (s.getTime() > Date.now()) return Promise.resolve({});
    const p = (async function () {
      const out = {};
      await loadConfig(hass);
      const HID = hid(), START = start();
      const ids = [].concat(HID.solar, HID.house, HID.imp, HID.exp);
      if (ids.length && e.getTime() > START && hass && hass.callWS) {
        const st = new Date(Math.max(s.getTime(), START));
        const r = await hass.callWS({ type: 'recorder/statistics_during_period', start_time: st.toISOString(),
          end_time: e.toISOString(), statistic_ids: ids, period: 'day', types: ['change'] });
        const K = { y: HID.solar, h: HID.house, i: HID.imp, e: HID.exp };
        Object.keys(K).forEach(function (k) {
          const id = K[k].find(function (x) { return r && r[x] && r[x].length; }); if (!id) return;
          r[id].forEach(function (row) { if (row.change === null || row.change === undefined) return;
            const d = isoDay(new Date(row.start)); (out[d] = out[d] || {})[k] = Math.max(row.change, 0); });
        });
      }
      g.month[key] = { t: Date.now(), v: out };
      return out;
    })().catch(function () { g.month[key] = { t: Date.now(), v: (c && c.v) || {}, failed: true }; return g.month[key].v; })
      .finally(function () { delete g.monthP[key]; });
    g.monthP[key] = p;
    return p;
  };
  /* Synchronous view for button-card: returns cached data (possibly stale) or null, and calls
     onLoad once fresh data has arrived so the card can re-render. */
  const monthDaysSync = function (hass, y, m, onLoad) {
    const g = G(); g.month = g.month || {};
    const c = g.month[y + '-' + m];
    const now = new Date(), isCur = y === now.getFullYear() && m === now.getMonth();
    const fresh = c && !c.failed && (!isCur || Date.now() - c.t < 30 * 60e3);
    if (!fresh && !(c && c.failed && Date.now() - c.t < 60e3)) {
      monthDays(hass, y, m).then(function () { if (onLoad) { try { onLoad(); } catch (x) {} } });
    }
    return c ? c.v : null;
  };

  /* Tap handlers used from inline onclick="" in button-card HTML */
  const pickDay = function (d) {
    const ha = document.querySelector('home-assistant'), h = ha && ha.hass;
    if (h) h.callService('input_datetime', 'set_datetime', { entity_id: 'input_datetime.solar_day', date: d });
  };
  const setCalMode = function (el, mode) {
    const g = G(); g.calMode = mode;
    try { window.localStorage.setItem('solarDash.calMode', mode); } catch (x) {}
    const host = el && el.getRootNode && el.getRootNode().host;
    if (host && host.requestUpdate) { try { host.requestUpdate('_config'); } catch (x) {} }
  };
  const calMode = function () {
    const g = G();
    if (!g.calMode) { try { g.calMode = window.localStorage.getItem('solarDash.calMode') || 'y'; } catch (x) { g.calMode = 'y'; } }
    return g.calMode;
  };


  /* ── Jump to a card on the current page ─────────────────────────────────────────────
     Target cards carry an invisible marker: <a data-solar-jump="battery"></a>. Cards live in
     nested shadow roots, so the search walks them. Fallback: a heading card with that text. */
  const deepFind = function (pred) {
    const stack = [document];
    while (stack.length) {
      const root = stack.pop();
      const all = root.querySelectorAll ? root.querySelectorAll('*') : [];
      for (let i = 0; i < all.length; i++) {
        const e = all[i];
        if (pred(e)) return e;
        if (e.shadowRoot) stack.push(e.shadowRoot);
      }
    }
    return null;
  };
  const JUMP_HEADINGS = { forecast: 'Forecast', today: 'Solar today', battery: 'Battery' };
  const jump = function (key) {
    let el = deepFind(function (e) { return e.getAttribute && e.getAttribute('data-solar-jump') === key; });
    if (!el && JUMP_HEADINGS[key]) el = deepFind(function (e) {
      return e.tagName === 'HUI-HEADING-CARD' && e.shadowRoot && (e.shadowRoot.textContent || '').trim().indexOf(JUMP_HEADINGS[key]) === 0; });
    if (!el) return;
    el.style.scrollMarginTop = '96px';                  // clear the app header
    el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    const card = (el.closest && el.closest('ha-card')) || (el.shadowRoot && el.shadowRoot.querySelector('ha-card'));
    if (card) {                                         // brief outline so you see where you landed
      const prev = card.style.boxShadow, prevT = card.style.transition;
      card.style.transition = 'box-shadow .25s ease';
      setTimeout(function () { card.style.boxShadow = '0 0 0 2px #f5b83d'; }, 350);
      setTimeout(function () { card.style.boxShadow = prev; setTimeout(function () { card.style.transition = prevT; }, 300); }, 1700);
    }
  };
  const jumpMark = function (key) { return '<a data-solar-jump="' + key + '" style="display:block;height:0;overflow:hidden"></a>'; };

    /* Shared helpers for the cards: colours, reading states, small pieces of HTML,
     and a WebSocket cache shared by every card (re-rendered when data arrives). */
  const ui = (el, hass, states) => {
    const W = (window.__sei = window.__sei || {});
    const C = { text: '#fff', muted: '#8e8e93', faint: '#636366', track: '#2c2c2e',
      typical: '#5ac8fa', heavy: '#ffb74d', ok: '#8be36a', bad: '#ff8a80' };
    const st = id => states[id];
    const val = id => { const s = st(id); return !s || ['unknown', 'unavailable', ''].includes(s.state) ? null : s.state; };
    const attr = (id, a) => st(id)?.attributes?.[a];
    const num = v => (v === null || v === undefined || v === '' || isNaN(+v)) ? null : +v;
    const hm = d => d ? new Date(d).toTimeString().slice(0, 5) : '–';
    const kwh = v => num(v) === null ? '–' : num(v).toFixed(1) + ' kWh';
    const sub = t => `<div style="font-size:11px;color:${C.muted};margin-top:8px;white-space:normal;line-height:1.4">${t}</div>`;
    const head = (icon, title, right) => `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
      <div style="display:flex;align-items:center;gap:8px;font-size:15px;color:${C.text}"><ha-icon icon="${icon}" style="--mdc-icon-size:18px;color:${C.muted}"></ha-icon>${title}</div>
      <div style="font-size:12px;color:${C.muted}">${right || ''}</div></div>`;
    const pill = (text, color) => `<span style="font-size:12px;padding:2px 10px;border-radius:10px;background:${color}22;color:${color}">${text}</span>`;
    if (el && !el.__seiListen) {
      el.__seiListen = true;
      (W.listeners = W.listeners || []).push(() => { try { el.requestUpdate('_config'); } catch (x) {} });
    }
    const ws = (key, type, maxAgeS) => {
      const c = W[key];
      if ((!c || (!c.pending && Date.now() - c.t > maxAgeS * 1000)) && hass?.callWS) {
        W[key] = { ...(c || {}), t: Date.now(), pending: true };
        hass.callWS({ type }).then(d => { W[key] = { t: Date.now(), data: d }; })
          .catch(e => { W[key] = { t: Date.now(), error: String(e?.message || e) }; })
          .finally(() => (W.listeners || []).forEach(f => f()));
      }
      return W[key]?.data;
    };
    if (hass) loadConfig(hass);
    const waiting = (key, what) => `<div style="font-size:12px;color:${C.muted}">${W[key]?.error ? 'Could not read ' + what + ': ' + W[key].error : 'Reading ' + what + '…'}</div>`;
    return { W, C, st, val, attr, num, hm, kwh, sub, head, pill, ws, waiting };
  };

  window.SolarDash = {
    version: 13, ui, config, loadConfig, stringKwp, dayRange, loadHourly, loadFc, fcHourly, stringHours,
    isoDay, monthDays, monthDaysSync, pickDay, setCalMode, calMode, jump, jumpMark,
    /* Read when a card renders, from the integration's settings. */
    get PFX() { return prefix(); },
    get HID() { return hid(); },
    get START() { return start(); },
  };
  /* Ask for the settings straight away with the frontend's own connection, when there is one. */
  try { const ha = document.querySelector('home-assistant'); if (ha && ha.hass) loadConfig(ha.hass); } catch (x) {}
})();
