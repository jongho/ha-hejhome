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
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HejhomeConfigEntry
from .const import DOMAIN, EVENT_BUTTON, button_index, is_button
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
        count = coordinator.button_count(device_id)
        if count == 1:
            entities.append(HejhomeButtonEvent(coordinator, device_id, None))
        else:
            entities.extend(HejhomeButtonEvent(coordinator, device_id, index) for index in range(1, count + 1))
    _remove_stale(hass, entry, {entity.device_id for entity in entities}, {entity.unique_id for entity in entities})
    async_add_entities(entities)


def _remove_stale(
    hass: HomeAssistant, entry: HejhomeConfigEntry, device_ids: set[str], keep: set[str | None]
) -> None:
    """Drop key entities of the current buttons whose key layout no longer exists.

    A button first set up as a single key (``<id>_button``) and later known to
    have four keys (``<id>_button_1`` ...) would otherwise leave a dead entity.
    Devices missing from this snapshot are left alone.
    """
    registry = er.async_get(hass)
    for reg in er.async_entries_for_config_entry(registry, entry.entry_id):
        if reg.domain != "event" or reg.platform != DOMAIN or reg.unique_id in keep:
            continue
        if any(reg.unique_id == f"{d}_button" or reg.unique_id.startswith(f"{d}_button_") for d in device_ids):
            registry.async_remove(reg.entity_id)


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

    def _owns_report(self, raw: list[dict[str, Any]]) -> bool:
        """Whether this key entity announces the report on the bus.

        Exactly one entity per device does: the key that was pressed, or the
        single / first key for reports without a known key number (battery,
        a key above the current count while the entry reloads).
        """
        if self._index is None:
            return True
        found = [index for index, _, _ in presses(raw) if index is not None]
        key = found[0] if found and found[0] <= self.coordinator.button_count(self.device_id) else 1
        return self._index == key

    @callback
    def _handle_report(self, raw: list[dict[str, Any]]) -> None:
        if self._owns_report(raw):
            # One bus event per report, carrying the raw data points and the
            # entity of the key that was pressed.
            self.hass.bus.async_fire(
                EVENT_BUTTON, {"device_id": self.device_id, "entity_id": self.entity_id, "status": raw}
            )
        for index, kind, attrs in presses(raw):
            # Presses without a key number land on the single / first key.
            if self._index is None or index == self._index or (index is None and self._index == 1):
                self._trigger_event(kind, attrs)
                self.async_write_ha_state()
