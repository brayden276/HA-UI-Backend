"""Constants for the HA Component Backend split-state feature."""

DOMAIN = "ha_component_backend"
PLATFORMS = ["sensor"]
STORE_VERSION = 1
STORE_KEY = DOMAIN
SENSOR_ENTITY_ID = "sensor.ha_component_backend"

SERVICE_REGISTER_ROOM = "configure_room"
SERVICE_REMOVE_ROOM = "remove_room"
SERVICE_SET_SETTINGS = "update_room"
SERVICE_SET_TIMER = "set_timer"
SERVICE_RESUME_ROOM = "resume_room"
SERVICE_UPSERT_PROFILE = "upsert_profile"
SERVICE_DELETE_PROFILE = "remove_profile"
SERVICE_CONFIGURE_DASHBOARD_PROFILE = "configure_dashboard_profile"
SERVICE_REMOVE_DASHBOARD_PROFILE = "remove_dashboard_profile"

ROOMS = "rooms"
PREFERENCES = "preferences"
PREFERENCE_REVISIONS = "preference_revisions"
REVISION = "revision"
EVENT_PREFERENCES_UPDATED = f"{DOMAIN}_preferences_updated"
WS_PREFERENCES_GET = f"{DOMAIN}/preferences/get"
WS_PREFERENCES_UPDATE = f"{DOMAIN}/preferences/update"
WS_PREFERENCES_REMOVE = f"{DOMAIN}/preferences/remove"
WS_PROFILE_GET = f"{DOMAIN}/profile/get"
WS_PROFILE_UPDATE = f"{DOMAIN}/profile/update"
WS_PROFILE_REMOVE = f"{DOMAIN}/profile/remove"
WS_ENERGY_DAY = f"{DOMAIN}/energy/day"
MAX_PREFERENCE_BYTES = 65_536
FAN_CEILINGS = {"quiet": "Quiet", "low": "Low", "medium": "Medium", "high": "High", "unrestricted": "Unrestricted"}
