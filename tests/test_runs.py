"""Tests for update runs: waiting period, ignore list, backup, restart, order, lock."""
from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.auto_updater import _format_report
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
from custom_components.auto_updater.coordinator import RunOptions

CONFIG: dict[str, Any] = {
    CONF_UPDATE_ADDONS: True,
    CONF_UPDATE_HACS: True,
    CONF_UPDATE_CORE: False,
    CONF_SCHEDULE_TIME: "03:00:00",
    CONF_COOLDOWN_DAYS: 0,
    CONF_RESTART_AFTER_UPDATE: True,
    CONF_AUTO_BACKUP: False,
    CONF_NOTIFY_SERVICE: "",
    CONF_IGNORED_ADDONS: [],
    CONF_IGNORED_HACS: [],
}


class Calls:
    """Records calls to the services the integration uses."""

    def __init__(self) -> None:
        self.install: list[str] = []
        self.restart = 0
        self.backup = 0
        self.backup_error: Exception | None = None
        self.install_gate: asyncio.Event | None = None


@pytest.fixture(autouse=True)
def no_install_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the 2 s pause between installs (it never ends with a frozen clock)."""
    monkeypatch.setattr("custom_components.auto_updater.coordinator.INSTALL_PAUSE", 0)


@pytest.fixture
def calls(hass: HomeAssistant) -> Calls:
    """Fake update.install, backup.create_automatic and homeassistant.restart."""
    rec = Calls()

    async def _install(call: ServiceCall) -> None:
        if rec.install_gate is not None:
            await rec.install_gate.wait()
        rec.install.extend(call.data["entity_id"] if isinstance(call.data["entity_id"], list)
                           else [call.data["entity_id"]])

    async def _backup(call: ServiceCall) -> None:
        rec.backup += 1
        if rec.backup_error:
            raise rec.backup_error

    async def _restart(call: ServiceCall) -> None:
        rec.restart += 1

    hass.services.async_register("update", "install", _install)
    hass.services.async_register("backup", "create_automatic", _backup)
    hass.services.async_register("homeassistant", "restart", _restart)
    return rec


def add_update(
    hass: HomeAssistant,
    object_id: str,
    platform: str,
    *,
    model: str | None = None,
    installed: str = "1.0.0",
    latest: str = "1.1.0",
) -> str:
    """Register a pending update entity with state 'on'."""
    device_id = None
    if model is not None:
        source = MockConfigEntry(domain=platform)
        source.add_to_hass(hass)
        device_id = dr.async_get(hass).async_get_or_create(
            config_entry_id=source.entry_id,
            identifiers={(platform, object_id)},
            model=model,
        ).id
    entry = er.async_get(hass).async_get_or_create(
        "update", platform, object_id, suggested_object_id=object_id, device_id=device_id
    )
    hass.states.async_set(
        entry.entity_id,
        "on",
        {"installed_version": installed, "latest_version": latest, "friendly_name": object_id},
    )
    return entry.entity_id


async def setup_entry(hass: HomeAssistant, **overrides: Any) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data={**CONFIG, **overrides}, version=2)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def run(entry: MockConfigEntry, **overrides: Any) -> dict[str, Any]:
    coordinator = entry.runtime_data
    options = RunOptions.from_config({**coordinator.config, **overrides})
    return await coordinator.async_run(options, trigger="test")


async def test_waiting_period(
    hass: HomeAssistant, calls: Calls, freezer: FrozenDateTimeFactory
) -> None:
    """A new version is installed only after it was offered for the waiting period."""
    card = add_update(hass, "mushroom_update", "hacs", model="plugin")
    entry = await setup_entry(hass, **{CONF_COOLDOWN_DAYS: 3})

    result = await run(entry)
    assert calls.install == []
    assert result["status"] == "no_updates"
    assert [s["reason"] for s in result["skipped"]] == ["waiting"]

    freezer.tick(timedelta(days=2, hours=23))
    await run(entry)
    assert calls.install == []

    freezer.tick(timedelta(hours=2))
    result = await run(entry)
    assert calls.install == [card]
    assert result["status"] == "success"


async def test_new_version_restarts_waiting_period(
    hass: HomeAssistant, calls: Calls, freezer: FrozenDateTimeFactory
) -> None:
    """When a newer version appears, its own waiting period starts."""
    card = add_update(hass, "mushroom_update", "hacs", model="plugin")
    entry = await setup_entry(hass, **{CONF_COOLDOWN_DAYS: 3})
    freezer.tick(timedelta(days=2))
    hass.states.async_set(card, "on", {"installed_version": "1.0.0", "latest_version": "1.2.0",
                                       "friendly_name": "mushroom_update"})
    freezer.tick(timedelta(days=2))
    await run(entry)
    assert calls.install == []  # 1.2.0 offered for 2 days only


async def test_ignore_list_is_exact(hass: HomeAssistant, calls: Calls) -> None:
    """Only the listed entity is skipped, not every entity containing its name."""
    ignored = add_update(hass, "mushroom", "hacs", model="plugin")
    similar = add_update(hass, "mushroom_strategy", "hacs", model="plugin")
    entry = await setup_entry(hass, **{CONF_IGNORED_HACS: [ignored]})

    result = await run(entry)
    assert calls.install == [similar]
    assert [(s["entity_id"], s["reason"]) for s in result["skipped"]] == [(ignored, "ignored")]


async def test_backup_failure_aborts_run(hass: HomeAssistant, calls: Calls) -> None:
    """If the backup fails, nothing is installed and the user is told."""
    add_update(hass, "mushroom_update", "hacs", model="plugin")
    calls.backup_error = HomeAssistantError("no backup agent")
    entry = await setup_entry(hass, **{CONF_AUTO_BACKUP: True})

    result = await run(entry)
    assert calls.backup == 1
    assert calls.install == []
    assert result["status"] == "backup_failed"
    assert "no updates were installed" in _format_report(result)


async def test_backup_without_backup_integration_aborts(hass: HomeAssistant, calls: Calls) -> None:
    """A missing backup service counts as failed backup."""
    add_update(hass, "mushroom_update", "hacs", model="plugin")
    hass.services.async_remove("backup", "create_automatic")
    entry = await setup_entry(hass, **{CONF_AUTO_BACKUP: True})
    result = await run(entry)
    assert result["status"] == "backup_failed"
    assert calls.install == []


async def test_backup_success_then_install(hass: HomeAssistant, calls: Calls) -> None:
    card = add_update(hass, "mushroom_update", "hacs", model="plugin")
    entry = await setup_entry(hass, **{CONF_AUTO_BACKUP: True})
    result = await run(entry)
    assert calls.backup == 1
    assert calls.install == [card]
    assert result["backup"] == "created"


@pytest.mark.parametrize(
    ("model", "restart_option", "expect_restart"),
    [
        ("plugin", True, False),
        ("theme", True, False),
        ("integration", True, True),
        (None, True, True),  # unknown category: restart to be safe
        ("integration", False, False),
    ],
)
async def test_restart_only_after_integrations(
    hass: HomeAssistant, calls: Calls, model: str | None, restart_option: bool,
    expect_restart: bool,
) -> None:
    add_update(hass, "thing_update", "hacs", model=model)
    entry = await setup_entry(hass, **{CONF_RESTART_AFTER_UPDATE: restart_option})
    result = await run(entry)
    await hass.async_block_till_done()
    assert calls.restart == (1 if expect_restart else 0)
    assert result["restart"] is expect_restart


async def test_order_and_one_system_update(hass: HomeAssistant, calls: Calls) -> None:
    """HACS, then add-ons, then exactly one system update (Supervisor before Core)."""
    core = add_update(hass, "home_assistant_core_update", "hassio")
    supervisor = add_update(hass, "home_assistant_supervisor_update", "hassio")
    addon = add_update(hass, "mosquitto_broker_update", "hassio")
    card = add_update(hass, "mushroom_update", "hacs", model="plugin")
    entry = await setup_entry(hass, **{CONF_UPDATE_CORE: True})

    result = await run(entry)
    assert calls.install == [card, addon, supervisor]
    skipped = {s["entity_id"]: s["reason"] for s in result["skipped"]}
    assert skipped == {core: "one_system_update_per_run"}


async def test_system_updates_ignore_waiting_period(hass: HomeAssistant, calls: Calls) -> None:
    core = add_update(hass, "home_assistant_core_update", "hassio")
    entry = await setup_entry(hass, **{CONF_UPDATE_CORE: True, CONF_COOLDOWN_DAYS: 3})
    await run(entry)
    assert calls.install == [core]


async def test_core_disabled_by_default(hass: HomeAssistant, calls: Calls) -> None:
    add_update(hass, "home_assistant_core_update", "hassio")
    entry = await setup_entry(hass)
    result = await run(entry)
    assert calls.install == []
    assert result["skipped"][0]["reason"] == "disabled"


async def test_no_parallel_runs(hass: HomeAssistant, calls: Calls) -> None:
    """A second run while one is in progress does nothing."""
    card = add_update(hass, "mushroom_update", "hacs", model="plugin")
    entry = await setup_entry(hass)
    calls.install_gate = asyncio.Event()

    first = hass.async_create_task(run(entry))
    for _ in range(5):
        await asyncio.sleep(0)
    assert entry.runtime_data.running
    # Without the lock the second run would wait on the gate as well.
    async with asyncio.timeout(2):
        second = await run(entry)
    assert second["message"] == "another run is in progress"

    calls.install_gate.set()
    await first
    assert calls.install == [card]


async def test_service_uses_configuration(hass: HomeAssistant, calls: Calls) -> None:
    """Services respect the configured ignore list and waiting period."""
    ignored = add_update(hass, "ignored_update", "hacs", model="plugin")
    waiting = add_update(hass, "fresh_update", "hacs", model="plugin")
    await setup_entry(hass, **{CONF_IGNORED_HACS: [ignored], CONF_COOLDOWN_DAYS: 3})

    response = await hass.services.async_call(
        DOMAIN, "update_hacs", {}, blocking=True, return_response=True
    )
    assert calls.install == []
    reasons = {s["entity_id"]: s["reason"] for s in response["skipped"]}
    assert reasons == {ignored: "ignored", waiting: "waiting"}

    response = await hass.services.async_call(
        DOMAIN, "update_hacs", {"ignore_waiting_period": True}, blocking=True,
        return_response=True,
    )
    assert calls.install == [waiting]


async def test_service_without_entry_raises(hass: HomeAssistant, calls: Calls) -> None:
    from homeassistant.exceptions import ServiceValidationError
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, DOMAIN, {})
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "update_all", {}, blocking=True)


async def test_interrupted_run_is_marked(hass: HomeAssistant, hass_storage: dict) -> None:
    """A run that was cut off by a restart shows up as interrupted."""
    hass_storage["auto_updater.state"] = {
        "version": 1,
        "key": "auto_updater.state",
        "data": {"first_seen": {}, "last_run": {"status": "running", "trigger": "schedule"}},
    }
    entry = await setup_entry(hass)
    assert entry.runtime_data.last_run["status"] == "interrupted"


def test_report_lists_versions() -> None:
    report = _format_report(
        {
            "status": "partial",
            "updated": [{"title": "Mushroom", "from": "1.0", "to": "1.1"}],
            "failed": [{"title": "Card Mod", "error": "boom"}],
            "skipped": [{"title": "Fresh", "reason": "waiting"}],
        }
    )
    assert "Mushroom: 1.0 → 1.1" in report
    assert "Card Mod: boom" in report
    assert "Fresh" in report


def test_report_silent_without_changes() -> None:
    assert _format_report({"status": "no_updates", "skipped": []}) is None
    assert _format_report({"status": "success", "updated": [], "failed": [],
                           "skipped": [{"title": "x", "reason": "waiting"}]}) is None
