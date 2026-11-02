"""Repairs: a better reserve sensor, offered under Settings, with a one-click fix."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.repairs import RepairsFlow
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

if TYPE_CHECKING:
    # Only for the type checker: Home Assistant 2025.12 does not have it yet.
    from homeassistant.components.repairs import RepairsFlowResult

from .const import CONF_BATTERY_RESERVE, DOMAIN

POWEROCEAN = "ef_powerocean_tcpmodbus"
ISSUE = "better_reserve"


def _better_reserve(hass: HomeAssistant, entry: ConfigEntry) -> str | None:
    """PowerOcean's effective reserve, when the plain limit is configured.

    The effective reserve is the higher of the app's backup reserve and the
    battery's own limit; the plain limit can read 0 while the app keeps more.
    """
    current = entry.data.get(CONF_BATTERY_RESERVE)
    registry = er.async_get(hass)
    configured = registry.async_get(current) if current else None
    if configured is None or configured.platform != POWEROCEAN:
        return None
    if configured.unique_id.endswith("_battery_reserve_effective"):
        return None
    # The same PowerOcean installation: the same config entry.
    for candidate in registry.entities.values():
        if (
            candidate.platform == POWEROCEAN
            and candidate.config_entry_id == configured.config_entry_id
            and candidate.unique_id.endswith("_battery_reserve_effective")
        ):
            return candidate.entity_id
    return None


def check_reserve(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Offer the better reserve sensor as a repair, or withdraw the offer."""
    better = _better_reserve(hass, entry)
    issue_id = f"{ISSUE}_{entry.entry_id}"
    if better is None:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE,
        translation_placeholders={
            "current": str(entry.data.get(CONF_BATTERY_RESERVE)),
            "better": better,
        },
        data={"entry_id": entry.entry_id, "better": better},
    )


class BetterReserveFlow(RepairsFlow):
    """Switch the reserve to the effective one, in one step."""

    def __init__(self, entry_id: str, better: str) -> None:
        self.entry_id = entry_id
        self.better = better

    async def async_step_init(
        self, _user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        if user_input is not None:
            entry = self.hass.config_entries.async_get_entry(self.entry_id)
            if entry is not None:
                self.hass.config_entries.async_update_entry(
                    entry, data={**entry.data, CONF_BATTERY_RESERVE: self.better}
                )
                await self.hass.config_entries.async_reload(entry.entry_id)
            return self.async_create_entry(data={})
        return self.async_show_form(
            step_id="confirm", description_placeholders={"better": self.better}
        )


async def async_create_fix_flow(
    _hass: HomeAssistant, _issue_id: str, data: dict[str, Any] | None
) -> RepairsFlow:
    assert data is not None
    return BetterReserveFlow(str(data["entry_id"]), str(data["better"]))
