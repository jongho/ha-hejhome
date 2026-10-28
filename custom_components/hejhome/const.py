"""Constants for the Hejhome integration."""
from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "hejhome"

CONF_FAMILY_ID = "family_id"
CONF_SESSION = "session"

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.EVENT,
    Platform.LIGHT,
    Platform.SENSOR,
    Platform.SWITCH,
]

# REST polling is only a safety net; state changes arrive over MQTT.
POLL_INTERVAL_SECONDS = 300

# Device type groups (from the Hejhome web dashboard's deviceType field).
COLOR_LIGHT_TYPES = {
    "LightRgbw5", "LightRgbw4", "LightRgbw3", "LightRgbw2", "LightRgbw1",
    "LightRgb5", "LightRgb4",
    "LedStripRgbw2", "LedStripRgbw1", "LedStripRgb",
}
WHITE_LIGHT_TYPES = {"LightWw3", "LightWw2", "LightWw1"}
LIGHT_TYPES = COLOR_LIGHT_TYPES | WHITE_LIGHT_TYPES

MOTION_TYPES = {"SensorMo", "SensorRadar"}
CONTACT_TYPES = {"SensorDo"}
TEMP_HUMIDITY_TYPES = {"SensorTh", "SensorTh2", "SensorRefTh", "SensorRefTh2"}
LEAK_TYPES = {"SensorWater2", "SensorWater3"}
SMOKE_TYPES = {"SensorSmoke3", "SensorSmoke4"}
BUTTON_TYPES = {"SmartButton"}

MULTI_SWITCH_TYPES = {
    "Switch1", "Switch2", "Switch3", "Switch4", "Switch5", "Switch6",
    "ZigbeeSwitch1", "ZigbeeSwitch2", "ZigbeeSwitch3",
}
SINGLE_SWITCH_TYPES = {
    "Relay", "RelayController", "RelayController2", "RelayControllerDc", "RelayControllerDc2",
    "Plug", "SmartPlug", "BruntPlug",
}
POWER_STRIP_TYPES = {"PowerStrip", "PowerStrip2"}

BATTERY_TYPES = (
    MOTION_TYPES | CONTACT_TYPES | TEMP_HUMIDITY_TYPES | LEAK_TYPES | SMOKE_TYPES | BUTTON_TYPES
)

# Vendor UI contract for LightWw*: temperature 0..100 maps to 3000..6500 K.
WHITE_MIN_KELVIN = 3000
WHITE_MAX_KELVIN = 6500

EVENT_BUTTON = f"{DOMAIN}_button"
