# Auto Updater

A Home Assistant custom integration that installs HACS and Supervisor add-on updates on a daily schedule, with a waiting period for new versions, an optional backup first, and sensors that report what happened.

## Features

- Updates HACS repositories (integrations, dashboard cards, themes) and Supervisor add-ons
- **Waiting period**: a new version is installed only after it has been offered for a number of days (default 3). Releases that are withdrawn or fixed quickly never reach your system.
- **Backup first** (optional): uses Home Assistant's automatic backup. If the backup fails, nothing is installed.
- **Restart only when needed**: Home Assistant is restarted after a HACS *integration* update, never for cards or themes (optional)
- Optional Core / Supervisor / OS updates: at most one per run, always as the last step
- Exact ignore lists, chosen from your update entities
- Sensors for pending updates and the result of the last run
- Report as persistent notification and, optionally, through a notify service, listing "from → to" versions
- Services to run updates now, returning the result
- Device firmware (ESPHome, ZHA, Z-Wave, Matter, …) is never touched

## Requirements

- Home Assistant **2025.1** or newer
- HACS for HACS updates
- Add-on, Core, Supervisor and OS updates need a Supervisor (Home Assistant OS or Supervised). On Container and Core installations these options are hidden; HACS updates and the backup work everywhere.
- For the backup: automatic backups must be set up under **Settings → System → Backups** (at least one backup location).

## Installation via HACS

1. In HA → HACS → Custom repositories, add this repository and select **Integration**
2. Install "Auto Updater" from HACS and restart Home Assistant
3. **Settings → Devices & services → Add integration** → "Auto Updater"

## Configuration

All options are set in the UI and can be changed later under the integration's **Configure** button.

| Option | Default | Description |
|---|---|---|
| Update add-ons | on | Supervisor add-ons (only with Supervisor) |
| Update HACS | on | HACS integrations, cards and themes |
| Daily update time | 03:00 | When the daily run starts |
| Waiting period for new versions | 3 days | 0 installs immediately. Applies to HACS and add-ons, not to Core/Supervisor/OS |
| Restart after integration updates | on | Restart only when a HACS integration was updated |
| Create a backup before updating | off | Automatic backup; a failed backup stops the run |
| Also update Core, Supervisor and OS | off | One per run, as the last step (only with Supervisor) |
| Notify service | – | e.g. `notify.mobile_app_my_phone` |
| Ignored add-ons / HACS repositories | – | Picked from your update entities |

### How the waiting period works

Every hour the integration notes which version each update entity offers and since when. A version is installed once it has been offered for the configured number of days. If a newer version appears in the meantime, its own waiting period starts. The sensor "Pending updates" shows for each update when it becomes installable.

## Sensors

| Sensor | State | Attributes |
|---|---|---|
| `sensor.auto_updater_pending_updates` | number of pending updates | per update: title, from, to, ignored, installable from |
| `sensor.auto_updater_last_run` | `success`, `partial`, `failed`, `no_updates`, `backup_failed`, `interrupted`, `running` | trigger, start/end, backup, restart, updated and failed items |

The entity IDs above are those of an English installation; Home Assistant derives them from the translated names, so in German they are `sensor.auto_updater_ausstehende_updates` and `sensor.auto_updater_letzter_lauf`.

`interrupted` means Home Assistant went down during the run, normally because a Core/Supervisor/OS update was installed last and restarted it.

Example automation:

```yaml
triggers:
  - trigger: state
    entity_id: sensor.auto_updater_last_run
    to: [failed, partial, backup_failed]
actions:
  - action: notify.mobile_app_my_phone
    data:
      message: "Auto Updater: {{ states('sensor.auto_updater_last_run') }}"
```

## Services

`auto_updater.update_all`, `auto_updater.update_addons`, `auto_updater.update_hacs` run immediately with the configured ignore lists, waiting period, backup and restart setting. Optional fields:

| Field | Description |
|---|---|
| `ignore_waiting_period` | Install new versions even if they were offered only recently |
| `additional_ignored` | Skip more update entities in this run |
| `auto_backup` | Override the backup setting |
| `restart_after_integration_update` | Override the restart setting |
| `update_core` | (`update_all`, `update_addons`) also install one Core/Supervisor/OS update |

The services return the run result when called with a response (e.g. from **Developer tools → Actions**).

## Upgrading from 1.x

- Requires Home Assistant 2025.1.
- Your settings are migrated automatically. The old ignore patterns are converted once to the update entities they matched; check them under **Configure**.
- The waiting period (3 days) also applies to updates that were already pending when you upgraded. Set it to 0 if you want the old behaviour.
- The backup now uses Home Assistant's automatic backup instead of the Supervisor API; set up automatic backups if you use this option.
- Manual service calls now respect your ignore lists.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements_test.txt
.venv/bin/pytest
```

Manifest, translations and services can be checked with hassfest:

```bash
podman run --rm -v "$PWD/custom_components:/github/workspace/custom_components:ro" ghcr.io/home-assistant/hassfest
```
