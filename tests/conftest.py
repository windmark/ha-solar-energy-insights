"""Fixtures shared by the Home Assistant tests."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant

from tests.common import DAY, configure_home, local

# The harness logs every SQL statement at INFO. The recorder runs thousands of
# them per test and no test reads them, but each is still captured and formatted.
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


@pytest.fixture
async def home(
    recorder_mock: object,  # noqa: ARG001 - must be set up before hass
    enable_custom_integrations: None,  # noqa: ARG001
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
) -> AsyncGenerator[HomeAssistant]:
    """Home Assistant with the recorder, in Augsburg, on a June morning."""
    await configure_home(hass)
    freezer.move_to(local(DAY, 9))
    yield hass
    # Let jobs that read the recorder finish before it shuts down.
    await hass.async_block_till_done(wait_background_tasks=True)


@pytest.fixture(autouse=True)
def _bulk_statistics_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    """Home Assistant checks each imported row for an existing one with its own
    query, which also flushes the row before it. Answer from one query per
    statistic and import instead; the rows and their outcome are the same."""
    from homeassistant.components.recorder import statistics  # noqa: PLC0415

    def exists(session, table, metadata_id, start):  # type: ignore[no-untyped-def]
        key = ("existing_statistics", table, metadata_id)
        if (known := session.info.get(key)) is None:
            known = session.info[key] = dict(
                session.query(table.start_ts, table.id).filter(
                    table.metadata_id == metadata_id
                )
            )
        return known.get(start.timestamp())

    monkeypatch.setattr(statistics, "_statistics_exists", exists)
