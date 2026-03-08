"""Config flow for Auto Updater."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_AUTO_BACKUP,
    CONF_IGNORED_ADDONS,
    CONF_IGNORED_HACS,
    CONF_NOTIFY_SERVICE,
    CONF_SCHEDULE_TIME,
    CONF_UPDATE_ADDONS,
    CONF_UPDATE_CORE,
    CONF_UPDATE_HACS,
    DEFAULT_AUTO_BACKUP,
    DEFAULT_NOTIFY_SERVICE,
    DEFAULT_SCHEDULE_TIME,
    DEFAULT_UPDATE_ADDONS,
    DEFAULT_UPDATE_CORE,
    DEFAULT_UPDATE_HACS,
    DOMAIN,
)


def _options_schema(defaults: dict) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_UPDATE_ADDONS,
                default=defaults.get(CONF_UPDATE_ADDONS, DEFAULT_UPDATE_ADDONS),
            ): bool,
            vol.Required(
                CONF_UPDATE_HACS,
                default=defaults.get(CONF_UPDATE_HACS, DEFAULT_UPDATE_HACS),
            ): bool,
            vol.Required(
                CONF_SCHEDULE_TIME,
                default=defaults.get(CONF_SCHEDULE_TIME, DEFAULT_SCHEDULE_TIME),
            ): str,
            vol.Optional(
                CONF_NOTIFY_SERVICE,
                default=defaults.get(CONF_NOTIFY_SERVICE, DEFAULT_NOTIFY_SERVICE),
            ): str,
            vol.Required(
                CONF_AUTO_BACKUP,
                default=defaults.get(CONF_AUTO_BACKUP, DEFAULT_AUTO_BACKUP),
            ): bool,
            vol.Required(
                CONF_UPDATE_CORE,
                default=defaults.get(CONF_UPDATE_CORE, DEFAULT_UPDATE_CORE),
            ): bool,
            vol.Optional(
                CONF_IGNORED_ADDONS,
                default=defaults.get(CONF_IGNORED_ADDONS, ""),
            ): str,
            vol.Optional(
                CONF_IGNORED_HACS,
                default=defaults.get(CONF_IGNORED_HACS, ""),
            ): str,
        }
    )


def _parse_user_input(user_input: dict) -> dict:
    """Normalize list fields that come in as comma-separated strings from the UI."""
    data = dict(user_input)
    for key in (CONF_IGNORED_ADDONS, CONF_IGNORED_HACS):
        raw = data.get(key, "")
        if isinstance(raw, str):
            data[key] = [s.strip() for s in raw.split(",") if s.strip()]
    return data


class AutoUpdaterConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Auto Updater."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        errors: dict[str, str] = {}

        if user_input is not None:
            if not _valid_time(user_input.get(CONF_SCHEDULE_TIME, "")):
                errors[CONF_SCHEDULE_TIME] = "invalid_time"
            else:
                data = _parse_user_input(user_input)
                return self.async_create_entry(title="Auto Updater", data=data)

        return self.async_show_form(
            step_id="user",
            data_schema=_options_schema({}),
            errors=errors,
            description_placeholders={
                "schedule_hint": "HH:MM (24-hour, e.g. 03:00)",
                "ignore_hint": "Comma-separated slugs to skip, e.g. 'mosquitto,node_red'",
            },
        )

    @callback
    def async_get_options_flow(self) -> OptionsFlow:
        return AutoUpdaterOptionsFlow()


class AutoUpdaterOptionsFlow(OptionsFlow):
    """Handle options (reconfiguration) for Auto Updater."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        current = dict(self.config_entry.options or self.config_entry.data)
        # Stringify list fields for the form
        for key in (CONF_IGNORED_ADDONS, CONF_IGNORED_HACS):
            if isinstance(current.get(key), list):
                current[key] = ", ".join(current[key])

        if user_input is not None:
            if not _valid_time(user_input.get(CONF_SCHEDULE_TIME, "")):
                errors[CONF_SCHEDULE_TIME] = "invalid_time"
            else:
                data = _parse_user_input(user_input)
                return self.async_create_entry(title="Auto Updater", data=data)

        return self.async_show_form(
            step_id="init",
            data_schema=_options_schema(current),
            errors=errors,
        )


def _valid_time(value: str) -> bool:
    """Validate HH:MM format."""
    try:
        parts = value.split(":")
        if len(parts) < 2:
            return False
        h, m = int(parts[0]), int(parts[1])
        return 0 <= h <= 23 and 0 <= m <= 59
    except (ValueError, AttributeError):
        return False
