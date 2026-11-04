"""Constants for the Hejhome integration."""
from __future__ import annotations

import re

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
# Raw realtime reports of devices no platform handles, for identifying new models.
EVENT_REPORT = f"{DOMAIN}_report"

# Realtime temperature/humidity samples outrank the REST snapshot for this long.
# Hejhome documents a 30-minute measurement cycle; allow three cycles.
MEASUREMENT_FRESHNESS_SECONDS = 90 * 60
# Any other reported field outranks a REST snapshot fetched right after it.
REPORT_FRESHNESS_SECONDS = 5
# Transient REST failures tolerated before entities become unavailable.
MAX_SILENT_POLL_FAILURES = 3

_COLOR_LIGHT_RE = re.compile(r"(light|led|bulb|lamp|strip).*rgb|rgb.*(light|led|bulb|lamp|strip)", re.I)
_WHITE_LIGHT_RE = re.compile(r"^(light|bulb|lamp)(ww|cw|cct|white)", re.I)
_BUTTON_RE = re.compile(r"button|scene", re.I)
_TRAILING_NUMBER_RE = re.compile(r"(\d+)$")


def is_color_light(device_type: str) -> bool:
    """Known RGB lights, plus newer models following the same naming (e.g. LightRgbw6)."""
    return device_type in COLOR_LIGHT_TYPES or (
        not device_type.startswith("Ir") and bool(_COLOR_LIGHT_RE.search(device_type))
    )


def is_white_light(device_type: str) -> bool:
    return device_type in WHITE_LIGHT_TYPES or (
        not is_color_light(device_type) and bool(_WHITE_LIGHT_RE.search(device_type))
    )


def is_button(device_type: str) -> bool:
    """SmartButton and multi-key scene buttons (e.g. a 4-key model)."""
    return device_type in BUTTON_TYPES or (
        not device_type.startswith("Ir") and bool(_BUTTON_RE.search(device_type))
    )


def button_count(device: dict) -> int:
    """Number of keys: trailing digit of the type, else the highest key seen in its state."""
    match = _TRAILING_NUMBER_RE.search(str(device.get("deviceType") or ""))
    count = int(match.group(1)) if match else 1
    for key in (device.get("deviceState") or {}):
        index = button_index(str(key))
        if index:
            count = max(count, index)
    return max(1, min(count, 8))


_BUTTON_INDEX_RES = (
    re.compile(r"^switch(\d)_value$"),
    re.compile(r"^switch_mode(\d)$"),
    re.compile(r"^switch_type_(\d)$"),
    re.compile(r"^(?:button|key|scene)_?(\d)(?:_\w+)?$", re.I),
)


def button_index(code: str) -> int | None:
    """Key number encoded in a Tuya button data-point code (switch1_value, key_2 ...)."""
    for regex in _BUTTON_INDEX_RES:
        match = regex.match(code)
        if match:
            return int(match.group(1))
    return None


def is_supported(device: dict) -> bool:
    """Whether any platform creates an entity for this device."""
    device_type = str(device.get("deviceType") or "")
    return (
        is_color_light(device_type)
        or is_white_light(device_type)
        or is_button(device_type)
        or device_type in BATTERY_TYPES
        or device_type in MULTI_SWITCH_TYPES
        or device_type in SINGLE_SWITCH_TYPES
        or device_type in POWER_STRIP_TYPES
    )
