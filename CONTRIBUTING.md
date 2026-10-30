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
  - `plan.py`: the three answers.
- `coordinator.py`: reads the states, re-reads the house's hourly use from the recorder once an hour, and recomputes the plan when the battery or the forecast changes.
- On top: the entities, the `simulate` action, the WebSocket command and the config flow.

Keep it this size. A new input or model earns its place only by fixing a miss the current answers show. Tests state what the integration does (a cloudier day fills the battery later), not how.
