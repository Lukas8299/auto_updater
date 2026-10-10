"""Helpers shared by the tests."""
from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.auto_updater.const import (
    CONF_AUTO_BACKUP,
    CONF_COOLDOWN_DAYS,
    CONF_IGNORED_ADDONS,
    CONF_IGNORED_HACS,
    CONF_NOTIFY_SERVICE,
    CONF_RESTART_AFTER_UPDATE,
    CONF_SCHEDULE_TIME,
    CONF_UPDATE_ADDONS,
    CONF_UPDATE_CORE,
    CONF_UPDATE_HACS,
    DOMAIN,
)
from custom_components.auto_updater.coordinator import RunOptions

CONFIG: dict[str, Any] = {
    CONF_UPDATE_ADDONS: True,
    CONF_UPDATE_HACS: True,
    CONF_UPDATE_CORE: False,
    CONF_SCHEDULE_TIME: "03:00:00",
    CONF_COOLDOWN_DAYS: 0,
    CONF_RESTART_AFTER_UPDATE: True,
    CONF_AUTO_BACKUP: False,
    CONF_NOTIFY_SERVICE: "",
    CONF_IGNORED_ADDONS: [],
    CONF_IGNORED_HACS: [],
}


class Calls:
    """Records calls to the services the integration uses."""

    def __init__(self) -> None:
        self.install: list[str] = []
        self.restart = 0
        self.backup = 0
        self.backup_error: Exception | None = None
        self.install_gate: asyncio.Event | None = None


def add_update(
    hass: HomeAssistant,
    object_id: str,
    platform: str,
    *,
    model: str | None = None,
    installed: str = "1.0.0",
    latest: str = "1.1.0",
) -> str:
    """Register a pending update entity with state 'on'."""
    device_id = None
    if model is not None:
        source = MockConfigEntry(domain=platform)
        source.add_to_hass(hass)
        device_id = dr.async_get(hass).async_get_or_create(
            config_entry_id=source.entry_id,
            identifiers={(platform, object_id)},
            model=model,
        ).id
    entry = er.async_get(hass).async_get_or_create(
        "update", platform, object_id, suggested_object_id=object_id, device_id=device_id
    )
    hass.states.async_set(
        entry.entity_id,
        "on",
        {"installed_version": installed, "latest_version": latest, "friendly_name": object_id},
    )
    return entry.entity_id


async def setup_entry(hass: HomeAssistant, **overrides: Any) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data={**CONFIG, **overrides}, version=2)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def run(entry: MockConfigEntry, **overrides: Any) -> dict[str, Any]:
    coordinator = entry.runtime_data
    options = RunOptions.from_config({**coordinator.config, **overrides})
    return await coordinator.async_run(options, trigger="test")
