"""Auto Updater – automatically installs Supervisor Addon and HACS updates."""
from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_time_change

from .const import (
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
    CONFIG_ENTRY_VERSION,
    DEFAULT_COOLDOWN_DAYS,
    DEFAULT_RESTART_AFTER_UPDATE,
    DEFAULT_SCHEDULE_TIME,
    DOMAIN,
    PLATFORM_HACS,
    PLATFORM_HASSIO,
    SERVICE_UPDATE_ADDONS,
    SERVICE_UPDATE_ALL,
    SERVICE_UPDATE_HACS,
)
from .coordinator import AutoUpdaterCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old config entries to the current format."""
    if entry.version > CONFIG_ENTRY_VERSION:
        # Downgrade from a newer release: we cannot read its format.
        return False

    if entry.version == 1:
        registry = er.async_get(hass)

        def _patterns_to_entities(raw: Any, platform: str) -> list[str]:
            # v1 matched every pattern as a substring of the entity ID.
            # Resolve that once to the entities it matches today, so the
            # effective ignore list does not change for existing users.
            if isinstance(raw, str):
                raw = raw.split(",")
            patterns = [p.strip().lower() for p in raw or [] if p and p.strip()]
            if not patterns:
                return []
            return sorted(
                reg.entity_id
                for reg in registry.entities.values()
                if reg.domain == "update"
                and reg.platform == platform
                and any(p in reg.entity_id.lower() for p in patterns)
            )

        def _migrate(cfg: Mapping[str, Any]) -> dict[str, Any]:
            new = dict(cfg)
            new[CONF_IGNORED_ADDONS] = _patterns_to_entities(
                cfg.get(CONF_IGNORED_ADDONS), PLATFORM_HASSIO
            )
            new[CONF_IGNORED_HACS] = _patterns_to_entities(
                cfg.get(CONF_IGNORED_HACS), PLATFORM_HACS
            )
            schedule = cfg.get(CONF_SCHEDULE_TIME)
            if isinstance(schedule, str) and schedule.count(":") == 1:
                new[CONF_SCHEDULE_TIME] = f"{schedule}:00"
            new.setdefault(CONF_COOLDOWN_DAYS, DEFAULT_COOLDOWN_DAYS)
            new.setdefault(CONF_RESTART_AFTER_UPDATE, DEFAULT_RESTART_AFTER_UPDATE)
            return new

        new_data = _migrate(entry.data)
        new_options = _migrate(entry.options) if entry.options else {}
        _LOGGER.info(
            "Migrated Auto Updater entry to version 2; ignored add-ons: %s, "
            "ignored HACS: %s",
            (new_options or new_data)[CONF_IGNORED_ADDONS],
            (new_options or new_data)[CONF_IGNORED_HACS],
        )
        hass.config_entries.async_update_entry(
            entry, data=new_data, options=new_options, version=2
        )

    return True


def _parse_schedule(value: Any) -> tuple[int, int]:
    """Parse 'HH:MM' or 'HH:MM:SS'; fall back to 03:00."""
    try:
        hour, minute = (int(p) for p in str(value).split(":")[:2])
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour, minute
    except ValueError:
        pass
    _LOGGER.warning("Invalid schedule_time '%s', defaulting to 03:00", value)
    return 3, 0


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Auto Updater from a config entry."""
    config = {**entry.data, **entry.options}

    coordinator = AutoUpdaterCoordinator(hass, config)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    # ------------------------------------------------------------------ #
    # Schedule automatic updates                                           #
    # ------------------------------------------------------------------ #
    hour, minute = _parse_schedule(config.get(CONF_SCHEDULE_TIME, DEFAULT_SCHEDULE_TIME))

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
