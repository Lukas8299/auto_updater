# Auto Updater

A Home Assistant custom integration that automatically installs Supervisor Addon and HACS updates on a configurable schedule.

## Features

- Automatically updates HACS integrations and Lovelace plugins
- Automatically updates Supervisor Addons
- Optional Home Assistant Core / Supervisor / OS updates (opt-in)
- Configurable daily schedule (default: 03:00)
- Optional pre-update backup via Supervisor
- Persistent notification + optional custom notify service after each run
- Manual trigger via services

## Installation via HACS

1. In HA → HACS → Integrations → Custom Repositories, add this repo URL and select **Integration**
2. Install "Auto Updater" from HACS
3. Restart Home Assistant
4. Go to **Settings → Devices & Services → Add Integration** and search for "Auto Updater"

## Configuration

All options are set via the UI config flow:

| Option | Default | Description |
|---|---|---|
| Update Addons | `true` | Auto-update Supervisor Addons |
| Update HACS | `true` | Auto-update HACS integrations/plugins |
| Update Core | `false` | Also update HA Core (triggers restart) |
| Schedule Time | `03:00` | Daily time to run updates (HH:MM) |
| Auto Backup | `false` | Create a full backup before updating |
| Notify Service | _(empty)_ | Optional service to call, e.g. `notify.mobile_app_phone` |
| Ignored Addons | _(empty)_ | Comma-separated addon slugs to skip |
| Ignored HACS | _(empty)_ | Comma-separated HACS repo names to skip |

## Services

| Service | Description |
|---|---|
| `auto_updater.update_all` | Run all updates immediately |
| `auto_updater.update_addons` | Update Supervisor Addons only |
| `auto_updater.update_hacs` | Update HACS only |

All services accept optional `ignored_addons`, `ignored_hacs`, `auto_backup`, and `update_core` parameters.
