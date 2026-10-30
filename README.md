# Solar Energy Insights

A Home Assistant integration that plans a home battery against the Solcast forecast and learns how your house uses power. It answers the questions a solar dashboard keeps asking — _when will the battery be full, will it last the night, when should the dishwasher run_ — once, on the server, as entities that automations can use.

It reads the entities you already have (battery, power and energy sensors, Solcast) and never talks to a device. With [EF-PowerOcean-TcpModbus](https://github.com/MaxGrmm/EF-PowerOcean-TcpModbus) and [Solcast](https://github.com/BJReplay/ha-solcast-solar) installed, setup fills itself in.

## What it does

- **Plans the battery** in 30-minute steps until the next sunset and sunrise: Solcast minus the learned house load, with the battery's power limit, round-trip losses and reserve floor.
- **Learns the house load** per hour of the week, and separately for evenings with and without cooking. Metered appliances are taken out and hours with an unmetered one running are skipped, so the model is the house alone.
- **Learns appliances** from their smart plugs: energy and length per run.
- **Corrects Solcast for your roof** per month and hour — but only switches the correction on once it has beaten the raw forecast on the days scored so far.
- **Keeps a record per day** from the recorder's statistics: energy totals, self-sufficiency, a score, when the battery got full, the forecasts at each lead time, and after sunrise the night that followed (grid use, and whether the battery was the right size).
- **Values a bigger battery** by replaying every recorded hour with extra capacity.

Learning reads completed periods from long-term statistics rather than sampling states at midnight, so a restart or a slow counter only delays it. What is learned is kept in `.storage`, not in state attributes.

## Installation

1. HACS → ⋮ → Custom repositories → add `https://github.com/windmark/ha-solar-energy-insights` as an Integration.
2. Install **Solar Energy Insights** and restart Home Assistant.
3. Settings → Devices & services → Add integration → **Solar Energy Insights**.

Requires Home Assistant 2025.12 or newer and the recorder.

## Setup

Three steps pick existing entities:

| Step              | Entities                                                                                                      |
| ----------------- | ------------------------------------------------------------------------------------------------------------- |
| Battery and power | State of charge, battery power, capacity (Wh or kWh), reserve (optional), solar, house and grid power         |
| Energy counters   | Solar, house, grid import, grid export, battery charged and discharged — daily or lifetime counters both work |
| Forecast          | Solcast today and tomorrow (with `detailedForecast`), and optionally day 3 onwards                            |

**Options:** electricity price and feed-in tariff (numbers, or entities that override them), the lowest state of charge the inverter discharges to by itself (default 5 %), the battery power limit, which sign means charging and importing, the cooking threshold, and the battery upgrade to value.

**Appliances** are added as sub-entries (⋮ → Add appliance): a plug's power sensor (and energy counter for exact kWh), or for an appliance without a meter a state entity such as Home Connect's operation state.

## Entities

| Entity                                                              | What it says                                                                                                    |
| ------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| Plan                                                                | One sentence for now, with the sub-line and up to four answer lines in attributes                               |
| Battery full at · Battery peak · Battery at sunset                  | Today's charging, or the next day's at night                                                                    |
| Reserve reached at · Load / Grid until sunrise · Spare at sunrise   | The night ahead, for the most likely evening                                                                    |
| Best appliance window · Sun covers the house at                     | When to run heavy loads; when solar takes over in the morning                                                   |
| Self-sufficiency today · Solar score today                          | The day so far (score: own power 40, solar kept 30, battery used 15, performance against the 06:00 forecast 15) |
| Forecast accuracy                                                   | Actual against the 06:00 forecast, 30 days; the miss at each lead time in attributes                            |
| Battery sizing                                                      | Last night's verdict: right size, ran out after a full day, or did not get full                                 |
| Battery upgrade value per year                                      | The configured upgrade, replayed; payback time in attributes                                                    |
| Battery lasts the night · Free power · On the grid · Cooked tonight | Binary sensors for automations                                                                                  |
| Per appliance: Energy per run · Run duration · Running              | Learned from the plug, or the configured default                                                                |
| Load model error · Forecast correction gain                         | Diagnostics: how well the models are doing                                                                      |

The event `solar_energy_insights_free_power` fires when the battery is full and power is being exported.

## Actions

- `solar_energy_insights.simulate` — runs the battery to sunrise or sunset with appliances started now, in the expected, cloudy or sunny case, and returns the result and the curve.
- `solar_energy_insights.value_upgrade` — values a list of added capacities from your recorded hours.
- `solar_energy_insights.relearn` — forgets the learned load and learns it again from statistics.

```yaml
action: solar_energy_insights.simulate
data:
  appliances: [Dishwasher, Dryer]
  until: sunrise
response_variable: night
```

## For dashboard cards

Cards ask the integration instead of querying the recorder themselves, so every device shows the same numbers:

```js
await hass.callWS({ type: "solar_energy_insights/plan" }); // the plan with its battery curve
await hass.callWS({ type: "solar_energy_insights/days", start: "2026-10-01" }); // day records
await hass.callWS({ type: "solar_energy_insights/upgrade", sizes: [5, 8, 16] });
```

Day records carry yes/no `flags` per day — self-sufficient, battery full, beat the forecast, grid-free night — as the raw material for streaks and badges.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md). The models live in `custom_components/solar_energy_insights/core/` as plain Python without Home Assistant imports, and are tested directly.
