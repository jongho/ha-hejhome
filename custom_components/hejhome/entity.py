"""Base entity."""
from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import HejhomeCoordinator


class HejhomeEntity(CoordinatorEntity[HejhomeCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: HejhomeCoordinator, device_id: str, key: str | None = None) -> None:
        super().__init__(coordinator)
        self.device_id = device_id
        self._attr_unique_id = device_id if key is None else f"{device_id}_{key}"
        device = coordinator.data[device_id]
        room = coordinator.rooms.get(device.get("roomId"))
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=device.get("name") or device_id,
            manufacturer="Hejhome (GOQUAL)",
            model=device.get("modelName") or device.get("deviceType"),
            suggested_area=room,
        )

    @property
    def device(self) -> dict[str, Any]:
        return self.coordinator.data.get(self.device_id, {})

    @property
    def state_data(self) -> dict[str, Any]:
        return self.device.get("deviceState") or {}

    @property
    def available(self) -> bool:
        return super().available and self.device_id in self.coordinator.data and self.device.get("online") is not False
