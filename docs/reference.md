# Reference

## Setup

| Field                      | Entity                                                                   |
| -------------------------- | ------------------------------------------------------------------------ |
| Battery state of charge    | %                                                                        |
| Battery capacity           | Wh or kWh                                                                |
| Battery reserve            | %; for PowerOcean, _Effective Battery Reserve_                           |
| Charge limit               | %; for PowerOcean, _Battery Charge Limit_; the highest over the last day |
| House energy               | A counter of the house's use                                             |
| Grid power                 | To tell when a full battery exports                                      |
| Solcast today and tomorrow | The forecast sensors                                                     |

Options: prices, a solar energy counter, an outdoor thermometer, string sizes
and roofs, nights to replay, battery limits, the feed-in cap, and how long a
changed answer is shown.

## Entities

| Entity                                                                                                         | Meaning                                                                                       |
| -------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| Night outlook                                                                                                  | `lasts`, `borderline` or `grid`; attributes include the evening budget and the short nights   |
| Battery lasts the night                                                                                        | On when a typical night lasts                                                                 |
| Charge needed for the night                                                                                    | The level from which even a heavy night lasts                                                 |
| Reserve reached at                                                                                             | When the grid takes over on a typical night                                                   |
| Spare at sunrise                                                                                               | kWh left on a typical night                                                                   |
| Nights covered                                                                                                 | % of recent nights that would last from now                                                   |
| Battery full at                                                                                                | Expected time; attributes: the cloudy case                                                    |
| Battery at sunset                                                                                              | %                                                                                             |
| Best window for heavy loads                                                                                    | Start; attributes: end, spare kWh                                                             |
| Solar surplus until sunset                                                                                     | kWh the battery can't take                                                                    |
| Free power                                                                                                     | On while the battery is full and exporting                                                    |
| Grid cost until sunrise                                                                                        | At your price                                                                                 |
| Saving from using the surplus                                                                                  | Using it at home instead of exporting                                                         |
| Clipping until sunset                                                                                          | With a feed-in cap: kWh above it, with the battery running by itself; attributes: from, until |
| Curtailed today                                                                                                | With a feed-in cap, grid power and solar power: an estimate of the solar held back today      |
| Summary                                                                                                        | The dashboard's rows as attributes                                                            |
| Morning forecast                                                                                               | Today's forecast as it stood in the morning                                                   |
| Forecast checkpoints                                                                                           | Days scored; attributes: frozen forecasts and scores                                          |
| PV*n* energy today, power per kWp, yield today                                                                 | Per string                                                                                    |
| Night prediction error, Full-at error, Solcast correction gain, Days of history, Grid share during load spikes | Diagnostics                                                                                   |

## Appliances

Added under the integration with **Add appliance**: a name, an icon, the
typical energy and duration of a run, and optionally a sensor that learns the
energy per run (with the last durations as its `hours` attribute). Each is its
own device with:

| Entity             | Meaning                                                                                                    |
| ------------------ | ---------------------------------------------------------------------------------------------------------- |
| Best start         | The start until sunset that takes the most from free sun; attributes: kWh, hours, free and now shares in % |
| Good time to start | On while starting now takes at least half the run from free sun, about as much as the best start           |

Free sun is sun to spare once the battery is full, which would otherwise be
exported. The blueprint `start_appliance.yaml` starts an appliance once it is
a good time, and optionally once it is ready to be started remotely.

## Actions

```yaml
action: solar_energy_insights.simulate
data:
  extra_kwh: 2.5
  extra_hours: 2
response_variable: with_dryer
```

The plan with an extra load started now.

```yaml
action: solar_energy_insights.scenario
data:
  extra_battery_kwh: 5
  battery_price: 2500 # optional, for the payback
  heat_loss_w_per_k: 180 # optional: a heat pump; needs the outdoor temperature
  heating_limit_c: 15
  indoor_c: 20
  hot_water_kwh_per_day: 6
response_variable: year
```

The recorded hours of the last year (from the solar energy counter and the
house's use) replayed through the battery as it is and with the change: per
year, what is bought, exported and used, self-sufficiency, nights without the
grid, the saving, the payback, and how much of a heat pump's electricity the
roof covers. The heat pump is a simple model: the heat loss below the heating
limit, divided by a coefficient of performance that falls in the cold, plus hot
water.

## WebSocket

`solar_energy_insights/plan`, `/backtest`, `/achievements` and `/config`
return the full plan, the scored evenings, the trophy room and this home's
settings.

## Card colours

The cards follow your theme's text, divider and card colours. Status colours
adapt to light and dark themes; set any of these in a theme to override them:

| Variable      | Used for                       |
| ------------- | ------------------------------ |
| `--sei-good`  | Lasts, full, earned            |
| `--sei-warn`  | Borderline, peaks below full   |
| `--sei-bad`   | The grid, short nights         |
| `--sei-info`  | Typical night, any-time badges |
| `--sei-track` | Bar backgrounds                |
