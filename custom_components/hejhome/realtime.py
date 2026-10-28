"""Realtime device reports over the Hejhome MQTT (WebSocket) broker.

Report parsing is ported from @chazepps/homebridge-hejhome src/hej/realtime.ts.
Reports carry Tuya data-point codes; they are translated into the same
vocabulary the REST ``devices-state`` endpoint uses so both can be merged.
"""
from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from typing import Any

from .api import HEJ_CLIENT_ID, HEJ_CLIENT_SECRET

_LOGGER = logging.getLogger(__name__)

MQTT_HOST = "mqtt.hej.so"
MQTT_PORT = 15675
MQTT_PATH = "/ws"

_CODE_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,127}$")
_SWITCH_N_RE = re.compile(r"^switch_(\d+)$")

ReportCallback = Callable[[str, dict[str, Any], list[dict[str, Any]]], None]


def parse_report(payload: str | bytes) -> tuple[str, dict[str, Any], list[dict[str, Any]]] | None:
    """Return (device_id, state_patch, raw_status) or None for an invalid report."""
    if isinstance(payload, bytes):
        if len(payload) > 256 * 1024:
            return None
        payload = payload.decode("utf-8", "replace")
    try:
        parsed = json.loads(payload)
    except ValueError:
        return None
    report = parsed.get("deviceDataReport") if isinstance(parsed, dict) else None
    if not isinstance(report, dict):
        return None
    dev_id = report.get("devId")
    status = report.get("status")
    if not isinstance(dev_id, str) or not dev_id.strip() or not isinstance(status, list):
        return None
    for item in status:
        if not isinstance(item, dict) or "value" not in item:
            return None
        if not isinstance(item.get("code"), str) or not _CODE_RE.match(item["code"]):
            return None

    patch: dict[str, Any] = {}
    for item in status:
        code, value = item["code"], item["value"]
        if code in ("switch_led", "switch_power"):
            patch["power"] = value
        elif code == "switch":
            power = _bool(value)
            patch["power"] = power
            if power is not None:
                patch["state"] = "OPEN" if power else "CLOSED"
        elif code == "door_opened":
            patch["doorOpened"] = value if isinstance(value, bool) else None
        elif code == "prm_switch":
            patch["state"] = "OPEN" if value else "CLOSED"
        elif code == "switch_usb1":
            patch["power4"] = value
        elif code == "bright_value":
            pct = _light_percent(value, 25)
            if pct is not None:
                patch["brightness"] = pct
        elif code == "temp_value":
            pct = _light_percent(value, 0)
            if pct is not None:
                patch["temperature"] = pct
        elif code == "work_mode":
            mode = str(value or "").lower()
            patch["lightMode"] = {"white": "WHITE", "colour": "COLOR", "color": "COLOR", "scene": "SCENE"}.get(mode)
        elif code == "colour_data":
            hsv = _colour_data(value)
            if hsv:
                patch["hsvColor"] = hsv
        elif code == "pir":
            if value in ("pir", "none"):
                patch["motionDetected"] = value == "pir"
                if value == "pir":
                    patch["lastMotionAt"] = time.time()
        elif code in ("va_temperature", "prm_temperature"):
            num = _num(value)
            if num is not None:
                patch["temperature"] = round(num) / 10
        elif code in ("va_humidity", "prm_content"):
            num = _num(value)
            if num is not None and 0 <= num <= 1000:
                patch["humidity"] = round(num / 10)
        elif code == "battery":
            num = _num(value)
            if num is not None:
                patch["battery"] = num
        elif code == "temp":
            patch["temperature"] = _num(value) if _num(value) is not None else value
        elif code in ("cur_power", "cur_current", "cur_voltage"):
            num = _num(value)
            key = {"cur_power": "curPower", "cur_current": "curCurrent", "cur_voltage": "curVoltage"}[code]
            patch[key] = num if num is not None and num >= 0 else None
        elif code == "alarm_state":
            patch["alarm"] = True if value == "alarm" else _bool(value)
        else:
            match = _SWITCH_N_RE.match(code)
            if match:
                patch[f"power{int(match.group(1))}"] = value
            else:
                patch[code] = value
    return dev_id, patch, status


def _bool(value: Any) -> bool | None:
    if value in (True, "true", 1, "1"):
        return True
    if value in (False, "false", 0, "0"):
        return False
    return None


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value.strip():
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _light_percent(value: Any, offset: int) -> int | None:
    """Realtime light values use a 230-step scale with a 25 offset for brightness/S/V."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value < offset or value > offset + 230:
        return None
    return round((value - offset) * 100 / 230)


def _colour_data(value: Any) -> dict[str, float] | None:
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        return None
    hue = parsed.get("h")
    sat = _light_percent(parsed.get("s"), 25)
    val = _light_percent(parsed.get("v"), 25)
    if not isinstance(hue, (int, float)) or not 0 <= hue <= 360 or sat is None or val is None:
        return None
    return {"hue": hue, "saturation": sat, "brightness": val}


class HejRealtime:
    """paho-mqtt WebSocket client. Callbacks fire on paho's thread."""

    def __init__(self, topic: str, on_report: ReportCallback, on_connected: Callable[[bool], None]) -> None:
        self._topic = topic
        self._on_report = on_report
        self._on_connected = on_connected
        self._client: Any = None

    def start(self) -> None:
        """Blocking setup; run in an executor."""
        import paho.mqtt.client as mqtt

        kwargs: dict[str, Any] = {"transport": "websockets"}
        if hasattr(mqtt, "CallbackAPIVersion"):  # paho-mqtt >= 2.0
            kwargs["callback_api_version"] = mqtt.CallbackAPIVersion.VERSION2
        client = mqtt.Client(**kwargs)
        client.ws_set_options(path=MQTT_PATH)
        client.username_pw_set(HEJ_CLIENT_ID, HEJ_CLIENT_SECRET)
        client.reconnect_delay_set(min_delay=5, max_delay=60)
        client.on_connect = self._handle_connect
        client.on_disconnect = self._handle_disconnect
        client.on_message = self._handle_message
        client.connect_async(MQTT_HOST, MQTT_PORT, keepalive=30)
        client.loop_start()
        self._client = client

    def stop(self) -> None:
        """Blocking teardown; run in an executor."""
        if self._client is not None:
            self._client.disconnect()
            self._client.loop_stop()
            self._client = None

    def _handle_connect(self, client: Any, _userdata: Any, _flags: Any, reason: Any, *_: Any) -> None:
        ok = reason == 0 or getattr(reason, "is_failure", True) is False
        _LOGGER.debug("MQTT connect result %s, subscribing to %s", reason, self._topic)
        if ok:
            client.subscribe(self._topic, qos=1)
        self._on_connected(ok)

    def _handle_disconnect(self, *args: Any) -> None:
        _LOGGER.debug("MQTT disconnected: %s", args[-2:] if len(args) > 2 else args)
        self._on_connected(False)

    def _handle_message(self, _client: Any, _userdata: Any, msg: Any) -> None:
        result = parse_report(msg.payload)
        if result is None:
            _LOGGER.debug("Ignored MQTT payload on %s", msg.topic)
            return
        _LOGGER.debug("Report for %s: %s", result[0], result[2])
        self._on_report(*result)
