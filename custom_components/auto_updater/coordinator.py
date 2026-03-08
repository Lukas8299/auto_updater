"""Data update coordinator for Auto Updater."""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import timedelta

import aiohttp

from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    DOMAIN,
    PLATFORM_HACS,
    PLATFORM_HASSIO,
    SUPERVISOR_API,
    SUPERVISOR_BACKUP_ENDPOINT,
    SYSTEM_UPDATE_ENTITIES,
)

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(hours=1)


class AutoUpdaterCoordinator(DataUpdateCoordinator):
    """Coordinator that checks for available updates and applies them on schedule."""

    def __init__(self, hass: HomeAssistant, config: dict) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )
        self._config = config

    # ------------------------------------------------------------------
    # DataUpdateCoordinator interface
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> dict:
        """Fetch current update state from the entity registry."""
        registry = er.async_get(self.hass)

        addon_entities = []
        hacs_entities = []

        for entry in registry.entities.values():
            if entry.domain != "update" or entry.disabled_by:
                continue
            # Only target Supervisor addons and HACS repositories.
            # Device firmware updates (zha, zwave_js, esphome, matter, etc.)
            # are intentionally ignored — they are never touched by this integration.
            if entry.platform == PLATFORM_HASSIO:
                # Exclude HA Core / Supervisor / OS — handled separately via
                # CONF_UPDATE_CORE so they are never mixed in with addon updates.
                if entry.entity_id not in SYSTEM_UPDATE_ENTITIES:
                    addon_entities.append(entry.entity_id)
            elif entry.platform == PLATFORM_HACS:
                hacs_entities.append(entry.entity_id)

        pending_addons = self._filter_pending(addon_entities)
        pending_hacs = self._filter_pending(hacs_entities)
        pending_system = self._filter_pending(list(SYSTEM_UPDATE_ENTITIES))

        return {
            "addon_entities": addon_entities,
            "hacs_entities": hacs_entities,
            "pending_addons": pending_addons,
            "pending_hacs": pending_hacs,
            "pending_system": pending_system,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _filter_pending(self, entity_ids: list[str]) -> list[str]:
        """Return entity IDs whose state is 'on' (update available)."""
        result = []
        for entity_id in entity_ids:
            state = self.hass.states.get(entity_id)
            if state and state.state == STATE_ON:
                result.append(entity_id)
        return result

    def _is_ignored(self, entity_id: str, ignored: list[str]) -> bool:
        """Check whether an entity_id or its slug appears in the ignore list."""
        entity_id_lower = entity_id.lower()
        for pattern in ignored:
            if pattern.lower() in entity_id_lower:
                return True
        return False

    # ------------------------------------------------------------------
    # Backup
    # ------------------------------------------------------------------

    async def async_create_backup(self) -> bool:
        """Request a full backup via the Supervisor API. Returns True on success."""
        token = os.environ.get("SUPERVISOR_TOKEN")
        if not token:
            _LOGGER.warning("SUPERVISOR_TOKEN not set – cannot create backup")
            return False

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        url = f"{SUPERVISOR_API}{SUPERVISOR_BACKUP_ENDPOINT}"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    headers=headers,
                    json={"name": "auto_updater_pre_update"},
                    timeout=aiohttp.ClientTimeout(total=300),
                ) as resp:
                    if resp.status == 200:
                        _LOGGER.info("Backup created successfully before update run")
                        return True
                    body = await resp.text()
                    _LOGGER.error(
                        "Backup request failed (HTTP %s): %s", resp.status, body
                    )
                    return False
        except Exception as exc:  # noqa: BLE001
            _LOGGER.error("Exception while creating backup: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Update runners
    # ------------------------------------------------------------------

    async def async_run_updates(
        self,
        update_addons: bool,
        update_hacs: bool,
        update_core: bool,
        ignored_addons: list[str],
        ignored_hacs: list[str],
        auto_backup: bool,
    ) -> dict:
        """Install available updates and return a result summary.

        Safe execution order (to avoid mid-run restarts cutting off updates):
          1. HACS repositories   – no restart
          2. Supervisor Addons   – restart only the individual addon
          3. HA Supervisor       – may restart supervisor process
          4. HA Core             – restarts Home Assistant
          5. HA OS               – reboots the host (last!)
        """
        await self.async_refresh()

        data = self.data or {}
        pending_addons: list[str] = data.get("pending_addons", [])
        pending_hacs: list[str] = data.get("pending_hacs", [])
        pending_system: list[str] = data.get("pending_system", [])

        # Build target list in safe order: HACS → Addons → System (if opted in)
        hacs_targets: list[str] = []
        if update_hacs:
            hacs_targets = [
                e for e in pending_hacs if not self._is_ignored(e, ignored_hacs)
            ]

        addon_targets: list[str] = []
        if update_addons:
            addon_targets = [
                e for e in pending_addons if not self._is_ignored(e, ignored_addons)
            ]

        system_targets: list[str] = []
        if update_core:
            # SYSTEM_UPDATE_ENTITIES is already ordered: Supervisor → Core → OS
            system_targets = list(pending_system)

        all_targets = hacs_targets + addon_targets + system_targets

        if not all_targets:
            _LOGGER.info("Auto Updater: no pending updates found")
            return {"updated": [], "skipped": [], "failed": []}

        if auto_backup:
            _LOGGER.info("Auto Updater: creating backup before updating %d item(s)", len(all_targets))
            await self.async_create_backup()

        updated: list[str] = []
        skipped: list[str] = []
        failed: list[str] = []

        for entity_id in all_targets:
            try:
                _LOGGER.info("Auto Updater: installing update for %s", entity_id)
                await self.hass.services.async_call(
                    "update",
                    "install",
                    {"entity_id": entity_id},
                    blocking=True,
                )
                updated.append(entity_id)
                # Brief pause between installs to avoid overwhelming the system
                await asyncio.sleep(2)
            except Exception as exc:  # noqa: BLE001
                _LOGGER.error(
                    "Auto Updater: failed to update %s – %s", entity_id, exc
                )
                failed.append(entity_id)

        # HACS integrations install files to disk but require an HA restart to
        # load the new code. Trigger a restart now — unless a Core/OS update is
        # also in the list, because that will restart HA on its own.
        hacs_were_updated = any(e in updated for e in hacs_targets)
        core_will_restart = any(e in updated for e in system_targets)

        if hacs_were_updated and not core_will_restart:
            _LOGGER.info(
                "Auto Updater: restarting HA to activate %d HACS update(s)",
                len([e for e in hacs_targets if e in updated]),
            )
            await self.hass.services.async_call(
                "homeassistant", "restart", blocking=False
            )

        _LOGGER.info(
            "Auto Updater run complete: updated=%s failed=%s", updated, failed
        )
        return {"updated": updated, "skipped": skipped, "failed": failed}

    async def async_run_updates_from_config(self) -> dict:
        """Convenience wrapper that reads settings from the stored config."""
        return await self.async_run_updates(
            update_addons=self._config.get("update_addons", True),
            update_hacs=self._config.get("update_hacs", True),
            update_core=self._config.get("update_core", False),
            ignored_addons=self._config.get("ignored_addons", []),
            ignored_hacs=self._config.get("ignored_hacs", []),
            auto_backup=self._config.get("auto_backup", False),
        )
