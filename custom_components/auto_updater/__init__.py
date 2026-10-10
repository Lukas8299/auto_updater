"""Auto Updater – installs HACS and Supervisor add-on updates on a schedule."""
from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime
from typing import Any

import voluptuous as vol

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_AUTO_BACKUP,
    CONF_COOLDOWN_DAYS,
    CONF_IGNORED_ADDONS,
    CONF_IGNORED_HACS,
    CONF_NOTIFY_SERVICE,
    CONF_RESTART_AFTER_UPDATE,
    CONF_SCHEDULE_TIME,
    CONF_UPDATE_CORE,
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
from .coordinator import (
    STATUS_BACKUP_FAILED,
    STATUS_FAILED,
    STATUS_INTERRUPTED,
    STATUS_NO_UPDATES,
    AutoUpdaterCoordinator,
    RunOptions,
)

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS: list[Platform] = [Platform.SENSOR]

ATTR_IGNORE_WAITING_PERIOD = "ignore_waiting_period"
ATTR_ADDITIONAL_IGNORED = "additional_ignored"

_BASE_SERVICE_FIELDS = {
    vol.Optional(CONF_AUTO_BACKUP): cv.boolean,
    vol.Optional(ATTR_IGNORE_WAITING_PERIOD, default=False): cv.boolean,
    vol.Optional(ATTR_ADDITIONAL_IGNORED, default=[]): cv.entity_ids,
    vol.Optional(CONF_RESTART_AFTER_UPDATE): cv.boolean,
}
SERVICE_SCHEMAS = {
    SERVICE_UPDATE_ALL: vol.Schema(
        {**_BASE_SERVICE_FIELDS, vol.Optional(CONF_UPDATE_CORE): cv.boolean}
    ),
    SERVICE_UPDATE_ADDONS: vol.Schema(
        {**_BASE_SERVICE_FIELDS, vol.Optional(CONF_UPDATE_CORE): cv.boolean}
    ),
    SERVICE_UPDATE_HACS: vol.Schema(_BASE_SERVICE_FIELDS),
}


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the services once; they act on the configured entry."""

    def _coordinator() -> AutoUpdaterCoordinator:
        for entry in hass.config_entries.async_entries(DOMAIN):
            if entry.state is ConfigEntryState.LOADED:
                return entry.runtime_data
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="not_loaded"
        )

    async def _handle(call: ServiceCall) -> ServiceResponse:
        coordinator = _coordinator()
        # Start from the configured behaviour (ignore lists, waiting period,
        # backup, restart) and apply what the call overrides.
        options = RunOptions.from_config(coordinator.config)
        if call.service == SERVICE_UPDATE_HACS:
            options = replace(options, update_hacs=True, update_addons=False, update_core=False)
        elif call.service == SERVICE_UPDATE_ADDONS:
            options = replace(options, update_hacs=False, update_addons=True,
                              update_core=call.data.get(CONF_UPDATE_CORE, False))
        else:
            options = replace(options, update_hacs=True, update_addons=True,
                              update_core=call.data.get(CONF_UPDATE_CORE, False))
        if CONF_AUTO_BACKUP in call.data:
            options = replace(options, auto_backup=call.data[CONF_AUTO_BACKUP])
        if CONF_RESTART_AFTER_UPDATE in call.data:
            options = replace(options, restart_after_update=call.data[CONF_RESTART_AFTER_UPDATE])
        if call.data[ATTR_IGNORE_WAITING_PERIOD]:
            options = replace(options, cooldown_days=0)
        if call.data[ATTR_ADDITIONAL_IGNORED]:
            options = replace(options, ignored=options.ignored | set(call.data[ATTR_ADDITIONAL_IGNORED]))

        result = await coordinator.async_run(options, trigger=f"service:{call.service}")
        _send_notification(hass, coordinator.config, result)
        return result if call.return_response else None

    for service, schema in SERVICE_SCHEMAS.items():
        hass.services.async_register(
            DOMAIN, service, _handle, schema=schema,
            supports_response=SupportsResponse.OPTIONAL,
        )
    return True


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
    await coordinator.async_load()
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    hour, minute = _parse_schedule(config.get(CONF_SCHEDULE_TIME, DEFAULT_SCHEDULE_TIME))

    async def _scheduled_update(now: datetime) -> None:
        _LOGGER.info("Auto Updater: scheduled run at %s", now)
        result = await coordinator.async_run(RunOptions.from_config(config), trigger="schedule")
        _send_notification(hass, config, result)

    entry.async_on_unload(
        async_track_time_change(hass, _scheduled_update, hour=hour, minute=minute, second=0)
    )
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


# --------------------------------------------------------------------------- #
# Notification                                                                 #
# --------------------------------------------------------------------------- #

def _format_report(result: Mapping[str, Any]) -> str | None:
    """Markdown report of a run, or None if there is nothing worth reporting."""
    status = result.get("status")
    updated = result.get("updated", [])
    failed = result.get("failed", [])
    if status in (STATUS_NO_UPDATES, STATUS_INTERRUPTED):
        return None

    lines = ["**Auto Updater report**"]
    if status == STATUS_BACKUP_FAILED:
        lines.append("\nThe backup failed, so **no updates were installed**. "
                     "Check Settings → System → Backups.")
    elif not updated and not failed:
        if status != STATUS_FAILED or not result.get("message"):
            return None  # only skipped updates, nothing to tell
        lines.append(f"\nNot run: {result['message']}.")
    if updated:
        lines.append(f"\nUpdated ({len(updated)}):")
        lines.extend(f"- {u['title']}: {u.get('from')} → {u.get('to')}" for u in updated)
    if failed:
        lines.append(f"\nFailed ({len(failed)}):")
        lines.extend(f"- {f['title']}: {f.get('error')}" for f in failed)
    waiting = [s for s in result.get("skipped", []) if s.get("reason") == "waiting"]
    if waiting:
        lines.append(f"\nWaiting for the waiting period ({len(waiting)}): "
                     + ", ".join(s["title"] for s in waiting))
    if result.get("restart"):
        lines.append("\nHome Assistant is restarting to load updated integrations.")
    return "\n".join(lines)


def _send_notification(hass: HomeAssistant, config: Mapping[str, Any],
                       result: Mapping[str, Any]) -> None:
    """Persistent notification and, if configured, a notify service call."""
    message = _format_report(result)
    if message is None:
        return

    persistent_notification.async_create(
        hass, message, title="Auto Updater", notification_id="auto_updater_result"
    )
    notify_service: str = config.get(CONF_NOTIFY_SERVICE, "")
    domain, _, service_name = notify_service.partition(".")
    if domain and service_name and hass.services.has_service(domain, service_name):
        hass.async_create_task(
            hass.services.async_call(
                domain, service_name, {"title": "Auto Updater", "message": message}
            )
        )
    elif notify_service:
        _LOGGER.warning("Notify service %s does not exist", notify_service)
