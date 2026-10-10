# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project

Home Assistant custom integration (HACS) that installs HACS and Supervisor add-on updates on a daily schedule. Source of truth is the Forgejo repo `lukas/auto_updater`; GitHub `Lukas8299/auto_updater` is a push mirror because HACS only installs from GitHub. Create GitHub releases for new tags, HACS offers versions from releases.

## Commands

```bash
.venv/bin/pytest                      # tests (pytest-homeassistant-custom-component)
podman run --rm -v "$PWD/custom_components:/github/workspace/custom_components:ro" \
  ghcr.io/home-assistant/hassfest     # manifest, translations, services
```

Set up the venv with `python3 -m venv .venv && .venv/bin/pip install -r requirements_test.txt`. The pinned test package defines the Home Assistant version tests run against.

## Architecture

```
__init__.py      setup, schedule, services (registered in async_setup), v1→v2 migration, report
coordinator.py   discovery, waiting period, run logic, persistence (.storage/auto_updater.state)
config_flow.py   setup and options flow (shared schema)
sensor.py        pending updates, last run
const.py         config keys and defaults
translations/    en.json, de.json (custom integrations do not read strings.json)
```

- `RunOptions.from_config()` turns the entry config into what a run may do; services start from it and override single fields.
- A run: refresh → filter (disabled, ignored, waiting, one system update) → optional backup (abort on failure) → install HACS, add-ons, then the system update → restart if an integration was updated.
- The result is saved before a system update, because it may restart HA before the call returns; such a run is shown as `interrupted` after the restart.
- HACS category comes from the device model HACS sets (`integration`, `plugin`, `theme`). Unknown category counts as integration (restart to be safe).

## Decisions (2026-10, v2.0.0)

- Backup via `backup.create_automatic` (works without Supervisor), not the Supervisor REST API. Minimum HA 2025.1.
- Waiting period default 3 days, for HACS and add-ons, not for Core/Supervisor/OS.
- Restart after updates is an option, on by default, only for integrations.
- Ignore lists are exact update entity IDs (entity picker), not substrings.
- No CI; run tests and hassfest locally before releasing.

## Rules

- Keep `manifest.json` keys in hassfest order: `domain`, `name`, then alphabetical.
- Every user-visible string goes into both `translations/en.json` and `de.json`.
- Every behaviour change needs a test; check that the test fails when the behaviour is broken.
