"""Smart button presses.

The upstream homebridge plugin never confirmed which data point the SmartButton
reports, so this maps common Tuya values (single_click / double_click / long_press,
click / double_click / press ...) and passes anything else through as ``unknown``
with the raw report attached. Every report is also fired on the bus as
``hejhome_button`` so it can be inspected in Developer tools -> Events.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HejhomeConfigEntry
from .const import BUTTON_TYPES, EVENT_BUTTON
from .coordinator import signal_report
from .entity import HejhomeEntity

_IGNORED_CODES = {"battery", "battery_state", "battery_percentage", "battery_value"}
EVENT_TYPES = ["single", "double", "long", "unknown"]


def classify(raw: list[dict[str, Any]]) -> tuple[str, dict[str, Any]] | None:
    """Pick the press type from a report, or None if the report is not a press."""
    for item in raw:
        code = str(item.get("code"))
        if code in _IGNORED_CODES:
            continue
        value = str(item.get("value")).lower()
        if "double" in value:
            kind = "double"
        elif "long" in value or "hold" in value or value == "press":
            kind = "long"
        elif "single" in value or value in ("click", "0"):
            kind = "single"
        elif value == "1":
            kind = "double"
        elif value == "2":
            kind = "long"
        else:
            kind = "unknown"
        return kind, {"code": code, "value": item.get("value")}
    return None


async def async_setup_entry(
    hass: HomeAssistant, entry: HejhomeConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        HejhomeButtonEvent(coordinator, device_id)
        for device_id, device in coordinator.data.items()
        if device.get("deviceType") in BUTTON_TYPES
    )


class HejhomeButtonEvent(HejhomeEntity, EventEntity):
    _attr_device_class = EventDeviceClass.BUTTON
    _attr_event_types = EVENT_TYPES
    _attr_translation_key = "button"

    def __init__(self, coordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id, "button")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_report(self.coordinator.config_entry.entry_id, self.device_id), self._handle_report
            )
        )

    @callback
    def _handle_report(self, raw: list[dict[str, Any]]) -> None:
        self.hass.bus.async_fire(EVENT_BUTTON, {"device_id": self.device_id, "entity_id": self.entity_id, "status": raw})
        result = classify(raw)
        if result is None:
            return
        kind, attrs = result
        self._trigger_event(kind, attrs)
        self.async_write_ha_state()
