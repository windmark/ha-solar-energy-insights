"""Repairs offered under Settings, each with a one-click fix: a better reserve
sensor, and appliances found among the energy counters."""

from __future__ import annotations

from types import MappingProxyType
from typing import Any

from homeassistant.components.repairs import RepairsFlow, RepairsFlowResult
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from .appliance_discovery import Found, found_appliances, issue_id
from .const import (
    CONF_APPLIANCE_ENERGY,
    CONF_BATTERY_RESERVE,
    CONF_ENERGY_PER_RUN,
    CONF_REJECTED_APPLIANCES,
    DOMAIN,
    SUBENTRY_APPLIANCE,
)
from .discovery import POWEROCEAN

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


APPLIANCE_ISSUE = "appliance_found"


def check_appliances(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Suggest each appliance counter found, and withdraw the ones no longer
    found or already in use. A suggestion the user ignored stays ignored."""
    found = {issue_id(f): f for f in found_appliances(hass, entry)}
    registry = ir.async_get(hass)
    for (domain, existing), _issue in list(registry.issues.items()):
        if (
            domain == DOMAIN
            and existing.startswith(APPLIANCE_ISSUE)
            and existing not in found
        ):
            ir.async_delete_issue(hass, DOMAIN, existing)
    for key, f in found.items():
        ir.async_create_issue(
            hass,
            DOMAIN,
            key,
            is_fixable=True,
            is_persistent=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=("appliance_counter" if f.existing else APPLIANCE_ISSUE),
            translation_placeholders={"name": f.name, "energy": f.energy},
            data={"entry_id": entry.entry_id, "energy": f.energy},
        )


class ApplianceFoundFlow(RepairsFlow):
    """Add the appliance found, or give the one of its name its counter."""

    def __init__(self, entry_id: str, energy: str) -> None:
        self.entry_id = entry_id
        self.energy = energy

    def _found(self) -> tuple[ConfigEntry | None, Found | None]:
        entry = self.hass.config_entries.async_get_entry(self.entry_id)
        if entry is None:
            return None, None
        return entry, next(
            (f for f in found_appliances(self.hass, entry) if f.energy == self.energy),
            None,
        )

    async def async_step_init(
        self, _user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        entry, found = self._found()
        if entry is None or found is None:
            return self.async_abort(reason="gone")
        return self.async_show_menu(
            step_id="init",
            menu_options=["reject", "submit"],
            description_placeholders={"name": found.name, "energy": found.energy},
        )

    async def async_step_reject(
        self, _user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        """Not this one: remembered, so it is not suggested again."""
        entry = self.hass.config_entries.async_get_entry(self.entry_id)
        if entry is not None:
            rejected = list(entry.options.get(CONF_REJECTED_APPLIANCES) or [])
            if self.energy not in rejected:
                rejected.append(self.energy)
            self.hass.config_entries.async_update_entry(
                entry, options={**entry.options, CONF_REJECTED_APPLIANCES: rejected}
            )
            check_appliances(self.hass, entry)
        return self.async_create_entry(data={})

    async def async_step_submit(
        self, _user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        return await self.async_step_confirm({})

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> RepairsFlowResult:
        entry, found = self._found()
        if entry is None or found is None:
            return self.async_abort(reason="gone")
        if user_input is not None:
            subentry = entry.subentries.get(found.existing) if found.existing else None
            if subentry is not None:
                self.hass.config_entries.async_update_subentry(
                    entry,
                    subentry,
                    data={
                        **subentry.data,
                        CONF_APPLIANCE_ENERGY: found.energy,
                        **(
                            {CONF_ENERGY_PER_RUN: found.energy_per_run}
                            if found.energy_per_run
                            and not subentry.data.get(CONF_ENERGY_PER_RUN)
                            else {}
                        ),
                    },
                )
            else:
                self.hass.config_entries.async_add_subentry(
                    entry,
                    ConfigSubentry(
                        data=MappingProxyType(found.data()),
                        subentry_type=SUBENTRY_APPLIANCE,
                        title=found.name,
                        unique_id=None,
                    ),
                )
            check_appliances(self.hass, entry)
            return self.async_create_entry(data={})
        return self.async_show_form(
            step_id="confirm",
            description_placeholders={"name": found.name, "energy": found.energy},
        )


async def async_create_fix_flow(
    _hass: HomeAssistant, issue_id_: str, data: dict[str, Any] | None
) -> RepairsFlow:
    assert data is not None
    if issue_id_.startswith(APPLIANCE_ISSUE):
        return ApplianceFoundFlow(str(data["entry_id"]), str(data["energy"]))
    return BetterReserveFlow(str(data["entry_id"]), str(data["better"]))
