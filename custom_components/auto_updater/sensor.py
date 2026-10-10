"""Sensors: pending updates and the result of the last run."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import (
    STATUS_BACKUP_FAILED,
    STATUS_FAILED,
    STATUS_INTERRUPTED,
    STATUS_NO_UPDATES,
    STATUS_PARTIAL,
    STATUS_RUNNING,
    STATUS_SUCCESS,
    AutoUpdaterCoordinator,
    Candidate,
    RunOptions,
)

LAST_RUN_STATES = [
    STATUS_SUCCESS,
    STATUS_PARTIAL,
    STATUS_FAILED,
    STATUS_NO_UPDATES,
    STATUS_BACKUP_FAILED,
    STATUS_INTERRUPTED,
    STATUS_RUNNING,
]


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Add the two sensors."""
    coordinator: AutoUpdaterCoordinator = entry.runtime_data
    async_add_entities(
        [PendingUpdatesSensor(coordinator, entry), LastRunSensor(coordinator, entry)]
    )


class _Base(CoordinatorEntity[AutoUpdaterCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: AutoUpdaterCoordinator, entry: ConfigEntry, key: str) -> None:
        super().__init__(coordinator)
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Auto Updater",
            entry_type=DeviceEntryType.SERVICE,
        )


class PendingUpdatesSensor(_Base):
    """Number of pending HACS, add-on and system updates."""

    _attr_native_unit_of_measurement = "updates"

    def __init__(self, coordinator: AutoUpdaterCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "pending_updates")

    @property
    def native_value(self) -> int:
        return len((self.coordinator.data or {}).get("pending", []))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        options = RunOptions.from_config(self.coordinator.config)
        updates = []
        for raw in (self.coordinator.data or {}).get("pending", []):
            cand = Candidate(**raw)
            until = self.coordinator.waiting_until(cand, options.cooldown_days)
            updates.append(
                {
                    "entity_id": cand.entity_id,
                    "title": cand.title,
                    "kind": cand.kind,
                    "from": cand.installed,
                    "to": cand.latest,
                    "ignored": cand.entity_id in options.ignored,
                    "installable_from": until.isoformat() if until else None,
                }
            )
        return {"updates": updates}


class LastRunSensor(_Base):
    """Outcome of the most recent run; details as attributes."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = LAST_RUN_STATES

    def __init__(self, coordinator: AutoUpdaterCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "last_run")

    @property
    def native_value(self) -> str | None:
        run = self.coordinator.last_run
        return run.get("status") if run else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        run = self.coordinator.last_run or {}
        return {
            "trigger": run.get("trigger"),
            "started": run.get("started"),
            "finished": run.get("finished"),
            "backup": run.get("backup"),
            "restart": run.get("restart"),
            "message": run.get("message"),
            "updated": [f"{u['title']}: {u.get('from')} → {u.get('to')}" for u in run.get("updated", [])],
            "failed": [f"{f['title']}: {f.get('error')}" for f in run.get("failed", [])],
            "skipped": len(run.get("skipped", [])),
        }
