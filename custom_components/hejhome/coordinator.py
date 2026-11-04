"""Data coordinator: REST snapshot + MQTT push, session refresh."""
from __future__ import annotations

import copy
import logging
import time
from collections import deque
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import HejAuth, HejAuthError, HejClient, HejConnectionError, HejError, HejSession
from .const import (
    CONF_BUTTON_COUNTS,
    CONF_FAMILY_ID,
    CONF_SESSION,
    DOMAIN,
    EVENT_REPORT,
    MAX_SILENT_POLL_FAILURES,
    MEASUREMENT_FRESHNESS_SECONDS,
    POLL_INTERVAL_SECONDS,
    REPORT_FRESHNESS_SECONDS,
    button_count,
    button_index,
    is_button,
    is_supported,
)
from .realtime import HejRealtime

_LOGGER = logging.getLogger(__name__)

Devices = dict[str, dict[str, Any]]

# Periodic environment samples: a realtime value stays authoritative for a while.
MEASUREMENT_KEYS = frozenset({"temperature", "humidity"})
# Fields a sleepy sensor's REST snapshot may report as null; keep the last value.
STICKY_KEYS = frozenset({"temperature", "humidity", "battery"})


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
        self._poll_failures = 0
        # device id -> field -> (value, monotonic time received over MQTT)
        self._reported: dict[str, dict[str, tuple[Any, float]]] = {}
        # Last raw reports per device, for diagnostics.
        self.recent_reports: dict[str, deque[dict[str, Any]]] = {}

    def log_unsupported(self) -> None:
        for device_id, device in (self.data or {}).items():
            if not is_supported(device):
                _LOGGER.warning(
                    "Hejhome device '%s' (deviceType %s, state keys %s) is not supported yet; "
                    "download the integration diagnostics and open an issue to add it",
                    device.get("name"), device.get("deviceType"),
                    sorted((device.get("deviceState") or {}).keys()),
                )

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
        try:
            devices = await self._fetch_devices()
        except UpdateFailed as err:
            self._poll_failures += 1
            # One slow or failed poll must not flag every entity unavailable;
            # realtime reports keep arriving over MQTT in the meantime.
            if self.data is not None and self._poll_failures < MAX_SILENT_POLL_FAILURES:
                _LOGGER.warning(
                    "Hejhome refresh failed (%s/%s), keeping last state: %s",
                    self._poll_failures, MAX_SILENT_POLL_FAILURES, err,
                )
                return self.data
            raise
        self._poll_failures = 0
        fresh = {str(d["id"]): d for d in devices if isinstance(d, dict) and d.get("id")}
        return {device_id: self._merge_snapshot(device_id, device) for device_id, device in fresh.items()}

    async def _fetch_devices(self) -> list[dict[str, Any]]:
        if self.client.session.expired:
            await self._relogin()
        try:
            return await self.client.get_devices(self.family_id)
        except HejAuthError:
            await self._relogin()
            return await self._fetch_or_fail()
        except (HejConnectionError, HejError) as err:
            raise UpdateFailed(str(err)) from err

    def _merge_snapshot(self, device_id: str, device: dict[str, Any]) -> dict[str, Any]:
        """Combine a REST snapshot with what is already known about the device.

        The snapshot can lag behind realtime reports and battery sensors may come
        back with null measurements or ``online: false`` while asleep.
        """
        previous = (self.data or {}).get(device_id) or {}
        prev_state = previous.get("deviceState") or {}
        rest_state = device.get("deviceState") or {}
        state = {**prev_state, **rest_state}
        for key in STICKY_KEYS:
            if rest_state.get(key) is None and prev_state.get(key) is not None:
                state[key] = prev_state[key]

        now = time.monotonic()
        reported = self._reported.get(device_id, {})
        latest = 0.0
        for key, (value, at) in reported.items():
            age = now - at
            latest = max(latest, at)
            limit = MEASUREMENT_FRESHNESS_SECONDS if key in MEASUREMENT_KEYS else REPORT_FRESHNESS_SECONDS
            if age <= limit:
                state[key] = value
        merged = {**device, "deviceState": state}
        # Battery sensors sleep between samples and the cloud may list them as
        # offline; one that reported over MQTT recently is evidently alive.
        if (
            merged.get("online") is False
            and "battery" in state
            and reported
            and now - latest <= MEASUREMENT_FRESHNESS_SECONDS
        ):
            merged["online"] = True
        return merged

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
            self._push({**self.data, device_id: merge_state(self.data[device_id], requirements)})

    @callback
    def _push(self, data: Devices) -> None:
        """Publish pushed state without postponing the next REST poll.

        ``async_set_updated_data`` would reschedule the poll on every call, so a
        house with chatty devices would never poll at all.
        """
        self.data = data
        self.async_update_listeners()

    # ---- multi-key buttons ---------------------------------------------

    def learned_button_count(self, device_id: str) -> int:
        return int((self.config_entry.data.get(CONF_BUTTON_COUNTS) or {}).get(device_id, 0))

    def button_count(self, device_id: str) -> int:
        return button_count(self.data.get(device_id) or {}, self.learned_button_count(device_id))

    @callback
    def _learn_button_keys(self, device_id: str, raw: list[dict[str, Any]]) -> None:
        """Remember a key number above the known count and reload to add entities.

        Models like the SmartButton report no key count in their type, and their
        REST state can be empty after a restart; one press of key 4 is proof.
        """
        if not is_button(str(self.data[device_id].get("deviceType") or "")):
            return
        seen = max((button_index(str(item.get("code"))) or 0 for item in raw), default=0)
        if seen <= self.button_count(device_id):
            return
        counts = dict(self.config_entry.data.get(CONF_BUTTON_COUNTS) or {})
        counts[device_id] = seen
        _LOGGER.info("Hejhome button %s has at least %s keys; reloading to add them", device_id, seen)
        self.hass.config_entries.async_update_entry(
            self.config_entry, data={**self.config_entry.data, CONF_BUTTON_COUNTS: counts}
        )
        self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)

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
        self.recent_reports.setdefault(device_id, deque(maxlen=10)).append(
            {"at": time.time(), "status": raw}
        )
        async_dispatcher_send(self.hass, signal_report(self.config_entry.entry_id, device_id), raw)
        if not self.data or device_id not in self.data:
            return
        self._learn_button_keys(device_id, raw)
        if not is_supported(self.data[device_id]):
            # Lets users see what an unsupported model sends (Developer tools -> Events).
            self.hass.bus.async_fire(
                EVENT_REPORT,
                {
                    "device_id": device_id,
                    "name": self.data[device_id].get("name"),
                    "device_type": self.data[device_id].get("deviceType"),
                    "status": raw,
                },
            )
        now = time.monotonic()
        fields = self._reported.setdefault(device_id, {})
        merged = merge_state({"deviceState": {}}, patch)["deviceState"]
        for key, value in merged.items():
            if value is not None:
                fields[key] = (value, now)
        device = merge_state(self.data[device_id], patch)
        device["online"] = True
        self._push({**self.data, device_id: device})
