# Changelog

## Unreleased

## [0.28.0] - 2026-11-21

### Changed

- The scenario lab reads the outdoor thermometer for the whole year, also before the solar and the house's meter were set up. Filled days keep its own readings, and a day it measured cold borrows a cold day's sun, so the winter's heat comes from the winter that was, not an estimate from the summer.

## [0.27.0] - 2026-11-21

### Added

- The scenario lab fills the days the recorder doesn't have yet from recorded days with a similar sun, moved to their season, so half a year replays as a whole one with a winter in it. Before, it scaled the recorded days up, which for a summer and an autumn made a heat pump look less than half its size and twice as sunny.
- The scenario lab shows the heat pump's heat for rooms and hot water, its electricity, its seasonal performance factor and its share from the roof, month by month, and what it saves against gas (`gas_price`). A year's gas works out the house's heat loss as it is.
- The heat pump can be given a size (above it, a heating rod adds the rest), its datasheet COP at A2/W35, its flow temperature on the coldest day, and hot water heated in the sunniest hours.
- The battery shows its cycles a year.

### Changed

- The scenario lab's sliders show their value as they move, and what you set is kept across reloads, per dashboard and card.
- The heat pump's COP follows the published curve of real air-source heat pumps (Ruhnau et al. 2019) at the flow temperature the heating curve asks for, in place of a fixed line. Heating days are decided by the day's mean temperature, as degree days count them, and hot water is set at 8 kWh a day in the card unless changed.
- With both a bigger battery and a heat pump, each is priced on its own: the battery's payback no longer includes the heat pump's cost, and the heat pump's roof share no longer counts the bigger battery's.

## [0.26.0] - 2026-11-20

### Added

- Badges get harder as they climb: bronze asks what the badge asked before, and silver and gold ask for better days, not only more of them. Winter warrior wants one winter day at 80 % self-sufficient, then ten at 85 %, then thirty at 90 %.
- Seasonal badges are played in editions, one per season, against last year's. Earn one on clearly more days than last winter (a tenth more, and at least two days) and this winter earns a crown and 150 XP. A new `crown` announcement says so, and the blueprint offers it. Badges ranked against your own days show last season but earn no crown.
- Four badges unlock a harder one once complete: Perfect streak, Harvest moon, Winter legend and Oracle.
- New badges: Bluebird day, which compares a day with what a clear sky would have given; Sun sponge, which compares how much of the day's own sun the house used with other days like it; Wheel of the year; and a hidden one.
- Where Solcast's rooftop sites give the planes' directions, each day is compared with what a clear sky would have made, so a bright December day counts as much as a bright June one.
- The last seven days are compared with the same week a year ago (score, self-sufficiency, yield, grid-free nights and house use), and today's yield with the same day last year.
- Quests have a level, from 1 to 6. Each quest met makes the next ones a little harder and pay a little more; each one missed makes them easier. The quest shows its level, and the quest sensor has it as an attribute.

### Changed

- A day's self-sufficiency, and whether it was grid-free, now count from sunrise. The hours before belong to the night, which the night badges and streaks judge, so a night that ran the battery down no longer puts the day's quest or badges out of reach before the sun is up. The days kept are worked out again from the recorder, without announcing anything.
- The steady trickle an inverter draws from the grid while the battery carries the house no longer counts as grid use. It is learned from your own hours with plenty in the battery (at most 100 W), so a day with nothing else from the grid is 100 % self-sufficient and grid-free.
- The badges card shows this season's badges first, each racing its last season, and says what each badge's next tier asks. A tapped badge shows its ladder and its seasons. The records card chases the same day last year and ends with the last week against a year ago (`year_ago: false` hides it).
- Badge tiers are worked out again under the new rules, so some may move once. No badge got easier: bronze asks what it asked before. Nothing is announced for that.

## [0.25.0] - 2026-11-20

### Changed

- The cards preview with your own data in the dashboard's card picker. The moments card, which hides with nothing new, previews an example.
- The card docs show each card as drawn, what it answers and its editor settings, with the YAML folded underneath.

## [0.24.0] - 2026-11-18

### Fixed

- Appliance suggestions: one per appliance, even when it shows up through several devices or integrations, with the socket's own meter preferred. Estimated or learned counters are no longer suggested, and an appliance that already has a counter gets no more suggestions.

### Changed

- An appliance suggestion now asks Reject or Submit. A rejected one is remembered and not suggested again.
- An appliance's energy counter is all it needs: its runs are found in the counter's last ten days, and their median energy and mean duration replace the typical figures. The separate energy-per-run sensor is gone.

## [0.23.0] - 2026-11-18

### Added

- An appliance can have its own energy counter, such as its plug's. The "if you run…" rows then take its past runs out of the replayed nights before adding tonight's run, so a dishwasher your evenings already had is not counted twice. "House only" still replays your nights as they were.
- Energy counters that look like appliances (a dishwasher's or dryer's plug, in English or German) are suggested. Adding an appliance starts from them, prefilled, and a repair offers to add each one, or to give an appliance of the same name its counter. Nothing is added without you, and an ignored suggestion stays ignored.

## [0.22.0] - 2026-11-16

### Changed

- Every sensor taken from another integration is now an option you can see and change: each string's power, the battery's charged and discharged counters, Solcast's rooftop sites, its "last polled" sensor and its forecasts from day 3 on. Discovery only fills them in, once, on the first start after the update and in the options form; a field you clear stays clear. Nothing reads another integration's sensors by a name worked out in code any more.

## [0.21.0] - 2026-11-16

## [0.20.0] - 2026-11-15

### Added

- A `string_worth` answer replays the recorded year with and without one string: what it made, the grid's import and export, self-sufficiency and grid-free nights each way, and what it was worth. It applies the feed-in cap and gives the grid meters' totals over the same hours, to check the replay against. Hours before the string readings start are left out rather than guessed.
- A Comparisons section in the options: single out a string, choose what it is compared against and give it a name, and choose which strings should produce alike. The dashboard settings carry these choices, so the cards no longer guess or assume the wiring.

### Fixed

- History cards could show Loading for a long time. Their months were all requested at once, and the later ones timed out while waiting for the recorder, then were requested again. Now two months are requested at a time, newest first, and each timeout starts when its request is sent.

### Changed

- Months that are over are kept in the browser, so opening the dashboard again doesn't read them from the recorder again.

## [0.19.0] - 2026-11-15

### Fixed

- The history's energy per string now comes from each string's power, whose statistics are complete, rather than from the per-string energy sensors. Their stored history from before 0.17 carried the previous day into the morning, so past days' split by string was wrong.

## [0.18.0] - 2026-11-15

### Added

- From 18:00 and through the night, the Solar now card's row for the coming day says when the sun reaches a level you choose in the options ("Sun worth starting things on", 1 kW by default): "1 kW of sun from ~09:35". It comes from the same corrected forecast as the rest of the plan. The plan answer carries it as `solar_from` with its `kw`, and the dashboard settings carry `solar_from_kw`, so charts can mark the same time.

## [0.17.0] - 2026-11-15

### Added

- A moments card, `custom:solar-energy-insights-moments`, shows what was just achieved with a line on why: from when it happened until a day later, each on its own clock, and hidden when there is nothing new. The achievements answer carries the last two days' `moments`, kept across restarts, and each achievement event now has an `explain` line too.

### Fixed

- Each string's energy today starts at local midnight. In the first hour of the day (two in summer) it took UTC's date, counted yesterday's whole day, and then held on to it until today's sun beat it, so the strings could show tens of kWh before sunrise. The curtailment check had the same slip.

## [0.16.0] - 2026-11-14

### Fixed

- The Now card's heavy loads and surplus rows agree. The window's spare sun counted what the battery still takes; it now shows what's left once the battery is full, the same as the surplus, as "~21 of the 23 kWh to spare".

## [0.15.0] - 2026-11-13

### Fixed

- The strings' energy no longer comes out at zero for the month and year they were installed in. After the backfill the recorder started its own running total again at zero; the integration now finds that drop and lifts every later hour back into line, at startup and then every hour.
- Today's energy per string no longer dips for a moment just after the hour, which the recorder could read as a meter reset and count the day twice.

## [0.14.0] - 2026-11-13

### Fixed

- The chart of spare sun in Best time to run and the Appliances card shows its bars and times again. Moving theme colours into a style dropped the closing slash of self-closing shapes, so the browser nested every later shape inside the first one and drew none of them.

## [0.13.0] - 2026-11-13

### Added

- An Appliances card: best time to run and until sunrise in one card, sharing one list of appliances, with a Today / Tonight switch. It opens on today while the sun is up and on tonight after sunset; a view picked by hand holds until that day or night is over. The two cards stay as they are.
- Tapping a row of the Now card sends a `sei-jump` event with the row's target before scrolling, so a card with views can open the one the row is about; the Appliances card opens tonight for the tonight row.

### Fixed

- A battery held above its reserve is recognised right after a restart or after its level was unavailable for a while. Both gave the level a new last change, so the 15 minutes started over; the wait now follows the level itself, read back from the recorder at startup.

## [0.12.0] - 2026-11-09

### Fixed

- A battery that stops discharging a percent or so above the reserve it reports, such as a PowerOcean stuck at 1 % while the house runs on the grid, now counts as empty. Once its level has sat at 2 % or less for 15 minutes while the grid supplies the house, the plan takes that level as its floor, so it no longer says the battery runs out in a few minutes, over and over. Past nights are still judged against the reported reserve. "Charge needed for the night" shows it as `battery_held`, and diagnostics as `battery_held_pct`.

## [0.11.0] - 2026-11-08

### Added

- "Typical day": the house's typical day, with its mean use for each hour of the week over the last five weeks as an attribute, for charts of the week.
- With achievements on, "Sun against the forecast", the sun of the last 30 days against each morning's forecast, energy-weighted, with each day and today so far, and "Battery size", whether the battery is the right size: of the days it got full over the last 60, how many ran out at night, with each day's verdict.

## [0.10.0] - 2026-11-08

### Added

- A scenario lab: the `scenario` action replays the last year's recorded solar and house use through the battery, as it is and with more capacity or a heat pump, and says per year what would be bought and exported, the self-sufficiency, the nights without the grid, the saving and the payback. A heat pump needs the outdoor thermometer, now an option again; its load is the heat lost below the heating limit at each hour's temperature, divided by a coefficient of performance that falls in the cold. The `custom:solar-energy-insights-scenario-lab` card has sliders for both.

## [0.9.0] - 2026-11-08

### Added

- Appliances can be added under the integration with **Add appliance**: a name, an icon, what a run takes, and optionally a sensor that learns it. Each is its own device with a "Best start" sensor, the start until sunset that takes the most from free sun, and a "Good time to start" binary sensor, on while starting now takes at least half the run from free sun and about as much as the best start. Cards without appliances of their own show them.
- A blueprint, `start_appliance.yaml`, that starts an appliance once it is a good time, and optionally once it is ready to be started remotely.

## [0.8.0] - 2026-11-08

### Changed

- A charge limit lowered for part of the day no longer counts as the day's. The plan, the charge needed and the night use the highest the limit has been over the last day, read back from the recorder after a restart, so an automation that keeps room for midday doesn't make the morning's answers pessimistic. A limit kept lower for a whole day is the limit. Free power follows the limit as it is now, and "Charge needed for the night" shows it as `charge_limit_now`.

## [0.7.0] - 2026-11-08

### Added

- A feed-in cap in the options, prefilled with PowerOcean's _Maximum feed-in Power_. With it, "Clipping until sunset" says how much solar the cap would curtail with the battery running by itself, and from when until when, and "Curtailed today" estimates what it held back so far: while the export sits at the cap, the forecast less what the roof made. They show whether holding battery room for midday is worth it.

## [0.6.0] - 2026-11-08

### Added

- A feed-in entity in the options, beside the price entity: it overrides the feed-in tariff while it has a value, read as cents when its unit starts with ct. Costs and savings follow it at once, and their sensors show where the tariff came from (`feed_in_source`).

## [0.5.0] - 2026-11-08

### Added

- `SolarPeriod.setPrices()` gives day rows their value in € at the price that applied that day: what wasn't bought, what was fed in and what was spent, summed into weeks, months and years. Prices come from the entities' long-term statistics or their recorded history.

## [0.4.0] - 2026-11-08

### Changed

- Plans are worked out about a fifth faster, with the same results.

## [0.3.0] - 2026-11-08

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
