"""Shared fixtures."""
from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from custom_components.hejhome.const import CONF_FAMILY_ID, CONF_SESSION, DOMAIN

SQ = "https://square.hej.so"
TF = "https://2factor.goqual.com"

DEVICES = [
    {"id": "mo1", "name": "현관 모션", "deviceType": "SensorMo", "roomId": 10, "online": True,
     "deviceState": {"motionDetected": False, "battery": 90}},
    {"id": "th1", "name": "거실 온습도", "deviceType": "SensorTh", "roomId": 10, "online": True,
     "deviceState": {"temperature": 23.4, "humidity": 45, "battery": 80}},
    {"id": "bulb1", "name": "침실 전구", "deviceType": "LightRgbw5", "roomId": 11, "online": True,
     "deviceState": {"power": True, "lightMode": "COLOR", "brightness": 50,
                     "hsvColor": {"hue": 120, "saturation": 80, "brightness": 50}}},
    {"id": "btn1", "name": "스마트 버튼", "deviceType": "SmartButton", "roomId": 11, "online": True,
     "deviceState": {"battery": 100}},
    {"id": "sw1", "name": "거실 스위치", "deviceType": "ZigbeeSwitch2", "roomId": 10, "online": True,
     "deviceState": {"power1": True, "power2": False}},
]

SESSION = {
    "identifier": "me@example.com",
    "access_token": "tok",
    "jsession_id": "js",
    "username_cookie": "me%40example.com",
    "expires_at": time.time() + 86400,
}

ENTRY_DATA = {
    "email": "me@example.com",
    "password": "pw",
    CONF_FAMILY_ID: 1,
    CONF_SESSION: SESSION,
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def no_realtime():
    with patch("custom_components.hejhome.realtime.HejRealtime.start"), patch(
        "custom_components.hejhome.realtime.HejRealtime.stop"
    ):
        yield


def mock_login(aioclient_mock):
    aioclient_mock.post(f"{TF}/api/2factor/send/email?vendor=web", status=200)
    aioclient_mock.post(f"{TF}/api/2factor/verify", status=200)
    aioclient_mock.post(
        f"{SQ}/oauth/login?vendor=shop", status=200, headers={"Set-Cookie": "JSESSIONID=js123; Path=/; HttpOnly"}
    )
    aioclient_mock.get(
        f"{SQ}/oauth/authorize", status=302, headers={"Location": f"{SQ}/list?code=abc"}
    )
    aioclient_mock.post(f"{SQ}/oauth/token", json={"access_token": "newtok", "expires_in": 3600})


__all__ = ["DOMAIN"]
