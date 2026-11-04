"""Diagnostics download: device list and recent raw realtime reports.

This is what is needed to add a model that the integration does not map yet
(its deviceType, its REST state keys and the Tuya codes it reports).
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from . import HejhomeConfigEntry
from .const import CONF_SESSION, is_supported

TO_REDACT = {CONF_EMAIL, CONF_PASSWORD, CONF_SESSION}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: HejhomeConfigEntry) -> dict[str, Any]:
    coordinator = entry.runtime_data
    devices = []
    for device_id, device in (coordinator.data or {}).items():
        devices.append(
            {
                "id": device_id,
                "name": device.get("name"),
                "deviceType": device.get("deviceType"),
                "modelName": device.get("modelName"),
                "category": device.get("category"),
                "hasSubDevices": device.get("hasSubDevices"),
                "online": device.get("online"),
                "supported": is_supported(device),
                "deviceState": device.get("deviceState"),
                "recentReports": list(coordinator.recent_reports.get(device_id, [])),
            }
        )
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "realtime_connected": coordinator.realtime_connected,
        "last_update_success": coordinator.last_update_success,
        "devices": devices,
    }
