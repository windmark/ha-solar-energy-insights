# Changelog

## Unreleased

### Added

- The battery's answers: when it fills (expected and cloudy), its level at sunset, the best window for heavy loads, and free power with until when.
- The night, by replaying the house's recent nights from the recorder: nights covered, reserve time and spare energy on a typical and a heavy night, a three-way outlook (lasts, borderline, needs the grid), and the charge needed for the night (at sunset in the day, now at night; 100 % with the shortfall when even full is not enough).
- Nights like tonight weigh more: by similar afternoons, the same kind of day, and optionally outdoor temperature or presence. Each way is used only if it predicts left-out evenings clearly better, and the simplest of near-equal options wins.
- Learning from up to 60 recent evenings: a hidden load the house counter never sees (such as the inverter's standby), and how heavy a heavy night is. Both are kept only when they predict evenings they were not fitted on better.
- Battery readings below the floor (a glitch, or a battery asleep at its reserve) are cleaned and counted.
- A small store keeps Solcast's morning forecast, the plan's own answers and the weighting in use. It gives a live full-at error, live night scores, and, with a solar energy counter, a per-hour Solcast correction for the roof once it beats raw Solcast on left-out days.
- Prices: a fixed price and feed-in tariff (falling back to the dashboard's helpers until saved), with an optional price entity for dynamic tariffs; grid cost until sunrise and the saving from using the surplus. Prices never change the plan.
- The `simulate` action for an extra load; WebSocket commands `plan` (with each replayed night's battery curve) and `backtest`; diagnostics that replay the exact moment offline.
- numpy batches every replay: a plan takes about 7 ms, calibration about 25 ms.
- Setup prefilled from EF-PowerOcean-TcpModbus and Solcast.

### Fixed

- During the day, the night is measured from sunset: this morning's reserve is not a failed night, and a heavy night never ends better than a typical one.
- The day's usual load and the fill-in for missing hours come from the recent days replayed, not the whole history read for scoring.
- A day the recorder mostly missed is skipped, not replayed as a night.
- A 0 % reading from a battery asleep at its reserve is taken as the reserve.
- Every saved morning is learned from, however far apart, and large solar hours are kept.
- A failed read from the recorder keeps the answers already made instead of making every entity unavailable.
- Numeric answers keep long-term statistics.
