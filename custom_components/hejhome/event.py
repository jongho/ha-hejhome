"""Smart button presses.

The upstream homebridge plugin never confirmed which data point the SmartButton
reports, so this maps common Tuya values (single_click / double_click / long_press,
click / double_click / press ...) and passes anything else through as ``unknown``
with the raw report attached. Multi-key models get one entity per key; the key
number is read from Tuya codes such as ``switch1_value`` or ``switch_mode2``.
Every report is also fired on the bus as ``hejhome_button`` so it can be
inspected in Developer tools -> Events.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HejhomeConfigEntry
from .const import EVENT_BUTTON, button_count, button_index, is_button
from .coordinator import signal_report
from .entity import HejhomeEntity

_IGNORED_CODES = {"battery", "battery_state", "battery_percentage", "battery_value", "va_battery"}
EVENT_TYPES = ["single", "double", "long", "unknown"]


def _kind(value: Any) -> str:
    value = str(value).lower()
    if "double" in value:
        return "double"
    if "long" in value or "hold" in value or value == "press":
        return "long"
    if "single" in value or value in ("click", "0"):
        return "single"
    if value == "1":
        return "double"
    if value == "2":
        return "long"
    return "unknown"


def presses(raw: list[dict[str, Any]]) -> list[tuple[int | None, str, dict[str, Any]]]:
    """Every press in a report as (key number or None, press type, attributes)."""
    result = []
    for item in raw:
        code = str(item.get("code"))
        if code in _IGNORED_CODES:
            continue
        index = button_index(code)
        attrs: dict[str, Any] = {"code": code, "value": item.get("value")}
        if index is not None:
            attrs["button"] = index
        result.append((index, _kind(item.get("value")), attrs))
    return result


def classify(raw: list[dict[str, Any]]) -> tuple[str, dict[str, Any]] | None:
    """Pick the press type from a report, or None if the report is not a press."""
    found = presses(raw)
    if not found:
        return None
    _, kind, attrs = found[0]
    return kind, attrs


async def async_setup_entry(
    hass: HomeAssistant, entry: HejhomeConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    entities: list[HejhomeButtonEvent] = []
    for device_id, device in coordinator.data.items():
        if not is_button(str(device.get("deviceType") or "")):
            continue
        count = button_count(device)
        if count == 1:
            entities.append(HejhomeButtonEvent(coordinator, device_id, None))
        else:
            entities.extend(HejhomeButtonEvent(coordinator, device_id, index) for index in range(1, count + 1))
    async_add_entities(entities)


class HejhomeButtonEvent(HejhomeEntity, EventEntity):
    _attr_device_class = EventDeviceClass.BUTTON
    _attr_event_types = EVENT_TYPES

    def __init__(self, coordinator, device_id: str, index: int | None) -> None:
        super().__init__(coordinator, device_id, "button" if index is None else f"button_{index}")
        self._index = index
        if index is None:
            self._attr_translation_key = "button"
        else:
            self._attr_translation_key = "button_n"
            self._attr_translation_placeholders = {"index": str(index)}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_report(self.coordinator.config_entry.entry_id, self.device_id), self._handle_report
            )
        )

    @callback
    def _handle_report(self, raw: list[dict[str, Any]]) -> None:
        if self._index in (None, 1):
            # One bus event per report, carrying the raw data points.
            self.hass.bus.async_fire(
                EVENT_BUTTON, {"device_id": self.device_id, "entity_id": self.entity_id, "status": raw}
            )
        for index, kind, attrs in presses(raw):
            # Presses without a key number land on the single / first key.
            if self._index is None or index == self._index or (index is None and self._index == 1):
                self._trigger_event(kind, attrs)
                self.async_write_ha_state()
