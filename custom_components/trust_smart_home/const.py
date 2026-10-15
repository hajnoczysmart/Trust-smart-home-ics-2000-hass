"""Constants for the Trust Smart Home (ICS-2000) integration."""

DOMAIN = "trust_smart_home"
MANUFACTURER = "Trust / KlikAanKlikUit"

CONF_HOME = "home"
# CONF_IP_ADDRESS (homeassistant.const) holds the optional LAN address of the hub.

# Options: extra sensors the user maps onto a raw status position
CONF_MAPPINGS = "mappings"  # {entity_id: {role, index, active_value, device_class}}
ROLE_NONE = "none"
ROLE_BINARY_SENSOR = "binary_sensor"
ROLE_SENSOR = "sensor"

CONF_SCAN_INTERVAL = "scan_interval"  # seconds between status polls
CONF_LOCAL_STATUS = "local_status"  # read statuses from the hub on the LAN (experimental, off)
DEFAULT_SCAN_INTERVAL = 3
MIN_SCAN_INTERVAL = 1
MAX_SCAN_INTERVAL = 60
REFRESH_AFTER_COMMAND = 2.0  # seconds

# Fired on the event bus for every button press (used by device triggers)
EVENT_PRESS = f"{DOMAIN}_press"

PLATFORMS = [
    "alarm_control_panel",
    "binary_sensor",
    "button",
    "cover",
    "event",
    "light",
    "scene",
    "sensor",
    "switch",
]

# Colour temperature range offered in Home Assistant (device accepts 0-600 mired)
MIN_KELVIN = 2000
MAX_KELVIN = 6500

BINARY_DEVICE_CLASSES = [
    "none",
    "motion",
    "occupancy",
    "door",
    "window",
    "opening",
    "smoke",
    "gas",
    "carbon_monoxide",
    "heat",
    "moisture",
    "problem",
    "safety",
    "tamper",
    "vibration",
    "battery",
]
