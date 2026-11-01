# Changelog

## Unreleased

### Added

- Tonight's budget, on "Night outlook": what the battery can deliver from sunset to its reserve, how much of it the evening may use (leaving your usual after-midnight use), your usual evening, the house's use since sunset, live, and the recent nights that ran short with their evening's use. A borderline night says why and what keeps it covered.

### Removed

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
