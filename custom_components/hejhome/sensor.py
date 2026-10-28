"""Temperature, humidity and battery sensors."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HejhomeConfigEntry
from .const import BATTERY_TYPES, TEMP_HUMIDITY_TYPES
from .entity import HejhomeEntity


@dataclass(frozen=True, kw_only=True)
class HejSensorDescription(SensorEntityDescription):
    device_types: set[str]
    state_key: str
    minimum: float
    maximum: float


DESCRIPTIONS = (
    HejSensorDescription(
        key="temperature",
        state_key="temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_types=TEMP_HUMIDITY_TYPES,
        minimum=-40,
        maximum=100,
    ),
    HejSensorDescription(
        key="humidity",
        state_key="humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        device_types=TEMP_HUMIDITY_TYPES,
        minimum=0,
        maximum=100,
    ),
    HejSensorDescription(
        key="battery",
        state_key="battery",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        device_types=BATTERY_TYPES,
        minimum=0,
        maximum=100,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: HejhomeConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        HejhomeSensor(coordinator, device_id, description)
        for device_id, device in coordinator.data.items()
        for description in DESCRIPTIONS
        if device.get("deviceType") in description.device_types
    )


class HejhomeSensor(HejhomeEntity, SensorEntity):
    entity_description: HejSensorDescription

    def __init__(self, coordinator, device_id: str, description: HejSensorDescription) -> None:
        super().__init__(coordinator, device_id, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | None:
        value: Any = self.state_data.get(self.entity_description.state_key)
        if isinstance(value, bool):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        d = self.entity_description
        if not d.minimum <= number <= d.maximum:
            return None
        return int(number) if number.is_integer() and d.key != "temperature" else number
