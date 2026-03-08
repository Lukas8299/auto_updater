# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Home Assistant custom integration (HACS-compatible) that automatically installs Supervisor Addon and HACS updates on a configurable schedule, with optional pre-update backups and notifications.

## Development Setup

No build system is required. This is a pure Python Home Assistant integration. To develop and test:

1. Copy/symlink `custom_components/auto_updater/` into a Home Assistant instance's `custom_components/` directory.
2. Restart Home Assistant and add the integration via the UI.
3. There are no automated tests — test by running the integration in a real or development Home Assistant instance.

For linting, use `pylint` or `flake8` with Home Assistant's coding style. No configuration files are present, so use defaults.

## Architecture

The integration follows Home Assistant's standard `DataUpdateCoordinator` pattern:

```
__init__.py          → Entry point: sets up coordinator, scheduler, and services
coordinator.py       → Core logic: update discovery and installation
config_flow.py       → UI setup and options flows (initial config + reconfiguration)
const.py             → All constants, config keys, and defaults
```

### Key Workflows

**Update Discovery** (`coordinator._async_update_data`):
- Queries HA's entity registry for all `update` domain entities
- Categorizes into addons (hassio platform), HACS repos, and system (Core/Supervisor/OS)
- Runs hourly, but installs only at the scheduled time

**Update Installation** (`coordinator.async_run_updates`):
- Executes in safe order to avoid mid-run restarts:
  1. HACS repositories (no restart)
  2. Supervisor Addons
  3. Supervisor
  4. HA Core
  5. HA OS (last — reboots host)
- 2-second pause between installs
- Auto-triggers HA restart after HACS updates if no Core update follows
- Optionally creates a full Supervisor backup before updating (300s timeout)

**Scheduling** (`__init__.async_setup_entry`):
- Uses `async_track_time_change` for daily triggers at the configured time (default 03:00)
- Three services registered: `update_all`, `update_addons`, `update_hacs`

### Supervisor API
Backup creation hits the Supervisor REST API using the `SUPERVISOR_TOKEN` environment variable as a bearer token. Endpoint: `http://supervisor/backups/new/full`.

### Configuration
Stored in a Home Assistant config entry. Key constants are in `const.py`. The config flow (`config_flow.py`) validates HH:MM time format and parses comma-separated ignore lists into Python lists.
