"""Snapshot/realtime merging, transient failures, multi-key buttons, new models."""
from __future__ import annotations

import copy
import json

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hejhome.const import (
    DOMAIN,
    button_count,
    button_index,
    is_button,
    is_color_light,
    is_supported,
    is_white_light,
)
from custom_components.hejhome.diagnostics import async_get_config_entry_diagnostics
from custom_components.hejhome.realtime import parse_report

from .conftest import DEVICES, ENTRY_DATA, SQ

EXTRA = [
    {"id": "btn4", "name": "4구 버튼", "deviceType": "SmartButton4", "roomId": 10, "online": True,
     "deviceState": {"battery": 95}},
    {"id": "bulb6", "name": "새 전구", "deviceType": "LightRgbw6", "roomId": 11, "online": True,
     "deviceState": {"power": False}},
    {"id": "robot", "name": "로봇", "deviceType": "UnknownRobot", "roomId": 11, "online": True,
     "deviceState": {"x": 1}},
]
URL = f"{SQ}/dashboard/1/devices-state?scope=shop"


def _report(dev, *status):
    return json.dumps({"deviceDataReport": {"devId": dev, "status": [{"code": c, "value": v} for c, v in status]}})


def _eid(hass, platform, unique_id):
    return er.async_get(hass).async_get_entity_id(platform, DOMAIN, unique_id)


def _serve(aioclient_mock, devices=None, exc=None):
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{SQ}/dashboard/rooms/1", json={"result": {"rooms": []}})
    if exc is not None:
        aioclient_mock.get(URL, exc=exc)
    else:
        aioclient_mock.get(URL, json=devices if devices is not None else DEVICES + EXTRA)


async def _setup(hass, aioclient_mock):
    _serve(aioclient_mock)
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, unique_id="me@example.com_1")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def test_type_classifiers():
    assert is_color_light("LightRgbw6") and is_color_light("LedStripRgbw3") and is_color_light("LightRgb4")
    assert not is_color_light("IrLamp") and not is_color_light("LightWw2")
    assert is_white_light("LightWw4") and not is_white_light("LightRgbw6")
    assert is_button("SmartButton") and is_button("SmartButton4") and is_button("SceneButton4")
    assert not is_button("Switch4")
    assert button_count({"deviceType": "SmartButton4"}) == 4
    assert button_count({"deviceType": "SmartButton"}) == 1
    assert button_count({"deviceType": "SceneButton", "deviceState": {"switch3_value": "single_click"}}) == 3
    assert [button_index(c) for c in ("switch2_value", "switch_mode4", "key_3", "battery", "switch_1")] == [
        2, 4, 3, None, None,
    ]
    assert not is_supported({"deviceType": "UnknownRobot"})


def test_parse_newer_thermometer_codes():
    _, patch, _ = parse_report(_report("t", ("temp_current", 218), ("humidity_value", 52), ("battery_percentage", 64)))
    assert patch == {"temperature": 21.8, "humidity": 52, "battery": 64.0}
    _, patch, _ = parse_report(_report("t", ("humidity_value", 523)))
    assert patch == {"humidity": 52}


def test_parse_sensor_th2_hundredths():
    # Real GKZ-TH141 (SensorTh2) reports: hundredths of a degree / percent.
    _, patch, _ = parse_report(_report("t", ("prm_temperature", 2240), ("prm_content", 5740)))
    assert patch == {"temperature": 22.4, "humidity": 57}
    _, patch, _ = parse_report(_report("t", ("prm_temperature", -550)))
    assert patch == {"temperature": -5.5}


def test_parse_drops_implausible_measurements():
    # A wrongly scaled value must not reach the entity or override REST.
    _, patch, _ = parse_report(_report("t", ("va_temperature", 2240), ("va_humidity", 5740)))
    assert patch == {}


async def test_new_models_create_entities(hass: HomeAssistant, aioclient_mock, no_realtime) -> None:
    entry = await _setup(hass, aioclient_mock)
    assert _eid(hass, "light", "bulb6")
    keys = [_eid(hass, "event", f"btn4_button_{i}") for i in range(1, 5)]
    assert all(keys)
    assert _eid(hass, "sensor", "btn4_battery")

    coordinator = entry.runtime_data
    coordinator.async_handle_report("btn4", {}, [{"code": "switch3_value", "value": "long_press"}])
    await hass.async_block_till_done()
    state = hass.states.get(keys[2])
    assert state.attributes["event_type"] == "long" and state.attributes["button"] == 3
    assert hass.states.get(keys[0]).attributes["event_type"] is None

    # Unsupported devices surface their raw reports on the bus.
    raw_events = []
    hass.bus.async_listen("hejhome_report", raw_events.append)
    coordinator.async_handle_report("robot", {"y": 2}, [{"code": "y", "value": 2}])
    await hass.async_block_till_done()
    assert raw_events and raw_events[0].data["device_type"] == "UnknownRobot"

    diag = await async_get_config_entry_diagnostics(hass, entry)
    robot = next(d for d in diag["devices"] if d["id"] == "robot")
    assert robot["supported"] is False and robot["recentReports"][0]["status"] == [{"code": "y", "value": 2}]
    assert diag["entry"]["password"] == "**REDACTED**"


async def test_push_does_not_postpone_poll(hass: HomeAssistant, aioclient_mock, no_realtime) -> None:
    entry = await _setup(hass, aioclient_mock)
    coordinator = entry.runtime_data
    scheduled = coordinator._unsub_refresh
    coordinator.async_handle_report("mo1", {"motionDetected": True}, [{"code": "pir", "value": "pir"}])
    assert coordinator._unsub_refresh is scheduled


async def test_snapshot_keeps_realtime_measurements(hass: HomeAssistant, aioclient_mock, no_realtime) -> None:
    entry = await _setup(hass, aioclient_mock)
    coordinator = entry.runtime_data
    temp = _eid(hass, "sensor", "th1_temperature")
    hum = _eid(hass, "sensor", "th1_humidity")

    _, patch, raw = parse_report(_report("th1", ("va_temperature", 251)))
    coordinator.async_handle_report("th1", patch, raw)

    # A lagging snapshot: stale temperature, null humidity, sensor listed offline.
    devices = copy.deepcopy(DEVICES + EXTRA)
    th = next(d for d in devices if d["id"] == "th1")
    th["online"] = False
    th["deviceState"] = {"temperature": 23.4, "humidity": None, "battery": 80}
    _serve(aioclient_mock, devices)
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get(temp).state == "25.1"
    assert hass.states.get(hum).state == "45"


async def test_transient_poll_failure_keeps_entities(hass: HomeAssistant, aioclient_mock, no_realtime) -> None:
    import aiohttp

    entry = await _setup(hass, aioclient_mock)
    coordinator = entry.runtime_data
    temp = _eid(hass, "sensor", "th1_temperature")

    _serve(aioclient_mock, exc=aiohttp.ClientError("boom"))
    await coordinator.async_refresh()
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(temp).state == "23.4"

    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(temp).state == STATE_UNAVAILABLE

    _serve(aioclient_mock)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(temp).state == "23.4"
