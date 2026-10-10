"""Tests for the pending-updates and last-run sensors."""
from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.auto_updater.const import (
    CONF_AUTO_BACKUP,
    CONF_COOLDOWN_DAYS,
    CONF_IGNORED_HACS,
)

from .helpers import Calls, add_update, run, setup_entry

PENDING = "sensor.auto_updater_pending_updates"
LAST_RUN = "sensor.auto_updater_last_run"


async def test_pending_updates_sensor(hass: HomeAssistant, calls: Calls) -> None:
    card = add_update(hass, "mushroom_update", "hacs", model="plugin", installed="1.0", latest="2.0")
    ignored = add_update(hass, "card_mod_update", "hacs", model="plugin")
    await setup_entry(hass, **{CONF_COOLDOWN_DAYS: 3, CONF_IGNORED_HACS: [ignored]})

    state = hass.states.get(PENDING)
    assert state.state == "2"
    updates = {u["entity_id"]: u for u in state.attributes["updates"]}
    assert updates[card]["from"] == "1.0"
    assert updates[card]["to"] == "2.0"
    assert updates[card]["installable_from"] is not None  # waiting period running
    assert updates[ignored]["ignored"] is True


async def test_last_run_sensor(hass: HomeAssistant, calls: Calls) -> None:
    add_update(hass, "mushroom_update", "hacs", model="plugin", installed="1.0", latest="2.0")
    entry = await setup_entry(hass)
    assert hass.states.get(LAST_RUN).state == "unknown"

    await run(entry)
    await hass.async_block_till_done()
    state = hass.states.get(LAST_RUN)
    assert state.state == "success"
    assert state.attributes["updated"] == ["mushroom_update: 1.0 → 2.0"]
    assert state.attributes["finished"] is not None


async def test_last_run_sensor_backup_failed(hass: HomeAssistant, calls: Calls) -> None:
    add_update(hass, "mushroom_update", "hacs", model="plugin")
    calls.backup_error = HomeAssistantError("no agent")
    entry = await setup_entry(hass, **{CONF_AUTO_BACKUP: True})
    await run(entry)
    await hass.async_block_till_done()
    assert hass.states.get(LAST_RUN).state == "backup_failed"


async def test_sensors_removed_on_unload(hass: HomeAssistant, calls: Calls) -> None:
    entry = await setup_entry(hass)
    assert hass.states.get(PENDING) is not None
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(PENDING).state == "unavailable"
