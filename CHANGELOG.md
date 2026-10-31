# Changelog

## Unreleased

### Added

- When the battery fills, in the expected and the cloudy case, and its level at sunset.
- Whether it lasts the night, by replaying the house's recent nights from the recorder: the share of nights covered, and the reserve time and spare energy on a typical and a heavy night.
- The best window for heavy loads, and free power when a full battery exports.
- The `simulate` action for an extra load, and a WebSocket command with the whole plan.
- Setup prefilled from EF-PowerOcean-TcpModbus and Solcast.
- Charge needed for the night, solar surplus until sunset, and until when free power lasts.
- Prices: a fixed electricity price and feed-in tariff, with an optional price entity for dynamic tariffs; grid cost until sunrise and the saving from using the surplus. Prices never change the plan.
- Nights like tonight weigh more, once replaying recent evenings shows that predicts better than weighting all nights equally; the night prediction error sensor reports how well it does.
