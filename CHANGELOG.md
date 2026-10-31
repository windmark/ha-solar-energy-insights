# Changelog

## Unreleased

### Added

- numpy: nights, battery levels and hidden loads are simulated in batches. The plan takes 7 ms instead of 21; calibration 25 ms instead of 444.
- Up to 60 recent evenings are scored instead of 14.
- More ways of weighting nights: the same kind of day, similar outdoor temperature, similar presence at home (the last two when configured). Each is used only if it predicts left-out evenings clearly better, and the simplest of near-equal options wins.
- The heavy night is calibrated from where real nights fell among the replayed ones.
- A small store keeps Solcast's morning forecast, the plan's own answers and the weighting in use. Full-at error and live night scores come from it.
- An optional solar energy counter enables a per-hour Solcast correction for the roof, used once it beats raw Solcast on left-out days.

### Added

- Night outlook: lasts, borderline, or needs the grid.
- A hidden load (such as the inverter's standby) is learned from recent evenings, kept only if it predicts left-out evenings better, and used in every simulation.

### Changed

- The night prediction error is measured on evenings that could have gone either way, and left out of any fitting.
- The weighting in use changes only for a clear gain.
- Prices fall back to `input_number.electricity_price` and `input_number.feed_in_tariff` until the options are saved.

### Fixed

- Battery readings below the floor (a glitch, or the battery asleep at its reserve) are corrected and counted, instead of scoring as nights that ran out.
- Diagnostics replay the plan shown, not one made from readings taken at download.

### Added

- Diagnostics carry a replay snapshot of everything the plan was made from: battery, forecast, hourly use and scored evenings. A download can be replayed offline as a test.

### Fixed

- During the day, the night answers now start at sunset. Reaching the reserve this morning, before the sun takes over, no longer counts as a failed night.
- A heavy night can no longer show more spare energy than a typical one. Nights are ranked by how they end, not by their load including today.
- Charge needed is for sunset during the day, instead of the floor because the sun will come.
- Free power no longer shows an empty "until" time (displayed as 01:00).

### Added

- When the battery fills, in the expected and the cloudy case, and its level at sunset.
- Whether it lasts the night, by replaying the house's recent nights from the recorder: the share of nights covered, and the reserve time and spare energy on a typical and a heavy night.
- The best window for heavy loads, and free power when a full battery exports.
- The `simulate` action for an extra load, and a WebSocket command with the whole plan.
- Setup prefilled from EF-PowerOcean-TcpModbus and Solcast.
- Charge needed for the night, solar surplus until sunset, and until when free power lasts.
- Prices: a fixed electricity price and feed-in tariff, with an optional price entity for dynamic tariffs; grid cost until sunrise and the saving from using the surplus. Prices never change the plan.
- Nights like tonight weigh more, once replaying recent evenings shows that predicts better than weighting all nights equally; the night prediction error sensor reports how well it does.
