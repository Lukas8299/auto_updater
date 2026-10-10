"""Tests for the config flow, options flow and entry migration."""
from __future__ import annotations

from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
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

BASE_INPUT = {
    CONF_UPDATE_HACS: True,
    CONF_SCHEDULE_TIME: "04:30:00",
    CONF_COOLDOWN_DAYS: 3,
    CONF_RESTART_AFTER_UPDATE: True,
    CONF_AUTO_BACKUP: False,
    CONF_NOTIFY_SERVICE: "",
    CONF_IGNORED_HACS: [],
}


async def test_user_flow_without_supervisor(hass: HomeAssistant) -> None:
    """Without Supervisor the add-on and core options are hidden and stored off."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    shown = {str(k) for k in result["data_schema"].schema}
    assert CONF_UPDATE_ADDONS not in shown
    assert CONF_UPDATE_CORE not in shown
    assert CONF_IGNORED_ADDONS not in shown

    result = await hass.config_entries.flow.async_configure(result["flow_id"], BASE_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    data = result["data"]
    assert data[CONF_UPDATE_ADDONS] is False
    assert data[CONF_UPDATE_CORE] is False
    assert data[CONF_IGNORED_ADDONS] == []
    assert data[CONF_COOLDOWN_DAYS] == 3
    assert data[CONF_SCHEDULE_TIME] == "04:30:00"


async def test_user_flow_with_supervisor(hass: HomeAssistant) -> None:
    """With Supervisor all options are offered and stored as given."""
    with patch(
        "custom_components.auto_updater.config_flow.is_hassio", return_value=True
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        shown = {str(k) for k in result["data_schema"].schema}
        assert {CONF_UPDATE_ADDONS, CONF_UPDATE_CORE, CONF_IGNORED_ADDONS} <= shown

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                **BASE_INPUT,
                CONF_UPDATE_ADDONS: True,
                CONF_UPDATE_CORE: True,
                CONF_IGNORED_ADDONS: ["update.mosquitto_broker_update"],
            },
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_UPDATE_ADDONS] is True
    assert result["data"][CONF_UPDATE_CORE] is True
    assert result["data"][CONF_IGNORED_ADDONS] == ["update.mosquitto_broker_update"]


async def test_notify_service_validation(hass: HomeAssistant) -> None:
    """A malformed or unknown notify service is rejected."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**BASE_INPUT, CONF_NOTIFY_SERVICE: "nodot"}
    )
    assert result["errors"] == {CONF_NOTIFY_SERVICE: "invalid_notify_service"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**BASE_INPUT, CONF_NOTIFY_SERVICE: "notify.does_not_exist"}
    )
    assert result["errors"] == {CONF_NOTIFY_SERVICE: "unknown_notify_service"}

    hass.services.async_register("notify", "phone", lambda call: None)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**BASE_INPUT, CONF_NOTIFY_SERVICE: " notify.phone "}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_NOTIFY_SERVICE] == "notify.phone"


async def test_single_instance(hass: HomeAssistant) -> None:
    """Only one entry is allowed."""
    MockConfigEntry(domain=DOMAIN, data=BASE_INPUT, version=2).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_options_flow(hass: HomeAssistant) -> None:
    """The options flow stores new values and keeps the entry loadable."""
    entry = MockConfigEntry(domain=DOMAIN, data=BASE_INPUT, version=2)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**BASE_INPUT, CONF_COOLDOWN_DAYS: 7}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options[CONF_COOLDOWN_DAYS] == 7
    assert entry.state is config_entries.ConfigEntryState.LOADED


async def test_migration_v1_to_v2(hass: HomeAssistant) -> None:
    """v1 substring patterns become the entity IDs they matched."""
    registry = er.async_get(hass)
    registry.async_get_or_create("update", "hacs", "1", suggested_object_id="mushroom_update")
    registry.async_get_or_create("update", "hacs", "2", suggested_object_id="card_mod_update")
    registry.async_get_or_create(
        "update", "hassio", "3", suggested_object_id="mosquitto_broker_update"
    )
    registry.async_get_or_create(
        "update", "esphome", "4", suggested_object_id="mushroom_firmware_update"
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        data={
            CONF_UPDATE_ADDONS: True,
            CONF_UPDATE_HACS: True,
            CONF_SCHEDULE_TIME: "03:00",
            CONF_AUTO_BACKUP: False,
            CONF_UPDATE_CORE: False,
            CONF_NOTIFY_SERVICE: "",
            CONF_IGNORED_ADDONS: ["Mosquitto"],
            CONF_IGNORED_HACS: ["mushroom"],
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.version == 2
    assert entry.data[CONF_IGNORED_HACS] == ["update.mushroom_update"]
    assert entry.data[CONF_IGNORED_ADDONS] == ["update.mosquitto_broker_update"]
    assert entry.data[CONF_SCHEDULE_TIME] == "03:00:00"
    assert entry.data[CONF_COOLDOWN_DAYS] == 3
    assert entry.data[CONF_RESTART_AFTER_UPDATE] is True


async def test_downgrade_is_refused(hass: HomeAssistant) -> None:
    """An entry from a newer release is not loaded."""
    entry = MockConfigEntry(domain=DOMAIN, data=BASE_INPUT, version=99)
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is config_entries.ConfigEntryState.MIGRATION_ERROR
