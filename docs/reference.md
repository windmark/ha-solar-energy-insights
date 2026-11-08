# Reference

## Setup

| Field                      | Entity                                         |
| -------------------------- | ---------------------------------------------- |
| Battery state of charge    | %                                              |
| Battery capacity           | Wh or kWh                                      |
| Battery reserve            | %; for PowerOcean, _Effective Battery Reserve_ |
| Charge limit               | %; for PowerOcean, _Charge Limit SOC_          |
| House energy               | A counter of the house's use                   |
| Grid power                 | To tell when a full battery exports            |
| Solcast today and tomorrow | The forecast sensors                           |

Options: prices, a solar energy counter, string sizes and roofs, nights to
replay, battery limits, and how long a changed answer is shown.

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
| Summary                                                                                                        | The dashboard's rows as attributes                                                                                         |
| Typical day                                                                                                    | The house's typical day in kWh; attribute `house`: the mean for each hour of the week, Monday 0:00 first, over five weeks  |
| Sun against the forecast                                                                                       | With achievements: the sun over the last 30 days against each morning's forecast, in %; attributes: each day, today so far |
| Battery size                                                                                                   | With achievements: right size, sometimes too small or too small, from the full days over 60 days that ran out at night     |
| Morning forecast                                                                                               | Today's forecast as it stood in the morning                                                                                |
| Forecast checkpoints                                                                                           | Days scored; attributes: frozen forecasts and scores                                                                       |
| PV*n* energy today, power per kWp, yield today                                                                 | Per string                                                                                                                 |
| Night prediction error, Full-at error, Solcast correction gain, Days of history, Grid share during load spikes | Diagnostics                                                                                                                |

## Actions

```yaml
action: solar_energy_insights.simulate
data:
  extra_kwh: 2.5
  extra_hours: 2
response_variable: with_dryer
```

The plan with an extra load started now.

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
