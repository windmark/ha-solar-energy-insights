# Solar Energy Insights

A Home Assistant integration that answers three questions about a home battery:

1. **Will it fill today?** And when, if the sun comes as forecast or if it's cloudier.
2. **Will it last the night?** And if not, when does it reach the reserve?
3. **When should heavy loads run?** The window with the most solar to spare.

It reads entities you already have and never talks to a device. With [EF-PowerOcean-TcpModbus](https://github.com/MaxGrmm/EF-PowerOcean-TcpModbus) and [Solcast](https://github.com/BJReplay/ha-solcast-solar) installed, setup fills itself in.

## How it answers

**The night is replayed, not modelled.** The battery as it is now is run, in half-hour steps, against each of the last 14 nights as your house actually used power in them, read from the recorder. The answer is how many of those nights it would have lasted:

> Lasts the night on 11 of the last 14 nights. On a heavy night, the reserve is reached around 04:10.

Cooking, the dishwasher and a cold night's heat pump are already in those nights, metered or not, so nothing has to be classified. A day the recorder mostly missed is skipped rather than replayed, and the day's usual load comes from the same recent days, not from months back.

**Nights like tonight count more, once that has proved better.** Today's last six hours, including the hour in progress, are compared with the same hours on each past day, and similar days weigh more. After a big dinner, nights that followed big dinners dominate; if the evening turns heavy at 22:00, the weights follow within the hour. This weighting is only used when it beats weighting every night equally on your own recent evenings.

**The score needs no stored predictions.** Each hour, the integration replays each of the recent evenings as it would have at 21:00, using only the days before it, and compares the result with the lowest level the battery actually reached before sunrise. Three things keep that honest:

- **Contested evenings only.** An evening that starts nearly empty ends at the reserve whatever is predicted, so only evenings where the prediction or the battery said "lasted" are scored.
- **Clean history.** A battery can't go below its floor. A reading under it is either a glitch (the hour otherwise sat well above) or the battery asleep at its reserve; both are corrected and counted.
- **The hidden load.** The house counter doesn't see everything the battery feeds, such as the inverter's own standby. One constant is learned from the evenings and kept only if it predicts evenings it wasn't fitted on better. On the first real installation it was 50 W, and it took the predictions from 0.5 kWh optimistic to unbiased.

**The last 30 evenings** are scored: about a month, enough to learn from and recent enough to follow the season. Each is replayed once against every past night and every hidden-load candidate, as arrays (numpy). Ways of weighting the nights only change how much each counts, so they're tried on the same runs:

- equal;
- similar afternoons;
- the same kind of day (weekday or weekend);
- similar afternoons and the same kind of day;
- similar afternoons and similar outdoor temperature (if configured);
- similar afternoons and similar presence at home (if configured).

The one in use changes only for a clear gain (10 %), and among near-equal options the simplest wins. **How heavy "a heavy night" is** is calibrated too: where real nights fell among the replayed ones decides it, so it's exceeded about as often as intended.

**What the recorder can't give back is kept** in `.storage`: Solcast's forecast each morning, the plan's own answers (the night at 21:00, "full at" in the morning), and the weighting in use, so it survives a restart. The answers are scored against what happened. With a solar energy counter configured, the morning forecasts are compared with what the panels made, and a correction per clock hour is used once it beats raw Solcast on left-out days (after at least 14 days).

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
| Charge limit (optional)    | %, the level charging stops at if set below 100 %; for PowerOcean, Charge Limit SOC                     |
| House energy               | A counter of the house's use, daily or lifetime; its recorded hours are what the night is replayed from |
| Grid power (optional)      | To tell when a full battery is exporting                                                                |
| Solcast today and tomorrow | The forecast sensors, with `detailedForecast`                                                           |

**Options:**

- **Prices:** the electricity price and feed-in tariff, in cents per kWh. Until they're saved here, `input_number.electricity_price` and `input_number.feed_in_tariff` are used if you have them. For a dynamic tariff, an optional price entity overrides the price while it has a value.
- **Optional inputs:** an outdoor temperature sensor and a presence sensor (each with statistics) for telling nights apart, and a solar energy counter for learning a Solcast correction. Each is used only if it predicts better.
- **The night test:** how many nights to replay (default 14), and the lowest state of charge the inverter discharges to by itself (5 %).
- **The battery:** its charge limit when no entity gives one (100 %), its power limit (5 kW), round-trip efficiency (92 %), and which sign of grid power means importing.

Prices only turn kWh into money. They never change the plan: a price entity's current value applies to every hour ahead, and nothing is timed to cheap hours.

## Entities

| Entity                        |                                                                                                                                                                                                                                                                                                           |
| ----------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Battery full at               | Expected; attributes: the cloudy case, and peak % in both                                                                                                                                                                                                                                                 |
| Battery at sunset             | %                                                                                                                                                                                                                                                                                                         |
| Nights covered                | % of recent nights the battery would last, weighted; attributes: nights, lasting                                                                                                                                                                                                                          |
| Reserve reached at            | On a typical recent night; attribute: on a heavy one                                                                                                                                                                                                                                                      |
| Spare at sunrise              | kWh above the reserve on a typical night; attributes: a heavy night, grid kWh                                                                                                                                                                                                                             |
| Charge needed for the night   | The lowest level from which even a heavy night lasts: at sunset during the day (compare it with "Battery at sunset"), now at night; attributes: for when, a typical night                                                                                                                                 |
| Solar surplus until sunset    | kWh the battery can't take, for an EV, water heater or dryer; attributes: after full, while charging (power-limited), cloudy case                                                                                                                                                                         |
| Best window for heavy loads   | Its start; attributes: end, spare kWh                                                                                                                                                                                                                                                                     |
| Grid cost until sunrise       | The typical night's grid energy at the current price; attributes: a heavy night, the price, where it came from                                                                                                                                                                                            |
| Saving from using the surplus | What using the surplus at home saves instead of exporting it (price minus feed-in); attribute: what exporting it earns                                                                                                                                                                                    |
| Night outlook                 | Lasts (even a heavy night), borderline (only if it's not a heavy night), or needs the grid                                                                                                                                                                                                                |
| Night prediction error        | How far off "spare at sunrise" would have been, in kWh, on recent evenings that could have gone either way, and left out of any fitting; attributes: weighting in use, each weighting's error, hidden load, heavy-night share, bias, corrected battery readings, and how the plan's own 21:00 answers did |
| Full-at error                 | How far the morning's "full at" was from when the battery really got full, in minutes; attributes: days scored, days where full was wrong                                                                                                                                                                 |
| Solcast correction gain       | Diagnostic: how much a correction for your roof cuts Solcast's error on left-out days; attributes: in use, days learned from, the factor per hour                                                                                                                                                         |
| Days of history               | Diagnostic: days of house use the recorder holds                                                                                                                                                                                                                                                          |
| Battery lasts the night       | On when a typical recent night lasts                                                                                                                                                                                                                                                                      |
| Free power                    | On when the battery is full and exporting; attribute: until when; the `solar_energy_insights_free_power` event fires when it turns on                                                                                                                                                                     |

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

returns the whole plan: each replayed night with its weight and battery curve, the typical and heavy one, and the battery curve until sunset.

```js
await hass.callWS({ type: "solar_energy_insights/backtest" });
```

returns every scored evening (predicted and actual spare, whether it was contested), what was learned (weighting, hidden load, heavy share, each weighting's error), and how the plan's own answers did.

## Reporting something that looks wrong

Download diagnostics (Settings → Devices & services → Solar Energy Insights → ⋮ → Download diagnostics) at the moment it looks wrong, and attach the file with a screenshot. The file replays that exact moment as a test (see CONTRIBUTING.md). It holds your energy data and entity ids, nothing else.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md).
