# Contributing

## Development setup

The project and CI use Python 3.14. [`uv`](https://docs.astral.sh/uv/) is recommended:

```shell
uv venv --python 3.14
uv pip install -r requirements-development.txt
uv run pre-commit install
```

## Run the checks

```shell
uv run pytest
uv run mypy
uv run pre-commit run --all-files
```

The Home Assistant tests run against a real Home Assistant and recorder from [pytest-homeassistant-custom-component](https://github.com/MatthewFlamm/pytest-homeassistant-custom-component). History is recorded into the test recorder with `tests/history_helpers.py`.

## Architecture

- `core/` — the models, plain Python on plain data: battery simulation, forecast and its correction, load model, appliances, planner, day records, upgrade replay. No Home Assistant imports.
- `coordinator.py` — the live plan from current states, appliance runs and cooking as they happen.
- `jobs.py` — learning from the recorder: hourly, daily, after sunrise and at fixed forecast times.
- `history.py` — reading statistics; `store.py` — what is learned, in `.storage`.
- Platforms, the config flow, actions and the WebSocket API on top.

Keep behaviour in the layer that owns it, and test it there: models in `tests/core`, Home Assistant behaviour in `tests/`. Tests state what the integration does (a cloudier day fills the battery less), not how it does it.
