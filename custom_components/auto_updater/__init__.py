"""Auto Updater – automatically installs Supervisor Addon and HACS updates."""
from __future__ import annotations

import logging
from datetime import datetime

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_time_change

from .const import (
    CONF_AUTO_BACKUP,
    CONF_IGNORED_ADDONS,
    CONF_IGNORED_HACS,
    CONF_NOTIFY_SERVICE,
    CONF_SCHEDULE_TIME,
    CONF_UPDATE_ADDONS,
    CONF_UPDATE_CORE,
    CONF_UPDATE_HACS,
    DOMAIN,
    SERVICE_UPDATE_ADDONS,
    SERVICE_UPDATE_ALL,
    SERVICE_UPDATE_HACS,
)
from .coordinator import AutoUpdaterCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Auto Updater from a config entry."""
    config = dict(entry.options) or dict(entry.data)

    coordinator = AutoUpdaterCoordinator(hass, config)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    # ------------------------------------------------------------------ #
    # Schedule automatic updates                                           #
    # ------------------------------------------------------------------ #
    schedule_str: str = config.get(CONF_SCHEDULE_TIME, "03:00")
    try:
        hour, minute = (int(p) for p in schedule_str.split(":")[:2])
    except (ValueError, AttributeError):
        _LOGGER.warning(
            "Invalid schedule_time '%s', defaulting to 03:00", schedule_str
        )
        hour, minute = 3, 0

    async def _scheduled_update(now: datetime) -> None:
        _LOGGER.info("Auto Updater: running scheduled update at %s", now)
        result = await coordinator.async_run_updates_from_config()
        _send_notification(hass, config, result)

    entry.async_on_unload(
        async_track_time_change(hass, _scheduled_update, hour=hour, minute=minute, second=0)
    )

    # ------------------------------------------------------------------ #
    # Services                                                             #
    # ------------------------------------------------------------------ #
    update_schema = vol.Schema(
        {
            vol.Optional("ignored_addons", default=[]): vol.All(
                cv.ensure_list, [cv.string]
            ),
            vol.Optional("ignored_hacs", default=[]): vol.All(
                cv.ensure_list, [cv.string]
            ),
            vol.Optional(CONF_AUTO_BACKUP, default=False): cv.boolean,
            vol.Optional(CONF_UPDATE_CORE, default=False): cv.boolean,
        }
    )

    async def _svc_update_all(call: ServiceCall) -> None:
        result = await coordinator.async_run_updates(
            update_addons=True,
            update_hacs=True,
            update_core=call.data.get(CONF_UPDATE_CORE, False),
            ignored_addons=call.data.get("ignored_addons", []),
            ignored_hacs=call.data.get("ignored_hacs", []),
            auto_backup=call.data.get(CONF_AUTO_BACKUP, False),
        )
        _send_notification(hass, config, result)

    async def _svc_update_addons(call: ServiceCall) -> None:
        result = await coordinator.async_run_updates(
            update_addons=True,
            update_hacs=False,
            update_core=call.data.get(CONF_UPDATE_CORE, False),
            ignored_addons=call.data.get("ignored_addons", []),
            ignored_hacs=[],
            auto_backup=call.data.get(CONF_AUTO_BACKUP, False),
        )
        _send_notification(hass, config, result)

    async def _svc_update_hacs(call: ServiceCall) -> None:
        result = await coordinator.async_run_updates(
            update_addons=False,
            update_hacs=True,
            update_core=False,
            ignored_addons=[],
            ignored_hacs=call.data.get("ignored_hacs", []),
            auto_backup=call.data.get(CONF_AUTO_BACKUP, False),
        )
        _send_notification(hass, config, result)

    hass.services.async_register(DOMAIN, SERVICE_UPDATE_ALL, _svc_update_all, update_schema)
    hass.services.async_register(DOMAIN, SERVICE_UPDATE_ADDONS, _svc_update_addons, update_schema)
    hass.services.async_register(DOMAIN, SERVICE_UPDATE_HACS, _svc_update_hacs, update_schema)

    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    hass.data[DOMAIN].pop(entry.entry_id, None)

    for service in (SERVICE_UPDATE_ALL, SERVICE_UPDATE_ADDONS, SERVICE_UPDATE_HACS):
        hass.services.async_remove(DOMAIN, service)

    return True


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


# --------------------------------------------------------------------------- #
# Notification helper                                                          #
# --------------------------------------------------------------------------- #

def _send_notification(hass: HomeAssistant, config: dict, result: dict) -> None:
    """Fire a persistent notification and optionally call a custom notify service."""
    updated: list[str] = result.get("updated", [])
    failed: list[str] = result.get("failed", [])

    if not updated and not failed:
        return  # Nothing to report

    lines = ["**Auto Updater report**\n"]
    if updated:
        lines.append(f"Updated ({len(updated)}):")
        lines.extend(f"  - {e}" for e in updated)
    if failed:
        lines.append(f"\nFailed ({len(failed)}):")
        lines.extend(f"  - {e}" for e in failed)

    message = "\n".join(lines)

    hass.async_create_task(
        hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "title": "Auto Updater",
                "message": message,
                "notification_id": "auto_updater_result",
            },
        )
    )

    notify_service: str = config.get(CONF_NOTIFY_SERVICE, "")
    if notify_service:
        domain, _, service_name = notify_service.partition(".")
        if domain and service_name:
            hass.async_create_task(
                hass.services.async_call(
                    domain,
                    service_name,
                    {"title": "Auto Updater", "message": message},
                )
            )
