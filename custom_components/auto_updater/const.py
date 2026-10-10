"""Constants for the Auto Updater integration."""

DOMAIN = "auto_updater"

# Config entry keys
CONF_UPDATE_ADDONS = "update_addons"
CONF_UPDATE_HACS = "update_hacs"
CONF_UPDATE_CORE = "update_core"
CONF_SCHEDULE_TIME = "schedule_time"
CONF_NOTIFY_SERVICE = "notify_service"
CONF_AUTO_BACKUP = "auto_backup"
CONF_IGNORED_ADDONS = "ignored_addons"  # list of update entity IDs (since v2)
CONF_IGNORED_HACS = "ignored_hacs"  # list of update entity IDs (since v2)
CONF_COOLDOWN_DAYS = "cooldown_days"
CONF_RESTART_AFTER_UPDATE = "restart_after_integration_update"

# Defaults
DEFAULT_UPDATE_ADDONS = True
DEFAULT_UPDATE_HACS = True
DEFAULT_UPDATE_CORE = False  # opt-in only — triggers HA restart
DEFAULT_SCHEDULE_TIME = "03:00:00"
DEFAULT_NOTIFY_SERVICE = ""
DEFAULT_AUTO_BACKUP = False
# A new version must have been offered for this many days before it is
# installed automatically (HACS and add-ons). 0 disables the delay.
DEFAULT_COOLDOWN_DAYS = 3
DEFAULT_RESTART_AFTER_UPDATE = True

MAX_COOLDOWN_DAYS = 30

# Config entry version history:
#   1 – ignore lists were comma-separated substrings matched against entity IDs
#   2 – ignore lists are exact update entity IDs; cooldown and restart options
CONFIG_ENTRY_VERSION = 2

# Well-known entity IDs for system-level updates (hassio platform).
# These must be processed last and are excluded from addon updates unless
# CONF_UPDATE_CORE is explicitly enabled.
ENTITY_CORE_UPDATE = "update.home_assistant_core_update"
ENTITY_SUPERVISOR_UPDATE = "update.home_assistant_supervisor_update"
ENTITY_OS_UPDATE = "update.home_assistant_operating_system_update"

SYSTEM_UPDATE_ENTITIES = (
    ENTITY_SUPERVISOR_UPDATE,  # processed before core
    ENTITY_CORE_UPDATE,        # restarts HA — must be second-to-last
    ENTITY_OS_UPDATE,          # reboots host  — must be last
)

# Platforms that expose update entities
PLATFORM_HASSIO = "hassio"
PLATFORM_HACS = "hacs"

# Services
SERVICE_UPDATE_ALL = "update_all"
SERVICE_UPDATE_ADDONS = "update_addons"
SERVICE_UPDATE_HACS = "update_hacs"

# Attributes
ATTR_UPDATED_ENTITIES = "updated_entities"
ATTR_SKIPPED_ENTITIES = "skipped_entities"
ATTR_FAILED_ENTITIES = "failed_entities"

# Supervisor backup endpoint
SUPERVISOR_API = "http://supervisor"
SUPERVISOR_BACKUP_ENDPOINT = "/backups/new/full"
