"""Config flow for Auto Updater."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector
from homeassistant.helpers.hassio import is_hassio

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
    DEFAULT_AUTO_BACKUP,
    DEFAULT_COOLDOWN_DAYS,
    DEFAULT_NOTIFY_SERVICE,
    DEFAULT_RESTART_AFTER_UPDATE,
    DEFAULT_SCHEDULE_TIME,
    DEFAULT_UPDATE_ADDONS,
    DEFAULT_UPDATE_CORE,
    DEFAULT_UPDATE_HACS,
    DOMAIN,
    MAX_COOLDOWN_DAYS,
    PLATFORM_HACS,
    PLATFORM_HASSIO,
)

# Options that only make sense with a Supervisor (Home Assistant OS or
# Supervised). On Container/Core installs they are hidden and stored as False.
SUPERVISOR_ONLY_OPTIONS = (CONF_UPDATE_ADDONS, CONF_UPDATE_CORE, CONF_IGNORED_ADDONS)


def _options_schema(hass: HomeAssistant, defaults: dict[str, Any]) -> vol.Schema:
    """Build the form shared by the initial setup and the options flow."""

    def default(key: str, fallback: Any) -> Any:
        return defaults.get(key, fallback)

    fields: dict[Any, Any] = {}
    supervisor = is_hassio(hass)

    if supervisor:
        fields[
            vol.Required(CONF_UPDATE_ADDONS, default=default(CONF_UPDATE_ADDONS, DEFAULT_UPDATE_ADDONS))
        ] = selector.BooleanSelector()
    fields[
        vol.Required(CONF_UPDATE_HACS, default=default(CONF_UPDATE_HACS, DEFAULT_UPDATE_HACS))
    ] = selector.BooleanSelector()
    fields[
        vol.Required(CONF_SCHEDULE_TIME, default=default(CONF_SCHEDULE_TIME, DEFAULT_SCHEDULE_TIME))
    ] = selector.TimeSelector()
    fields[
        vol.Required(CONF_COOLDOWN_DAYS, default=default(CONF_COOLDOWN_DAYS, DEFAULT_COOLDOWN_DAYS))
    ] = selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=0,
            max=MAX_COOLDOWN_DAYS,
            step=1,
            unit_of_measurement="d",
            mode=selector.NumberSelectorMode.BOX,
        )
    )
    fields[
        vol.Required(
            CONF_RESTART_AFTER_UPDATE,
            default=default(CONF_RESTART_AFTER_UPDATE, DEFAULT_RESTART_AFTER_UPDATE),
        )
    ] = selector.BooleanSelector()
    fields[
        vol.Required(CONF_AUTO_BACKUP, default=default(CONF_AUTO_BACKUP, DEFAULT_AUTO_BACKUP))
    ] = selector.BooleanSelector()
    if supervisor:
        fields[
            vol.Required(CONF_UPDATE_CORE, default=default(CONF_UPDATE_CORE, DEFAULT_UPDATE_CORE))
        ] = selector.BooleanSelector()
    fields[
        vol.Optional(CONF_NOTIFY_SERVICE, default=default(CONF_NOTIFY_SERVICE, DEFAULT_NOTIFY_SERVICE))
    ] = selector.TextSelector()
    if supervisor:
        fields[
            vol.Optional(CONF_IGNORED_ADDONS, default=default(CONF_IGNORED_ADDONS, []))
        ] = selector.EntitySelector(
            selector.EntitySelectorConfig(
                domain="update", integration=PLATFORM_HASSIO, multiple=True
            )
        )
    fields[
        vol.Optional(CONF_IGNORED_HACS, default=default(CONF_IGNORED_HACS, []))
    ] = selector.EntitySelector(
        selector.EntitySelectorConfig(domain="update", integration=PLATFORM_HACS, multiple=True)
    )
    return vol.Schema(fields)


def _validate(hass: HomeAssistant, user_input: dict[str, Any]) -> dict[str, str]:
    """Return form errors keyed by field."""
    errors: dict[str, str] = {}
    notify = (user_input.get(CONF_NOTIFY_SERVICE) or "").strip()
    if notify:
        domain, _, service = notify.partition(".")
        if not domain or not service:
            errors[CONF_NOTIFY_SERVICE] = "invalid_notify_service"
        elif not hass.services.has_service(domain, service):
            errors[CONF_NOTIFY_SERVICE] = "unknown_notify_service"
    return errors


def _normalize(hass: HomeAssistant, user_input: dict[str, Any]) -> dict[str, Any]:
    """Fill hidden Supervisor-only options and normalise types."""
    data = dict(user_input)
    if not is_hassio(hass):
        data[CONF_UPDATE_ADDONS] = False
        data[CONF_UPDATE_CORE] = False
        data[CONF_IGNORED_ADDONS] = []
    data[CONF_COOLDOWN_DAYS] = int(data.get(CONF_COOLDOWN_DAYS, DEFAULT_COOLDOWN_DAYS))
    data[CONF_NOTIFY_SERVICE] = (data.get(CONF_NOTIFY_SERVICE) or "").strip()
    data.setdefault(CONF_IGNORED_ADDONS, [])
    data.setdefault(CONF_IGNORED_HACS, [])
    return data


class AutoUpdaterConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Auto Updater."""

    VERSION = CONFIG_ENTRY_VERSION

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Initial setup."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate(self.hass, user_input)
            if not errors:
                return self.async_create_entry(
                    title="Auto Updater", data=_normalize(self.hass, user_input)
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_options_schema(self.hass, user_input or {}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return AutoUpdaterOptionsFlow()


class AutoUpdaterOptionsFlow(OptionsFlow):
    """Change the settings of an existing entry."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and store the options."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate(self.hass, user_input)
            if not errors:
                return self.async_create_entry(data=_normalize(self.hass, user_input))

        current = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(
            step_id="init",
            data_schema=_options_schema(self.hass, user_input or current),
            errors=errors,
        )
