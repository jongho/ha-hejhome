"""Data coordinator: REST snapshot + MQTT push, session refresh."""
from __future__ import annotations

import copy
import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import HejAuth, HejAuthError, HejClient, HejConnectionError, HejError, HejSession
from .const import CONF_FAMILY_ID, CONF_SESSION, DOMAIN, POLL_INTERVAL_SECONDS
from .realtime import HejRealtime

_LOGGER = logging.getLogger(__name__)

Devices = dict[str, dict[str, Any]]


def signal_report(entry_id: str, device_id: str) -> str:
    return f"{DOMAIN}_{entry_id}_report_{device_id}"


def merge_state(device: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Merge a state patch into a device, normalising light mode / HSV brightness."""
    merged = copy.deepcopy(device)
    state = dict(merged.get("deviceState") or {})
    state.update(patch)
    hsv = patch.get("hsvColor")
    if isinstance(hsv, dict) and isinstance(hsv.get("brightness"), (int, float)):
        state["brightness"] = hsv["brightness"]
    if patch.get("lightMode") == "colour":
        state["lightMode"] = "COLOR"
    elif patch.get("lightMode") == "white":
        state["lightMode"] = "WHITE"
    merged["deviceState"] = state
    return merged


class HejhomeCoordinator(DataUpdateCoordinator[Devices]):
    """Keeps every device of one Hejhome family up to date."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, auth: HejAuth, client: HejClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=POLL_INTERVAL_SECONDS),
        )
        self._auth = auth
        self.client = client
        self.family_id: int = int(entry.data[CONF_FAMILY_ID])
        self.realtime_connected = False
        self.rooms: dict[Any, str] = {}
        self._realtime: HejRealtime | None = None

    async def async_load_rooms(self) -> None:
        """Room names become suggested areas; failure here is not fatal."""
        try:
            rooms = await self.client.get_rooms(self.family_id)
        except HejError as err:
            _LOGGER.debug("Could not load Hejhome rooms: %s", err)
            return
        self.rooms = {r.get("room_id"): str(r.get("name")) for r in rooms if isinstance(r, dict) and r.get("name")}

    # ---- polling -------------------------------------------------------

    async def _async_update_data(self) -> Devices:
        if self.client.session.expired:
            await self._relogin()
        try:
            devices = await self.client.get_devices(self.family_id)
        except HejAuthError:
            await self._relogin()
            devices = await self._fetch_or_fail()
        except (HejConnectionError, HejError) as err:
            raise UpdateFailed(str(err)) from err
        return {str(d["id"]): d for d in devices if isinstance(d, dict) and d.get("id")}

    async def _fetch_or_fail(self) -> list[dict[str, Any]]:
        try:
            return await self.client.get_devices(self.family_id)
        except HejAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except HejError as err:
            raise UpdateFailed(str(err)) from err

    async def _relogin(self) -> None:
        """Try a silent password login; fall back to the reauth flow."""
        data = self.config_entry.data
        try:
            session = await self._auth.login(data[CONF_EMAIL], data[CONF_PASSWORD])
        except HejAuthError as err:
            raise ConfigEntryAuthFailed("Hejhome session expired; please re-authenticate") from err
        except HejError as err:
            raise UpdateFailed(str(err)) from err
        self._set_session(session)

    def _set_session(self, session: HejSession) -> None:
        self.client.session = session
        self.hass.config_entries.async_update_entry(
            self.config_entry, data={**self.config_entry.data, CONF_SESSION: session.to_dict()}
        )

    # ---- control -------------------------------------------------------

    async def async_control(self, device_id: str, requirements: dict[str, Any]) -> None:
        try:
            await self.client.control(device_id, requirements)
        except HejAuthError:
            await self._relogin()
            try:
                await self.client.control(device_id, requirements)
            except HejError as err:
                raise HomeAssistantError(f"Hejhome control failed: {err}") from err
        except HejError as err:
            raise HomeAssistantError(f"Hejhome control failed: {err}") from err
        # Optimistic update; the MQTT echo will confirm or correct it.
        if self.data and device_id in self.data:
            self.async_set_updated_data({**self.data, device_id: merge_state(self.data[device_id], requirements)})

    # ---- realtime ------------------------------------------------------

    async def async_start_realtime(self) -> None:
        self._realtime = HejRealtime(
            self.client.session.mqtt_topic, self._report_threadsafe, self._connected_threadsafe
        )
        await self.hass.async_add_executor_job(self._realtime.start)

    async def async_stop_realtime(self) -> None:
        if self._realtime is not None:
            await self.hass.async_add_executor_job(self._realtime.stop)
            self._realtime = None

    def _report_threadsafe(self, device_id: str, patch: dict[str, Any], raw: list[dict[str, Any]]) -> None:
        self.hass.loop.call_soon_threadsafe(self.async_handle_report, device_id, patch, raw)

    def _connected_threadsafe(self, connected: bool) -> None:
        self.hass.loop.call_soon_threadsafe(self._set_connected, connected)

    @callback
    def _set_connected(self, connected: bool) -> None:
        self.realtime_connected = connected

    @callback
    def async_handle_report(self, device_id: str, patch: dict[str, Any], raw: list[dict[str, Any]]) -> None:
        async_dispatcher_send(self.hass, signal_report(self.config_entry.entry_id, device_id), raw)
        if not self.data or device_id not in self.data:
            return
        device = merge_state(self.data[device_id], patch)
        device["online"] = True
        self.async_set_updated_data({**self.data, device_id: device})
