# Solar Energy Insights

A Home Assistant integration that answers three questions about a home battery:

1. **Will it fill today?** And when, if the sun comes as forecast or if it's cloudier.
2. **Will it last the night?** And if not, when does it reach the reserve?
3. **When should heavy loads run?** The window with the most solar to spare.

It reads entities you already have and never talks to a device. With [EF-PowerOcean-TcpModbus](https://github.com/MaxGrmm/EF-PowerOcean-TcpModbus) and [Solcast](https://github.com/BJReplay/ha-solcast-solar) installed, setup fills itself in.

## How it answers

**The night is replayed, not modelled.** The battery as it is now is run, in half-hour steps, against each of the last 14 nights as your house actually used power in them, read from the recorder. The answer is how many of those nights it would have lasted:

> Lasts the night on 11 of the last 14 nights. On a heavy night, the reserve is reached around 04:10.

Cooking, the dishwasher and a cold night's heat pump are already in those nights, metered or not. There is nothing to classify, learn or keep: the recorder is the only store, and a restart loses nothing.

**The day** runs Solcast's expected and cloudy case against the house's typical use at each hour. Both runs account for the battery's power limit, round-trip losses and reserve.

## Installation

1. HACS → ⋮ → Custom repositories → add `https://github.com/windmark/ha-solar-energy-insights` as an Integration.
2. Install **Solar Energy Insights** and restart Home Assistant.
3. Settings → Devices & services → Add integration → **Solar Energy Insights**.

Requires Home Assistant 2025.12 or newer and the recorder.

## Setup

| Field                      | Entity                                                                                                  |
| -------------------------- | ------------------------------------------------------------------------------------------------------- |
| Battery state of charge    | %                                                                                                       |
| Battery capacity           | Wh or kWh; while it reports none, the last value is used                                                |
| Battery reserve (optional) | %, the reserve in force; for PowerOcean, Effective Battery Reserve                                      |
| House energy               | A counter of the house's use, daily or lifetime; its recorded hours are what the night is replayed from |
| Grid power (optional)      | To tell when a full battery is exporting                                                                |
| Solcast today and tomorrow | The forecast sensors, with `detailedForecast`                                                           |

**Options:** how many nights to replay (default 14), the lowest state of charge the inverter discharges to by itself (5 %), the battery's power limit (5 kW), round-trip efficiency (92 %), and which sign means importing.

## Entities

| Entity                      |                                                                                                                |
| --------------------------- | -------------------------------------------------------------------------------------------------------------- |
| Battery full at             | Expected; attributes: the cloudy case, and peak % in both                                                      |
| Battery at sunset           | %                                                                                                              |
| Nights covered              | % of recent nights the battery would last; attributes: nights, lasting                                         |
| Reserve reached at          | On a typical recent night; attribute: on a heavy one                                                           |
| Spare at sunrise            | kWh above the reserve on a typical night; attributes: a heavy night, grid kWh                                  |
| Best window for heavy loads | Its start; attributes: end, spare kWh                                                                          |
| Battery lasts the night     | On when a typical recent night lasts                                                                           |
| Free power                  | On when the battery is full and exporting; the `solar_energy_insights_free_power` event fires when it turns on |

The entities hold facts, not sentences: word them in your dashboard.

## Simulate

The plan with an extra load started now, such as a dryer:

```yaml
action: solar_energy_insights.simulate
data:
  extra_kwh: 2.5
  extra_hours: 2
response_variable: with_dryer
```

## For dashboard cards

```js
await hass.callWS({ type: "solar_energy_insights/plan" });
```

returns the whole plan: each replayed night, the typical and heavy one, and the battery curve until sunset.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md).
