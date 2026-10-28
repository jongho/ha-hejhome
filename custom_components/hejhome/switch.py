"""Wall switches, relays, plugs and power strips."""
from __future__ import annotations

import re
from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HejhomeConfigEntry
from .const import MULTI_SWITCH_TYPES, POWER_STRIP_TYPES, SINGLE_SWITCH_TYPES
from .entity import HejhomeEntity

_PLUG_TYPES = {"Plug", "SmartPlug", "BruntPlug"}


def _count_power_keys(state: dict[str, Any]) -> int:
    return len([k for k in state if re.fullmatch(r"power\d+", k)]) or 1


def power_keys(device: dict[str, Any]) -> list[str]:
    device_type = str(device.get("deviceType"))
    state = device.get("deviceState") or {}
    if device_type in MULTI_SWITCH_TYPES:
        match = re.search(r"Switch(\d+)", device_type)
        count = int(match.group(1)) if match else _count_power_keys(state)
        return [f"power{i}" for i in range(1, max(1, count) + 1)]
    if device_type in POWER_STRIP_TYPES:
        count = max(4, _count_power_keys(state))
        return [f"power{i}" for i in range(1, count + 1) if i != 5]
    if device_type in SINGLE_SWITCH_TYPES:
        return ["power" if "power" in state or device_type in _PLUG_TYPES else "power1"]
    return []


async def async_setup_entry(
    hass: HomeAssistant, entry: HejhomeConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    entities: list[HejhomeSwitch] = []
    for device_id, device in coordinator.data.items():
        keys = power_keys(device)
        for key in keys:
            entities.append(HejhomeSwitch(coordinator, device_id, key, len(keys) > 1))
    async_add_entities(entities)


class HejhomeSwitch(HejhomeEntity, SwitchEntity):
    def __init__(self, coordinator, device_id: str, power_key: str, numbered: bool) -> None:
        super().__init__(coordinator, device_id, power_key)
        self._power_key = power_key
        if numbered:
            self._attr_translation_key = "gang"
            self._attr_translation_placeholders = {"index": power_key.removeprefix("power")}
        else:
            self._attr_name = None
        if self.device.get("deviceType") in _PLUG_TYPES | POWER_STRIP_TYPES:
            self._attr_device_class = SwitchDeviceClass.OUTLET

    @property
    def is_on(self) -> bool | None:
        value = self.state_data.get(self._power_key)
        if value in (True, "켜짐"):
            return True
        if value in (False, "꺼짐"):
            return False
        return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_control(self.device_id, {self._power_key: True})

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_control(self.device_id, {self._power_key: False})
