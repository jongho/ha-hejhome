"""Hejhome bulbs and LED strips."""
from __future__ import annotations

from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_HS_COLOR,
    ATTR_WHITE,
    ColorMode,
    LightEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HejhomeConfigEntry
from .const import COLOR_LIGHT_TYPES, WHITE_LIGHT_TYPES, WHITE_MAX_KELVIN, WHITE_MIN_KELVIN
from .entity import HejhomeEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: HejhomeConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    entities: list[LightEntity] = []
    for device_id, device in coordinator.data.items():
        if device.get("deviceType") in COLOR_LIGHT_TYPES:
            entities.append(HejhomeColorLight(coordinator, device_id))
        elif device.get("deviceType") in WHITE_LIGHT_TYPES:
            entities.append(HejhomeWhiteLight(coordinator, device_id))
    async_add_entities(entities)


def _is_on(value: Any) -> bool | None:
    if value in (True, "켜짐", "on", "ON", 1):
        return True
    if value in (False, "꺼짐", "off", "OFF", 0):
        return False
    return None


def _pct(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if 0 <= value <= 100 else None


def to_vendor_brightness(ha_brightness: int) -> int:
    return max(1, min(100, round(ha_brightness * 100 / 255)))


def to_ha_brightness(pct: float) -> int:
    return max(1, min(255, round(pct * 255 / 100))) if pct > 0 else 0


class _HejhomeLightBase(HejhomeEntity, LightEntity):
    _attr_name = None

    @property
    def is_on(self) -> bool | None:
        return _is_on(self.state_data.get("power"))

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_control(self.device_id, {"power": False})


class HejhomeWhiteLight(_HejhomeLightBase):
    """LightWw*: brightness + colour temperature (3000-6500 K)."""

    _attr_color_mode = ColorMode.COLOR_TEMP
    _attr_supported_color_modes = {ColorMode.COLOR_TEMP}
    _attr_min_color_temp_kelvin = WHITE_MIN_KELVIN
    _attr_max_color_temp_kelvin = WHITE_MAX_KELVIN

    @property
    def brightness(self) -> int | None:
        pct = _pct(self.state_data.get("brightness"))
        return None if pct is None else to_ha_brightness(pct)

    @property
    def color_temp_kelvin(self) -> int | None:
        pct = _pct(self.state_data.get("temperature"))
        return None if pct is None else round(WHITE_MIN_KELVIN + pct * 35)

    async def async_turn_on(self, **kwargs: Any) -> None:
        req: dict[str, Any] = {}
        if not self.is_on:
            req["power"] = True
        if ATTR_BRIGHTNESS in kwargs:
            req["brightness"] = to_vendor_brightness(kwargs[ATTR_BRIGHTNESS])
        if ATTR_COLOR_TEMP_KELVIN in kwargs:
            kelvin = min(WHITE_MAX_KELVIN, max(WHITE_MIN_KELVIN, kwargs[ATTR_COLOR_TEMP_KELVIN]))
            req["temperature"] = round((kelvin - WHITE_MIN_KELVIN) / 35)
        await self.coordinator.async_control(self.device_id, req or {"power": True})


class HejhomeColorLight(_HejhomeLightBase):
    """LightRgb*/LedStrip*: HS colour mode, or the white channel."""

    _attr_supported_color_modes = {ColorMode.HS, ColorMode.WHITE}

    @property
    def _white(self) -> bool:
        return str(self.state_data.get("lightMode") or "").upper() == "WHITE"

    @property
    def color_mode(self) -> ColorMode:
        return ColorMode.WHITE if self._white else ColorMode.HS

    @property
    def _hsv(self) -> dict[str, Any]:
        hsv = self.state_data.get("hsvColor")
        return hsv if isinstance(hsv, dict) else {}

    @property
    def brightness(self) -> int | None:
        raw = self.state_data.get("brightness") if self._white else self._hsv.get("brightness", self.state_data.get("brightness"))
        pct = _pct(raw)
        return None if pct is None else to_ha_brightness(pct)

    @property
    def hs_color(self) -> tuple[float, float] | None:
        hue, sat = self._hsv.get("hue"), _pct(self._hsv.get("saturation"))
        if isinstance(hue, (int, float)) and sat is not None:
            return float(hue), sat
        return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        control = self.coordinator.async_control
        if not self.is_on:
            await control(self.device_id, {"power": True})

        if ATTR_WHITE in kwargs:
            if not self._white:
                await control(self.device_id, {"lightMode": "white"})
            await control(self.device_id, {"brightness": to_vendor_brightness(kwargs[ATTR_WHITE])})
            return

        if ATTR_HS_COLOR in kwargs:
            hue, sat = kwargs[ATTR_HS_COLOR]
            if self._white or str(self.state_data.get("lightMode") or "").upper() == "SCENE":
                await control(self.device_id, {"lightMode": "colour"})
            bri = kwargs.get(ATTR_BRIGHTNESS, self.brightness or 255)
            await control(
                self.device_id,
                {"hsvColor": {"hue": round(hue), "saturation": max(1, round(sat)), "brightness": to_vendor_brightness(bri)}},
            )
            return

        if ATTR_BRIGHTNESS in kwargs:
            bri = to_vendor_brightness(kwargs[ATTR_BRIGHTNESS])
            hs = self.hs_color
            if self._white or hs is None:
                await control(self.device_id, {"brightness": bri})
            else:
                await control(
                    self.device_id,
                    {"hsvColor": {"hue": round(hs[0]), "saturation": round(hs[1]), "brightness": bri}},
                )
