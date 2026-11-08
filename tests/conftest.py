"""Fixtures shared by the Home Assistant tests."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from datetime import datetime

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.recorder import statistics
from homeassistant.components.recorder.db_schema import StatisticsBase
from homeassistant.core import HomeAssistant
from sqlalchemy.orm import Session

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
def bulk_statistics_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """Import statistics without a query per row.

    Home Assistant checks each imported row for an existing one with a query of
    its own, which also flushes the row before it: about half a millisecond a
    row, and most of the time these tests took. Here one query per import reads
    the rows already there; what is inserted and updated stays the same.
    """

    def existing(
        session: Session,
        table: type[StatisticsBase],
        metadata_id: int,
        start: datetime,
    ) -> int | None:
        key = ("existing_statistics", table, metadata_id)
        rows: dict[float, int] | None = session.info.get(key)
        if rows is None:
            query = session.query(table.start_ts, table.id).filter(
                table.metadata_id == metadata_id
            )
            rows = session.info[key] = {row.start_ts: row.id for row in query}
        return rows.get(start.timestamp())

    monkeypatch.setattr(statistics, "_statistics_exists", existing)
