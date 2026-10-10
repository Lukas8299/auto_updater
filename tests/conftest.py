"""Shared fixtures."""
from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant, ServiceCall

from .helpers import Calls

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load custom_components/ in every test."""
    yield


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
