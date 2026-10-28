"""Hejhome (헤이홈) integration via the vendor's cloud dashboard API."""
from __future__ import annotations

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import HejAuth, HejClient, HejSession
from .const import CONF_SESSION, PLATFORMS
from .coordinator import HejhomeCoordinator

type HejhomeConfigEntry = ConfigEntry[HejhomeCoordinator]


def create_websession(hass: HomeAssistant) -> aiohttp.ClientSession:
    # Cookies are set by hand on every request; never let a shared jar leak them.
    return async_create_clientsession(hass, cookie_jar=aiohttp.DummyCookieJar())


async def async_setup_entry(hass: HomeAssistant, entry: HejhomeConfigEntry) -> bool:
    websession = create_websession(hass)
    client = HejClient(websession, HejSession.from_dict(entry.data[CONF_SESSION]))
    coordinator = HejhomeCoordinator(hass, entry, HejAuth(websession), client)
    await coordinator.async_config_entry_first_refresh()
    await coordinator.async_load_rooms()

    entry.runtime_data = coordinator
    await coordinator.async_start_realtime()
    entry.async_on_unload(coordinator.async_stop_realtime)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HejhomeConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
