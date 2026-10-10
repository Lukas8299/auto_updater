"""Update discovery and installation for Auto Updater."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.update import ATTR_INSTALLED_VERSION, ATTR_LATEST_VERSION
from homeassistant.const import ATTR_FRIENDLY_NAME, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CONF_AUTO_BACKUP,
    CONF_COOLDOWN_DAYS,
    CONF_IGNORED_ADDONS,
    CONF_IGNORED_HACS,
    CONF_RESTART_AFTER_UPDATE,
    CONF_UPDATE_ADDONS,
    CONF_UPDATE_CORE,
    CONF_UPDATE_HACS,
    DEFAULT_AUTO_BACKUP,
    DEFAULT_COOLDOWN_DAYS,
    DEFAULT_RESTART_AFTER_UPDATE,
    DEFAULT_UPDATE_ADDONS,
    DEFAULT_UPDATE_CORE,
    DEFAULT_UPDATE_HACS,
    DOMAIN,
    PLATFORM_HACS,
    PLATFORM_HASSIO,
    SYSTEM_UPDATE_ENTITIES,
)

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(hours=1)
STORAGE_VERSION = 1
# Pause between two installs so add-ons and HACS can settle.
INSTALL_PAUSE = 2

KIND_HACS = "hacs"
KIND_ADDON = "addon"
KIND_SYSTEM = "system"

# HACS sets the repository category as device model. Only integrations need a
# restart to load new code; cards ("plugin") and themes do not.
HACS_CATEGORY_INTEGRATION = "integration"
HACS_CATEGORIES_WITHOUT_RESTART = ("plugin", "theme", "template", "python_script")

STATUS_SUCCESS = "success"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"
STATUS_NO_UPDATES = "no_updates"
STATUS_BACKUP_FAILED = "backup_failed"
STATUS_RUNNING = "running"
STATUS_INTERRUPTED = "interrupted"

SKIP_IGNORED = "ignored"
SKIP_WAITING = "waiting"
SKIP_DISABLED = "disabled"
SKIP_ONE_SYSTEM_PER_RUN = "one_system_update_per_run"


@dataclass
class Candidate:
    """A pending update the integration may install."""

    entity_id: str
    kind: str
    title: str
    installed: str | None
    latest: str | None
    category: str | None = None  # HACS category, None if unknown


@dataclass
class RunOptions:
    """What a single run is allowed to do."""

    update_addons: bool
    update_hacs: bool
    update_core: bool
    ignored: set[str]
    auto_backup: bool
    restart_after_update: bool
    cooldown_days: int

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> RunOptions:
        """Build the options for the scheduled run from the entry config."""
        return cls(
            update_addons=config.get(CONF_UPDATE_ADDONS, DEFAULT_UPDATE_ADDONS),
            update_hacs=config.get(CONF_UPDATE_HACS, DEFAULT_UPDATE_HACS),
            update_core=config.get(CONF_UPDATE_CORE, DEFAULT_UPDATE_CORE),
            ignored=set(config.get(CONF_IGNORED_ADDONS, []))
            | set(config.get(CONF_IGNORED_HACS, [])),
            auto_backup=config.get(CONF_AUTO_BACKUP, DEFAULT_AUTO_BACKUP),
            restart_after_update=config.get(
                CONF_RESTART_AFTER_UPDATE, DEFAULT_RESTART_AFTER_UPDATE
            ),
            cooldown_days=int(config.get(CONF_COOLDOWN_DAYS, DEFAULT_COOLDOWN_DAYS)),
        )


@dataclass
class RunResult:
    """Outcome of one run; persisted and shown by the last-run sensor."""

    trigger: str
    started: str
    status: str = STATUS_RUNNING
    finished: str | None = None
    backup: str = "off"  # off | created | failed
    restart: bool = False
    updated: list[dict[str, Any]] = field(default_factory=list)
    failed: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    message: str | None = None


class AutoUpdaterCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Finds pending updates every hour and installs them when asked to."""

    def __init__(self, hass: HomeAssistant, config: dict[str, Any]) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=SCAN_INTERVAL)
        self._config = config
        self._lock = asyncio.Lock()
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.state")
        # entity_id -> {"version": latest version, "since": ISO timestamp}
        self._first_seen: dict[str, dict[str, str]] = {}
        self.last_run: dict[str, Any] | None = None

    @property
    def config(self) -> dict[str, Any]:
        """The merged entry data and options."""
        return self._config

    @property
    def running(self) -> bool:
        """True while a run is in progress."""
        return self._lock.locked()

    async def async_load(self) -> None:
        """Restore first-seen times and the last run from storage."""
        stored = await self._store.async_load() or {}
        self._first_seen = stored.get("first_seen", {})
        self.last_run = stored.get("last_run")
        if self.last_run and self.last_run.get("status") == STATUS_RUNNING:
            # Home Assistant went down during the run, most likely restarted
            # by the system update that was being installed last.
            self.last_run["status"] = STATUS_INTERRUPTED
            await self._async_save()

    async def _async_save(self) -> None:
        await self._store.async_save(
            {"first_seen": self._first_seen, "last_run": self.last_run}
        )

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def _discover(self) -> list[Candidate]:
        """Return all pending HACS, add-on and system updates."""
        ent_reg = er.async_get(self.hass)
        dev_reg = dr.async_get(self.hass)
        candidates: list[Candidate] = []

        for entry in ent_reg.entities.values():
            if entry.domain != "update" or entry.disabled_by:
                continue
            # Only HACS and Supervisor updates. Device firmware (ESPHome, ZHA,
            # Z-Wave, Matter, ...) is never touched.
            if entry.platform == PLATFORM_HACS:
                kind = KIND_HACS
            elif entry.platform == PLATFORM_HASSIO:
                kind = KIND_SYSTEM if entry.entity_id in SYSTEM_UPDATE_ENTITIES else KIND_ADDON
            else:
                continue

            state = self.hass.states.get(entry.entity_id)
            if state is None or state.state != STATE_ON:
                continue

            category = None
            if kind == KIND_HACS and entry.device_id:
                device = dev_reg.async_get(entry.device_id)
                category = (device.model or None) if device else None

            candidates.append(
                Candidate(
                    entity_id=entry.entity_id,
                    kind=kind,
                    title=state.attributes.get(ATTR_FRIENDLY_NAME, entry.entity_id),
                    installed=state.attributes.get(ATTR_INSTALLED_VERSION),
                    latest=state.attributes.get(ATTR_LATEST_VERSION),
                    category=category,
                )
            )

        # System updates in their fixed order: Supervisor, Core, OS.
        candidates.sort(
            key=lambda c: SYSTEM_UPDATE_ENTITIES.index(c.entity_id)
            if c.kind == KIND_SYSTEM
            else -1
        )
        return candidates

    def _track_first_seen(self, candidates: list[Candidate]) -> bool:
        """Remember since when each version is offered. Returns True if changed."""
        now = dt_util.utcnow().isoformat()
        changed = False
        pending = {c.entity_id: c for c in candidates}
        for entity_id, cand in pending.items():
            seen = self._first_seen.get(entity_id)
            if seen is None or seen.get("version") != cand.latest:
                self._first_seen[entity_id] = {"version": str(cand.latest), "since": now}
                changed = True
        for entity_id in list(self._first_seen):
            if entity_id not in pending:
                del self._first_seen[entity_id]
                changed = True
        return changed

    async def _async_update_data(self) -> dict[str, Any]:
        candidates = self._discover()
        if self._track_first_seen(candidates):
            await self._async_save()
        return {"pending": [asdict(c) for c in candidates]}

    def _waiting_until(self, cand: Candidate, cooldown_days: int) -> datetime | None:
        """Return when the cooldown for this version ends, or None if it has."""
        if cooldown_days <= 0 or cand.kind == KIND_SYSTEM:
            return None
        seen = self._first_seen.get(cand.entity_id)
        since = dt_util.parse_datetime(seen["since"]) if seen else None
        if since is None:
            since = dt_util.utcnow()
        until = since + timedelta(days=cooldown_days)
        return until if until > dt_util.utcnow() else None

    # ------------------------------------------------------------------
    # Installation
    # ------------------------------------------------------------------

    async def _async_backup(self) -> bool:
        """Create a backup with Home Assistant's automatic backup settings."""
        if not self.hass.services.has_service("backup", "create_automatic"):
            _LOGGER.error("Backup requested, but the backup integration is not loaded")
            return False
        try:
            # Returns when the backup is written; raises if it fails.
            await self.hass.services.async_call(
                "backup", "create_automatic", {}, blocking=True
            )
        except Exception as exc:  # noqa: BLE001 - any failure must stop the run
            _LOGGER.error("Backup before updating failed: %s", exc)
            return False
        return True

    async def async_run(self, options: RunOptions, trigger: str) -> dict[str, Any]:
        """Install pending updates according to options. Never runs twice at once."""
        if self._lock.locked():
            _LOGGER.warning("Auto Updater: run '%s' skipped, another run is in progress", trigger)
            return asdict(
                RunResult(
                    trigger=trigger,
                    started=dt_util.utcnow().isoformat(),
                    finished=dt_util.utcnow().isoformat(),
                    status=STATUS_FAILED,
                    message="another run is in progress",
                )
            )
        async with self._lock:
            return await self._async_run_locked(options, trigger)

    async def _async_run_locked(self, options: RunOptions, trigger: str) -> dict[str, Any]:
        result = RunResult(trigger=trigger, started=dt_util.utcnow().isoformat())
        await self.async_refresh()
        candidates = [Candidate(**c) for c in (self.data or {}).get("pending", [])]

        targets: list[Candidate] = []
        system_chosen = False
        for cand in candidates:
            enabled = {
                KIND_HACS: options.update_hacs,
                KIND_ADDON: options.update_addons,
                KIND_SYSTEM: options.update_core,
            }[cand.kind]
            if not enabled:
                result.skipped.append(_skip(cand, SKIP_DISABLED))
                continue
            if cand.entity_id in options.ignored:
                result.skipped.append(_skip(cand, SKIP_IGNORED))
                continue
            until = self._waiting_until(cand, options.cooldown_days)
            if until is not None:
                result.skipped.append(_skip(cand, SKIP_WAITING, until=until.isoformat()))
                continue
            if cand.kind == KIND_SYSTEM:
                # Supervisor, Core and OS each restart something; whatever
                # follows would be cut off. One per run, as the very last step.
                if system_chosen:
                    result.skipped.append(_skip(cand, SKIP_ONE_SYSTEM_PER_RUN))
                    continue
                system_chosen = True
            targets.append(cand)

        # HACS first, then add-ons, then the one system update.
        order = {KIND_HACS: 0, KIND_ADDON: 1, KIND_SYSTEM: 2}
        targets.sort(key=lambda c: order[c.kind])

        if not targets:
            result.status = STATUS_NO_UPDATES
            return await self._async_finish(result)

        if options.auto_backup:
            _LOGGER.info("Auto Updater: creating backup before %d update(s)", len(targets))
            if not await self._async_backup():
                result.backup = "failed"
                result.status = STATUS_BACKUP_FAILED
                result.message = "backup failed, no updates installed"
                return await self._async_finish(result)
            result.backup = "created"

        for cand in targets:
            if cand.kind == KIND_SYSTEM:
                # This may restart Home Assistant before the call returns.
                # Persist what happened so far so the result survives it.
                result.message = f"{cand.entity_id} was being installed last"
                await self._async_finish(result, final=False)
            try:
                _LOGGER.info("Auto Updater: installing %s (%s -> %s)",
                             cand.entity_id, cand.installed, cand.latest)
                await self.hass.services.async_call(
                    "update", "install", {"entity_id": cand.entity_id}, blocking=True
                )
            except Exception as exc:  # noqa: BLE001 - report and continue with the next
                _LOGGER.error("Auto Updater: failed to update %s: %s", cand.entity_id, exc)
                result.failed.append({**_brief(cand), "error": str(exc)})
                continue
            result.updated.append(_brief(cand))
            await asyncio.sleep(INSTALL_PAUSE)

        result.message = None
        result.status = (
            STATUS_SUCCESS if not result.failed
            else STATUS_PARTIAL if result.updated
            else STATUS_FAILED
        )

        system_updated = any(u["kind"] == KIND_SYSTEM for u in result.updated)
        integration_updated = any(
            u["kind"] == KIND_HACS and u.get("category") not in HACS_CATEGORIES_WITHOUT_RESTART
            for u in result.updated
        )
        if options.restart_after_update and integration_updated and not system_updated:
            result.restart = True

        await self._async_finish(result)
        if result.restart:
            _LOGGER.info("Auto Updater: restarting Home Assistant to load updated integrations")
            await self.hass.services.async_call("homeassistant", "restart", blocking=False)
        return self.last_run or {}

    async def _async_finish(self, result: RunResult, final: bool = True) -> dict[str, Any]:
        if final:
            result.finished = dt_util.utcnow().isoformat()
        self.last_run = asdict(result)
        await self._async_save()
        self.async_update_listeners()
        _LOGGER.info(
            "Auto Updater run '%s': %s, updated=%d failed=%d skipped=%d",
            result.trigger, result.status, len(result.updated),
            len(result.failed), len(result.skipped),
        )
        return self.last_run


def _brief(cand: Candidate) -> dict[str, Any]:
    return {
        "entity_id": cand.entity_id,
        "kind": cand.kind,
        "title": cand.title,
        "from": cand.installed,
        "to": cand.latest,
        "category": cand.category,
    }


def _skip(cand: Candidate, reason: str, **extra: Any) -> dict[str, Any]:
    return {**_brief(cand), "reason": reason, **extra}
