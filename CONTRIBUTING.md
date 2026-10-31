# Contributing

## Setup

The project and CI use Python 3.14. [`uv`](https://docs.astral.sh/uv/) is recommended:

```shell
uv venv --python 3.14
uv pip install -r requirements-development.txt
uv run pre-commit install
```

## Checks

```shell
uv run pytest
uv run mypy
uv run pre-commit run --all-files
```

The Home Assistant tests run against a real Home Assistant and recorder from [pytest-homeassistant-custom-component](https://github.com/MatthewFlamm/pytest-homeassistant-custom-component); `tests/common.py` records past days into it.

## Architecture

- `core/` holds the logic as plain Python, with no Home Assistant imports:
  - `battery.py`: steps the battery through solar and load;
  - `forecast.py`: reads Solcast;
  - `nights.py`: lays recent days over the hours ahead;
  - `plan.py`: the answers;
  - `backtest.py`: replays recent evenings to score the night answer and choose the weighting.
- `coordinator.py`: reads the states, re-reads the house's hourly use from the recorder once an hour, and recomputes the plan when the battery or the forecast changes.
- On top: the entities, the `simulate` action, the WebSocket command and the config flow.

Besides tests of single situations, `tests/core/test_invariants.py` checks properties that must hold across a grid of seasons, skies, houses, battery levels and times of day, and `tests/core/test_relations.py` checks how plans relate (more charge never hurts, nothing jumps at sunset or midnight). A new answer should get both kinds of test.

### Replaying a real moment

When the dashboard shows something that looks wrong, download diagnostics (Settings → Devices & services → Solar Energy Insights → ⋮ → Download diagnostics). The file holds everything the plan was made from: the battery, Solcast's forecast, the house's hourly use and the scored evenings.

1. Save it in `tests/fixtures/` under a name that says what looked wrong. From then on, `tests/test_replays.py` replays it and runs every check on it.
2. To pin down the fix, add a test that loads it with `tests/replay.py` and asserts what the answer should have been. The test fails first, then passes once the fix is in.

The file holds your own energy data and entity ids, and nothing else.

Keep it this size. A new input or model earns its place only by fixing a miss the current answers show. Tests state what the integration does (a cloudier day fills the battery later), not how.
