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

| Entity                                                                                                         | Meaning                                                                                                                    |
| -------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| Night outlook                                                                                                  | `lasts`, `borderline` or `grid`; attributes include the evening budget and the short nights                                |
| Battery lasts the night                                                                                        | On when a typical night lasts                                                                                              |
| Charge needed for the night                                                                                    | The level from which even a heavy night lasts                                                                              |
| Reserve reached at                                                                                             | When the grid takes over on a typical night                                                                                |
| Spare at sunrise                                                                                               | kWh left on a typical night                                                                                                |
| Nights covered                                                                                                 | % of recent nights that would last from now                                                                                |
| Battery full at                                                                                                | Expected time; attributes: the cloudy case                                                                                 |
| Battery at sunset                                                                                              | %                                                                                                                          |
| Best window for heavy loads                                                                                    | Start; attributes: end, spare kWh                                                                                          |
| Solar surplus until sunset                                                                                     | kWh the battery can't take                                                                                                 |
| Free power                                                                                                     | On while the battery is full and exporting                                                                                 |
| Grid cost until sunrise                                                                                        | At your price                                                                                                              |
| Saving from using the surplus                                                                                  | Using it at home instead of exporting                                                                                      |
| Clipping until sunset                                                                                          | With a feed-in cap: kWh above it, with the battery running by itself; attributes: from, until                              |
| Curtailed today                                                                                                | With a feed-in cap, grid power and solar power: an estimate of the solar held back today                                   |
| Summary                                                                                                        | The dashboard's rows as attributes                                                                                         |
| Typical day                                                                                                    | The house's typical day in kWh; attribute `house`: the mean for each hour of the week, Monday 0:00 first, over five weeks  |
| Sun against the forecast                                                                                       | With achievements: the sun over the last 30 days against each morning's forecast, in %; attributes: each day, today so far |
| Battery size                                                                                                   | With achievements: right size, sometimes too small or too small, from the full days over 60 days that ran out at night     |
| Morning forecast                                                                                               | Today's forecast as it stood in the morning                                                                                |
| Forecast checkpoints                                                                                           | Days scored; attributes: frozen forecasts and scores                                                                       |
| PV*n* energy today, power per kWp, yield today                                                                 | Per string                                                                                                                 |
| Night prediction error, Full-at error, Solcast correction gain, Days of history, Grid share during load spikes | Diagnostics                                                                                                                |

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
  heat_loss_w_per_k: 150 # optional: a heat pump; needs the outdoor temperature
  heating_limit_c: 12.5
  indoor_c: 20
  hot_water_kwh_per_day: 8
  design_flow_c: 45 # flow temperature on the coldest day
  design_outdoor_c: -12 # the coldest day the heating is designed for
  heat_pump_kw: 7 # optional: above it, a heating rod adds the rest
  rated_cop: 4.5 # optional: the datasheet's COP at A2/W35
  hot_water_at_noon: true # heat the hot water in the sunniest hours
  gas_price: 0.11 # optional: compare with gas
  boiler_efficiency: 0.9
  gas_standing_charge: 150
  fill_year: true # fill the days the recorder lacks
response_variable: year
```

The recorded hours of the last year (from the solar energy counter and the
house's use) replayed through the battery as it is and with the change: per
year, what is bought, exported and used, self-sufficiency, nights without the
grid, battery cycles, the saving, the battery's payback, the heat pump's heat,
electricity, seasonal performance factor and share from the roof, the same by
month, and with a gas price, what the same heat would cost from gas.

**A whole year, even from part of one.** A replay of half a year, scaled up,
takes the summer for the winter: too much sun, and for a heat pump far too
little heat. So each day the recorder lacks borrows a recorded day with a
similar sun, by k-nearest-neighbour resampling (Rajagopalan & Lall, 1999), its
solar scaled by the sunlight that reaches the top of the atmosphere on each date
(Duffie & Beckman, eq. 1.10.3). The outdoor thermometer is read for the whole
year on its own, as it is often older than the solar and the house's meter:
its readings stand in the borrowed hours, and among days with a similar sun a
day it measured cold borrows a cold one. Where it has no readings either, the
borrowed day's temperatures are laid on the date's typical temperature, from a
sinusoid fitted to the readings there are. `year` in the answer says how many
days were recorded, how many filled and how much of the filled time kept the
thermometer's readings (`measured_temperature_pct`). On a test year near
Augsburg, filling 168 days from the other 197 gives the whole year's heat
exactly with the thermometer's readings and within about 10% without them;
scaling them gave less than half.

The heat follows the thermometer: one that reads warm, in the sun or against
the house, counts fewer degree-hours than the weather service. A heat loss
worked out from a gas bill in the card makes up for it, as long as the house
after a change is scaled from that figure.

**The heat pump.** The heat is the degree-hour method of VDI 4710: on each day
whose mean outdoor temperature is below the heating limit, the heat loss times
indoor less outdoor. Use the _effective_ heat loss, as measured from a heating
bill (a year's useful heat over the year's G20/15 degree-hours, which the answer
gives as `degree_hours_g20_15`): the sun's and the household's free heat are
already in it. A heat-load calculation's figure (DIN EN 12831, heat load ÷
(20 °C − the design temperature)) leaves them out and is about half as high
again. The heating limit is about 15 °C for an older house and 12–13 °C once
well insulated. Each hour's heat is divided by the COP of an air-source heat
pump in the field: the quadratic fit of Ruhnau et al. (2019) on the lift from
the outdoor air to the water, times their 0.85 for real systems, scaled to a
datasheet's A2/W35 if given. The water's temperature follows a heating curve
for radiators (EN 442, exponent 1.3) up to the design flow temperature; hot
water is heated to 50 °C. It answers "about how much", not the installer's
sizing.

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
