# Changelog

## Unreleased

### Added

- Battery plan against Solcast: full time, peak, level at sunset, reserve time, the night's load, grid use and spare energy, the best window for appliances, and when the sun covers the house.
- House load learned per hour of the week and per kind of evening (cooking or quiet), without metered appliances.
- Appliances as sub-entries, learning energy and length per run from their plugs.
- Solcast correction per month and hour, applied only once it beats the raw forecast.
- Day records from long-term statistics, with score, forecasts at each lead time, the night after and the battery sizing verdict.
- Battery upgrade value from replaying recorded hours.
- Actions `simulate`, `value_upgrade` and `relearn`; WebSocket commands `plan`, `days` and `upgrade`; diagnostics.
- Setup prefilled from EF-PowerOcean-TcpModbus and Solcast.
