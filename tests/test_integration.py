"""End-to-end tests against a mocked Hejhome cloud."""
from __future__ import annotations

import json

from homeassistant import config_entries
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hejhome.const import DOMAIN
from custom_components.hejhome.event import classify
from custom_components.hejhome.realtime import parse_report

from .conftest import DEVICES, ENTRY_DATA, SQ, mock_login


# ---- parsing ---------------------------------------------------------------

def _report(dev, *status):
    return json.dumps({"deviceDataReport": {"devId": dev, "status": [{"code": c, "value": v} for c, v in status]}})


def test_parse_motion_and_climate():
    assert parse_report(_report("m", ("pir", "pir")))[1]["motionDetected"] is True
    assert parse_report(_report("m", ("pir", "none")))[1]["motionDetected"] is False
    _, patch, _ = parse_report(_report("t", ("va_temperature", 235), ("va_humidity", 456), ("battery", 77)))
    assert patch == {"temperature": 23.5, "humidity": 46, "battery": 77.0}


def test_parse_light_scales():
    _, patch, _ = parse_report(
        _report("l", ("switch_led", True), ("work_mode", "colour"), ("colour_data", '{"h":200,"s":255,"v":140}'))
    )
    assert patch["power"] is True and patch["lightMode"] == "COLOR"
    assert patch["hsvColor"] == {"hue": 200, "saturation": 100, "brightness": 50}


def test_parse_rejects_garbage():
    assert parse_report("not json") is None
    assert parse_report(json.dumps({"deviceDataReport": {"devId": "x", "status": [{"code": "__proto__"}]}})) is None


def test_button_classify():
    assert classify([{"code": "switch_mode1", "value": "single_click"}])[0] == "single"
    assert classify([{"code": "switch1_value", "value": "double_click"}])[0] == "double"
    assert classify([{"code": "switch1_value", "value": "long_press"}])[0] == "long"
    assert classify([{"code": "battery", "value": 90}]) is None
    assert classify([{"code": "weird", "value": "xyz"}])[0] == "unknown"


# ---- config flow -------------------------------------------------------------

async def test_config_flow(hass: HomeAssistant, aioclient_mock, no_realtime) -> None:
    mock_login(aioclient_mock)
    aioclient_mock.get(f"{SQ}/dashboard/family", json={"result": [{"familyId": 1, "name": "우리집"}]})
    aioclient_mock.get(f"{SQ}/dashboard/1/devices-state?scope=shop", json=DEVICES)
    aioclient_mock.get(f"{SQ}/dashboard/rooms/1", json={"result": {"rooms": []}})

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"email": "Me@Example.com"})
    assert result["step_id"] == "verify"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": "123456", "password": "pw"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Hejhome - 우리집"
    assert result["data"]["email"] == "me@example.com"
    assert result["data"]["session"]["access_token"] == "newtok"
    assert result["data"]["session"]["jsession_id"] == "js123"

    # The token request must use the vendor client credentials.
    token_call = next(c for c in aioclient_mock.mock_calls if str(c[1]).endswith("/oauth/token"))
    assert "code=abc" in token_call[2]


async def test_config_flow_bad_code(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.post("https://2factor.goqual.com/api/2factor/send/email?vendor=web", status=200)
    aioclient_mock.post("https://2factor.goqual.com/api/2factor/verify", status=401)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"email": "me@example.com"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": "000000", "password": "pw"})
    assert result["errors"] == {"base": "invalid_code"}


# ---- entities ------------------------------------------------------------------

async def _setup(hass, aioclient_mock):
    aioclient_mock.get(f"{SQ}/dashboard/1/devices-state?scope=shop", json=DEVICES)
    aioclient_mock.get(
        f"{SQ}/dashboard/rooms/1", json={"result": {"rooms": [{"room_id": 10, "name": "거실"}, {"room_id": 11, "name": "침실"}]}}
    )
    aioclient_mock.post(f"{SQ}/dashboard/control/bulb1", text="")
    aioclient_mock.post(f"{SQ}/dashboard/control/sw1", text="")
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, unique_id="me@example.com_1")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _eid(hass, platform, unique_id):
    from homeassistant.helpers import entity_registry as er

    return er.async_get(hass).async_get_entity_id(platform, DOMAIN, unique_id)


async def test_entities_and_push(hass: HomeAssistant, aioclient_mock, no_realtime) -> None:
    entry = await _setup(hass, aioclient_mock)
    coordinator = entry.runtime_data

    motion = _eid(hass, "binary_sensor", "mo1_motion")
    temp = _eid(hass, "sensor", "th1_temperature")
    hum = _eid(hass, "sensor", "th1_humidity")
    bulb = _eid(hass, "light", "bulb1")
    button = _eid(hass, "event", "btn1_button")
    sw2 = _eid(hass, "switch", "sw1_power2")
    assert all([motion, temp, hum, bulb, button, sw2])

    assert hass.states.get(motion).state == STATE_OFF
    assert hass.states.get(temp).state == "23.4"
    assert hass.states.get(hum).state == "45"
    assert hass.states.get(bulb).state == STATE_ON
    assert hass.states.get(bulb).attributes["hs_color"] == (120.0, 80.0)
    assert hass.states.get(button).state == STATE_UNKNOWN
    assert hass.states.get(sw2).state == STATE_OFF

    # Rooms become areas.
    from homeassistant.helpers import device_registry as dr

    dev = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, "mo1")})
    assert dev.suggested_area == "거실" or dev.area_id == "거실"

    # MQTT push: motion, temperature, button press.
    events = []
    hass.bus.async_listen("hejhome_button", events.append)
    coordinator.async_handle_report("mo1", {"motionDetected": True}, [{"code": "pir", "value": "pir"}])
    _, patch, raw = parse_report(_report("th1", ("va_temperature", 251)))
    coordinator.async_handle_report("th1", patch, raw)
    coordinator.async_handle_report("btn1", {}, [{"code": "switch_mode1", "value": "double_click"}])
    await hass.async_block_till_done()

    assert hass.states.get(motion).state == STATE_ON
    assert hass.states.get(temp).state == "25.1"
    state = hass.states.get(button)
    assert state.attributes["event_type"] == "double"
    assert state.attributes["value"] == "double_click"
    assert events and events[0].data["status"][0]["value"] == "double_click"


async def test_controls(hass: HomeAssistant, aioclient_mock, no_realtime) -> None:
    await _setup(hass, aioclient_mock)
    bulb = _eid(hass, "light", "bulb1")
    sw2 = _eid(hass, "switch", "sw1_power2")
    start = len(aioclient_mock.mock_calls)

    await hass.services.async_call(
        "light", "turn_on", {"entity_id": bulb, "hs_color": (240, 100), "brightness": 255}, blocking=True
    )
    await hass.services.async_call("switch", "turn_on", {"entity_id": sw2}, blocking=True)
    await hass.services.async_call("light", "turn_on", {"entity_id": bulb, "white": 128}, blocking=True)

    bodies = [
        (str(c[1]).rsplit("/", 1)[-1], json.loads(c[2]))
        for c in aioclient_mock.mock_calls[start:]
        if "/dashboard/control/" in str(c[1])
    ]
    assert bodies[0] == ("bulb1", {"requirments": {"hsvColor": {"hue": 240, "saturation": 100, "brightness": 100}}})
    assert bodies[1] == ("sw1", {"requirments": {"power2": True}})
    assert bodies[2] == ("bulb1", {"requirments": {"lightMode": "white"}})
    assert bodies[3] == ("bulb1", {"requirments": {"brightness": 50}})
    assert hass.states.get(sw2).state == STATE_ON
    assert hass.states.get(bulb).attributes["color_mode"] == "white"


async def test_expired_session_relogins(hass: HomeAssistant, aioclient_mock, no_realtime) -> None:
    mock_login(aioclient_mock)
    data = {**ENTRY_DATA, "session": {**ENTRY_DATA["session"], "expires_at": 0}}
    aioclient_mock.get(f"{SQ}/dashboard/1/devices-state?scope=shop", json=DEVICES)
    aioclient_mock.get(f"{SQ}/dashboard/rooms/1", json={"result": {"rooms": []}})
    entry = MockConfigEntry(domain=DOMAIN, data=data, unique_id="me@example.com_1")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    assert entry.data["session"]["access_token"] == "newtok"


async def test_login_rejected_starts_reauth(hass: HomeAssistant, aioclient_mock, no_realtime) -> None:
    aioclient_mock.post(f"{SQ}/oauth/login?vendor=shop", status=401)
    data = {**ENTRY_DATA, "session": {**ENTRY_DATA["session"], "expires_at": 0}}
    entry = MockConfigEntry(domain=DOMAIN, data=data, unique_id="me@example.com_1")
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    flows = hass.config_entries.flow.async_progress()
    assert flows and flows[0]["context"]["source"] == "reauth"
