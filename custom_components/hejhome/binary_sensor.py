"""Motion, door, leak and smoke sensors."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HejhomeConfigEntry
from .const import CONTACT_TYPES, LEAK_TYPES, MOTION_TYPES, SMOKE_TYPES
from .entity import HejhomeEntity


@dataclass(frozen=True, kw_only=True)
class HejBinaryDescription(BinarySensorEntityDescription):
    device_types: set[str]
    value_fn: Callable[[dict[str, Any]], bool | None]


def _motion(state: dict[str, Any]) -> bool | None:
    value = state.get("motionDetected")
    return value if isinstance(value, bool) else None


def _contact(state: dict[str, Any]) -> bool | None:
    if state.get("doorOpened") is True or str(state.get("state") or "").upper() == "OPEN":
        return True
    if state.get("doorOpened") is False or str(state.get("state") or "").upper() == "CLOSED":
        return False
    return None


def _alarm(state: dict[str, Any]) -> bool | None:
    value = state.get("alarm")
    return bool(value) if value is not None else None


DESCRIPTIONS = (
    HejBinaryDescription(
        key="motion", device_class=BinarySensorDeviceClass.MOTION, device_types=MOTION_TYPES, value_fn=_motion
    ),
    HejBinaryDescription(
        key="door", device_class=BinarySensorDeviceClass.DOOR, device_types=CONTACT_TYPES, value_fn=_contact
    ),
    HejBinaryDescription(
        key="leak", device_class=BinarySensorDeviceClass.MOISTURE, device_types=LEAK_TYPES, value_fn=_alarm
    ),
    HejBinaryDescription(
        key="smoke", device_class=BinarySensorDeviceClass.SMOKE, device_types=SMOKE_TYPES, value_fn=_alarm
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: HejhomeConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        HejhomeBinarySensor(coordinator, device_id, description)
        for device_id, device in coordinator.data.items()
        for description in DESCRIPTIONS
        if device.get("deviceType") in description.device_types
    )


class HejhomeBinarySensor(HejhomeEntity, BinarySensorEntity):
    entity_description: HejBinaryDescription

    def __init__(self, coordinator, device_id: str, description: HejBinaryDescription) -> None:
        super().__init__(coordinator, device_id, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self.state_data)
