# Cards

Every card has its settings in the dashboard's visual editor; the YAML below is
the same thing written out. All settings are optional.

## Now

```yaml
type: custom:solar-energy-insights-now
rows: [battery, tonight, next_day, loads, surplus, last_night] # which, in this order
header: true # the time and today's own power
tap: jump # or none
```

## Until sunrise

```yaml
type: custom:solar-energy-insights-until-sunrise
columns: [typical, heavy]
footnotes: true
appliances:
  - name: Dishwasher
    icon: mdi:dishwasher
    kwh: 1.0 # typical per run
    hours: 2.5
    energy_per_run: sensor.dishwasher_energy_per_run # optional, learned
running: # optional
  entity: sensor.washing_machine_operation_state
  state: run
  name: Washing machine
```

Without `appliances`, the cards show the appliances added under the
integration.

## Best time to run

```yaml
type: custom:solar-energy-insights-best-time
chart: true
appliances: [] # as above
```

## Can I trust the forecast?

```yaml
type: custom:solar-energy-insights-forecast-trust
checkpoints: ["18:00", "21:00", "06:00", "12:00"]
footnotes: true
```

## Achievements

```yaml
type: custom:solar-energy-insights-level
quest: true
recap: true       # yesterday and last week

type: custom:solar-energy-insights-badges
sections: [within_reach, any_time, later, complete]
```

`custom:solar-energy-insights-streaks` and `-records` have no settings.

## Colours

The cards follow your theme. See [Card colours](reference.md#card-colours) to
change the status colours.

## Building your own

`sensor.solar_energy_insights_summary` holds what the Now card shows: `rows`,
each with a `label`, `main` (the headline), `detail` (up to two clauses), a
`tone` (`good`, `warn`, `bad`, `neutral`) and an `icon`. A Markdown card:

```yaml
type: markdown
content: >
  {% for row in state_attr('sensor.solar_energy_insights_summary', 'rows') %}
  **{{ row.label }}:** {{ row.main }}{% if row.detail %} · {{ row.detail | join(' · ') }}{% endif %}

  {% endfor %}
```

The other sensors are in the [reference](reference.md). For everything the
cards draw, the `solar_energy_insights/plan` WebSocket command returns the full
plan.

### History by week, month and year

The shipped script also provides `window.SolarPeriod`, for button-card and
apexcharts cards on a history page. Create two helpers: a dropdown
`input_select.solar_history_period` with `Day`, `Week`, `Month` and `Year`, and a
date `input_datetime.solar_day`. `SolarPeriod.view(hass, states, this)` returns the
selected period's totals with the previous period and the same period last year;
`SolarPeriod.series(hass, key)` and `SolarPeriod.profile(hass, key)` feed
apexcharts' `data_generator`. Pass a typical year, kWh per month, with
`SolarPeriod.setTypical([...])` to compare a year with it. To value days in €, pass the
price entities (ct/kWh) with `SolarPeriod.setPrices({ buy, sell, buyDefault,
sellDefault })`: each day then carries `v` (€ saved), `va` (not bought), `ve` (fed
in) and `vb` (spent) at that day's price. Prices come from the entity's long-term
statistics, else its recorded history, which the recorder keeps only for its purge
window.
