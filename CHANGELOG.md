# Changelog

## Unreleased

## [0.2.0] - 2026-11-08

### Fixed

- Week, month and year totals and the day calendar leave out counter jumps. A counter that drops out and comes back makes the recorder book its whole total in one hour; the daily and monthly statistics keep those hours, so a summer month could show several times its real yield. Day totals are now summed from the hourly statistics, dropping such hours the way the integration's own records do; the day view's hourly figures (`loadHourly`) drop them too.
- Today's row in the week, month and year view comes from the five-minute statistics. It read the configured counters' states, which are lifetime totals, so today showed the whole system's yield.

## [0.1.0] - 2026-11-08

### Changed

- A shorter README with pictures of the tiles; the reference moved to `docs/reference.md`.

- "Longest off the grid" is the longest consecutive stretch off the grid, across midnight and across days, instead of the most off-grid hours within one day, which stopped at 24.
- Load spikes no longer count as grid use. When a load switches on, the inverter takes a moment to ramp the battery up and the grid covers it. An hour the battery carried (above its reserve all hour) whose import was only that small share of the house's use counts as off the grid. The share is learned from hours the battery had plenty (their 95th percentile, at most 10 %), and shows as the diagnostic sensor "Grid share during load spikes". It applies to the record, Island mode, grid-free days and the self-sufficiency streak, which leave out only the import excused this way. Stored facts are rebuilt once with it.

- A string's size is asked as its number of panels and the watts per panel, two whole numbers, instead of kWp. Some phone keypads offer only a comma for decimals, which the field does not accept, so a size like 5.06 could not be entered. A kWp value set before still counts.

- Requires Home Assistant 2026.9 or newer, and so Python 3.14 (was 2025.12). CI tests the oldest supported release, the pinned one and the newest; the workaround for 2025.12's repairs is gone.

### Added

- `window.SolarPeriod` in the shipped script, for a history page that switches between day, week, month and year: the period's totals, the one before and the same one last year, day and month series for charts, a typical day by the hour, and taps that move between periods. Day totals come from the daily statistics a month at a time, shared by every period; today's row uses the live day counters. Nothing is fetched before the integration's settings arrive. A typical year (PVGIS, say) can be passed with `setTypical()`.

- Card settings, in the dashboard's visual editor: which rows the now card shows and in what order, its header and taps; until sunrise's columns and notes; best time's chart; which forecast checkpoints show; the level card's quest and recap; which badge sections show. `docs/cards.md` lists them and shows how to build your own card from the Summary sensor.

- The cards follow the theme: text, divider and card colours from Home Assistant, status colours chosen for light or dark themes, and `--sei-good`, `--sei-warn`, `--sei-bad`, `--sei-info` and `--sei-track` to override them.

- The solar cards ship with the integration: `custom:solar-energy-insights-now`, `-until-sunrise`, `-best-time` and `-forecast-trust`. Appliances (name, icon, typical kWh and hours, optionally a sensor that learns energy per run) and an appliance already running are card settings; without them the cards show the house alone. Everything else comes from the integration's settings.

- Quests carry the plan's advice for meeting them, as it stood when they were set: tonight's "get to sunrise without the grid" what is left of the evening's budget, tomorrow's "keep the sun at home" and "be self-sufficient" the best window for heavy loads, "fill the battery before" that heavy loads after then won't slow it.
- The achievements answer gives last week in one line (nights without the grid, the best day, the sun kept at home and what it was worth, badges earned) and what yesterday was the best at in a while ("best day since 12 Oct", or "on record" with a month behind it; gaps under a week are not news). The level card shows both, and the quest's advice.

- The achievement cards ship with the integration as real cards: `custom:solar-energy-insights-level`, `-streaks`, `-records` and `-badges`, each one line in a dashboard and listed in the card picker. The achievements answer's `today` carries the sun so far (`solar_kwh`), so the records card needs no sensor of its own. They draw into their own card, so taps, redraws and what is selected work the ordinary way, and are tested in CI.

- Each badge in the achievements view says when it can next be earned (`season_from`, `season_until`: the window it is in, or the next one, found with the same rule that decides "in season"), whether it is `complete`, and the count its next tier needs (`next_tier_at`), for dashboards that group badges into within reach, coming up and complete.

- Forecast checkpoints: Solcast's forecast is frozen at 18:00 and 21:00 (for tomorrow), 06:00 (for today) and 12:00 (for the afternoon), with when Solcast last refreshed. Once a day is over, each is judged against the solar counter on the stretch it was about. The Forecast checkpoints sensor gives, per checkpoint over the last 30 days: the typical miss in kWh and as a share of a day, the lean (optimistic or pessimistic), how often the real sun fell inside the forecast's range, the same split by what the forecast said (sunny, mixed or dull, against a clear day's energy from the roofs), the same weeks last year once there is a year, and whether waiting from 18:00 to 21:00 or from 06:00 to 12:00 cuts the miss. Scores appear after 10 days (8 per group); it needs the solar energy counter.

- Each PowerOcean string found gets three sensors (power per kWp writes a change of 0.01 kW/kWp at once and smaller drift at most every 30 s, since every write goes into the recorder): energy today, power per kWp and yield today, from its own power and its size in the options. The energy sensor's long-term statistics are filled in from the string's power history on first start, so charts keep every past day. Each string can be placed on one of the Solcast sites ("Roof of string PV1"), so dashboards compare strings on the same roof.
- A Morning forecast sensor: Solcast's forecast for today as it stood in the morning, with past mornings as attributes (each day's total and hourly profile), for charts of forecast against actual.

- The dashboard helpers ship with the integration and load on every page: nothing in /config/www, no resource to add, updated with the integration. What they used to hardcode comes from the integration's settings through `solar_energy_insights/config`: the configured counters, the first day with statistics, and PowerOcean's per-string power sensors, found on the same installation as the battery (their hourly mean power is the hour's energy, so no template sensors are needed). Each string found gets an optional size in the options, explained in plain words.

- A Summary sensor: what the dashboard says, as rows (Battery, Tonight, Heavy loads, Surplus, and Last night in the morning), each a headline and at most two short clauses chosen by the time of day. The wording lived in the dashboard's JavaScript, copied into two cards and untested; it is now in `core/summary.py`, with real moments word for word and over a thousand day scenarios checked in CI for length and for not contradicting itself. The day's phases come from the plan's own sunset and sunrise.

- How long a changed answer stays shown is an option, from 30 minutes to 2 hours.
- "Battery full at" carries `this_morning` in the day: the full time the plan said this morning, the sun so far (with the solar counter) and the morning forecast for those hours.
- "Night outlook" carries `last_night`: what the plan said at 21:00, what happened, and how many of the recent calls were right.

- What changed in the last half hour: when tonight's outlook, the grid time, the spare at sunrise or the time the battery is full moves meaningfully (15 minutes, 0.3 kWh, or a different outlook), "Night outlook" and "Battery full at" carry a `changed` attribute with the earlier answer, since when, and the likely cause (the house using more or less than usual, or Solcast expecting more or less sun). It clears itself after half an hour.

- Tonight's budget, on "Night outlook": what the battery can deliver from sunset to its reserve, how much of it the evening may use (leaving your usual after-midnight use), your usual evening, the house's use since sunset, live, and the recent nights that ran short with their evening's use. A borderline night says why and what keeps it covered.

### Removed

- The fallback to the dashboard's `input_number.electricity_price` and `input_number.feed_in_tariff` helpers. Prices come from a price entity, else the options, else the default.

Complications that, measured on real data, did not earn their place:

- Weighting past nights by similarity (the afternoon so far, weekday or weekend, outdoor temperature, presence), the choice between weightings and its stored state, and the temperature and presence options. On a month of real evenings, counting every night equally predicted better (0.48 against 0.53 kWh).
- Battery health and cycle counts from the EcoFlow cloud integration, and the "Cycle master" badge. The plan uses the nominal capacity; the learned hidden load absorbs a steady shortfall.
- The battery's live charge and discharge limits. The plan steps in hourly averages, and real houses rarely average more than the battery's limit over an hour; the fixed power limit option remains.

### Added

- The array is read from Solcast's rooftop sites: its size, and where each plane faces. Per-kWp badges scale with a plane's yearly yield against an ideal roof, so east–west and north-facing arrays earn them at their own pace. The array's size is no longer estimated from output.
- The options prefill what is found in EF-PowerOcean-TcpModbus and Solcast.
- A blueprint that announces achievements on your phone, and the value of the sun kept at home in each monthly recap.
- Achievements, optional: a daily score, XP and named levels, ranks that follow the season, four streaks with a weather shield, records, 22 badges relative to the home and seasonal by hemisphere, daily and weekly quests sized from your own days, monthly recaps, and an achievement event. Backfilled from the history on first start. Design: docs/achievements.md.

- The battery's answers: when it fills (expected and cloudy), its level at sunset, the best window for heavy loads, and free power with until when.
- The night, by replaying the house's recent nights from the recorder: nights covered, reserve time and spare energy on a typical and a heavy night, a three-way outlook (lasts, borderline, needs the grid), and the charge needed for the night (at sunset in the day, now at night; 100 % with the shortfall when even full is not enough).
- Learning from the last 30 evenings (about a month): a hidden load the house counter never sees (such as the inverter's standby), and how heavy a heavy night is. Both are kept only when they predict evenings they were not fitted on better.
- Battery readings below the floor (a glitch, or a battery asleep at its reserve) are cleaned and counted.
- A small store keeps Solcast's morning forecast, and the plan's own answers. It gives a live full-at error, live night scores, and, with a solar energy counter, a per-hour Solcast correction for the roof once it beats raw Solcast on left-out days.
- Prices: a fixed price and feed-in tariff (falling back to the dashboard's helpers until saved), with an optional price entity for dynamic tariffs; grid cost until sunrise and the saving from using the surplus. Prices never change the plan.
- The plan reply shows the day's working for dashboards: solar and house use until sunset, the cloudy curve, any reserve time before sunset, and expected solar and house power per half hour.
- The `simulate` action for an extra load; WebSocket commands `plan` (with each replayed night's battery curve) and `backtest`; diagnostics that replay the exact moment offline.
- numpy batches every replay: a plan takes about 7 ms, calibration about 25 ms.
- A charge limit, from an entity (PowerOcean's Charge Limit SOC is found at setup) or the options. Charging stops there and "full" means reaching it, for the day, charge needed, free power and the full-at score; what the battery no longer takes counts as surplus.
- Setup prefilled from EF-PowerOcean-TcpModbus and Solcast.

### Fixed

- The shipped appliance cards shared a cache name with dashboard tiles built from the same code, and overwrote their results in a new shape ("undefined is not an object (evaluating 'both.full_at')"). They keep their own now.

- The badges card's progress bar had no width and didn't show.
- The streaks card's explanation and the streaks' and records' descriptions ran off the card on a phone: they now wrap.

- Dashboard tiles showed "Could not read the plan: 3" after Home Assistant restarted or the app lost its connection: 3 is the frontend's code for a lost connection, and a failed request replaced the last good answer with the error. A failed request now keeps the last answer, retries by itself after 5 s and then longer, retries at once when the connection is back, and says in words what happened when there is nothing to show yet.
- The shipped script's address carried the integration's version, which did not change between updates, so browsers could keep an old copy. It now carries a fingerprint of the script itself.

- "Night prediction error" and "Full-at error" are diagnostic sensors, beside "Forecast correction" and "Days of history": they describe the integration's own accuracy, not the house.

- The day and the night are stepped by one engine. The step-by-step day simulation is now one battery through the same code as the nights, which keeps the books the day needs (when the battery filled, export before and after, the peak and when, the low, the level at sunset). The original loop is kept in the tests as an independent reference; the engine agrees with it on every result, over 1,000 random days in CI and 3,000 checked by hand.

- "Last night" and the live night score now read one record of each night's call and outcome. They disagreed: the live score counts only nights that could have gone either way, "last night" counts every call, and each took the outcome from a different place.
- Options an earlier version had are dropped when the integration loads.
- When the reserve comes from PowerOcean's plain limit and its effective reserve exists, a repair under Settings offers to switch, in one click. The plain limit can read 0 % while the app keeps a higher backup reserve.

- A battery switched off below its reserve reports nothing, which stopped the integration from starting ("The battery's capacity is not available"). Its last good capacity, state of charge and reserve are now kept and stand in until it reports again; before any are stored, the recorder's last values are used. Only a battery never seen at all is waited for. The level used is shown on "Charge needed" (`battery_now`, `battery_remembered`).

- "Nights covered" is a replay from the battery as it is now, so once the battery reaches its reserve at night it reads 0 %, which looked like "no night lasts". It now also carries what really happened: how many of the recent nights lasted until sunrise, and whether last night did.

- A counter that first reports 0 and then its lifetime total no longer counts as one enormous hour. Records showed it as the best day and the most exported, and the array's size learned from it made every per-kWp badge unreachable; stored achievement facts are rebuilt once, quietly. The Solcast correction, reading the same counter, is protected too.

- During the day, the night is measured from sunset: this morning's reserve is not a failed night, and a heavy night never ends better than a typical one.
- The day's usual load and the fill-in for missing hours come from the recent days replayed, not the whole history read for scoring.
- A day the recorder mostly missed is skipped, not replayed as a night.
- A 0 % reading from a battery asleep at its reserve is taken as the reserve.
- Every saved morning is learned from, however far apart, and large solar hours are kept.
- A failed read from the recorder keeps the answers already made instead of making every entity unavailable.
- Numeric answers keep long-term statistics.
- Calibration looks back a month, not two: two months reached into a different season, whose noisier nights hid the current bias, so the learned hidden load was dropped and the plan turned optimistic again.
