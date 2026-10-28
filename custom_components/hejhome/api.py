"""Async client for the (unofficial) Hejhome web dashboard API.

Protocol notes are ported from the ISC-licensed homebridge plugin
@chazepps/homebridge-hejhome (src/hej/auth.ts, src/hej/rest.ts).
This module has no Home Assistant dependencies so it can be tested on its own.
"""
from __future__ import annotations

import base64
import json
import re
import time
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlparse

import aiohttp

# Client credentials of the vendor's own web dashboard (square.hej.so).
HEJ_CLIENT_ID = "62f4020744ca4510827d3b4a4d2c7e7f"
HEJ_CLIENT_SECRET = "fcd4302cece447a9ab009296f649d2c0"

SQUARE_ORIGIN = "https://square.hej.so"
TWO_FACTOR_ORIGIN = "https://2factor.goqual.com"

_TIMEOUT = aiohttp.ClientTimeout(total=30)
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class HejError(Exception):
    """Base error."""


class HejAuthError(HejError):
    """Credentials, verification code or session were rejected."""


class HejConnectionError(HejError):
    """The Hejhome servers could not be reached."""


@dataclass
class HejSession:
    """An authenticated dashboard session."""

    identifier: str
    access_token: str
    jsession_id: str
    username_cookie: str
    expires_at: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HejSession:
        return cls(
            identifier=str(data["identifier"]),
            access_token=str(data["access_token"]),
            jsession_id=str(data["jsession_id"]),
            username_cookie=str(data["username_cookie"]),
            expires_at=float(data["expires_at"]),
        )

    @property
    def expired(self) -> bool:
        # Treat the last 10 minutes as expired so we re-login before calls fail.
        return time.time() >= self.expires_at - 600

    @property
    def mqtt_topic(self) -> str:
        from urllib.parse import unquote

        return f"custom.{unquote(self.username_cookie).replace('.', '-')}.*"


def _basic(user: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


def _jsession_from(resp: aiohttp.ClientResponse) -> str | None:
    for header in resp.headers.getall("Set-Cookie", []):
        match = re.search(r"JSESSIONID=([^;]+)", header)
        if match:
            return match.group(1)
    return None


class HejAuth:
    """Email verification + password login."""

    def __init__(self, websession: aiohttp.ClientSession) -> None:
        self._session = websession

    async def send_verification_code(self, email: str) -> None:
        if not _EMAIL_RE.match(email):
            raise HejAuthError("Hejhome login requires the e-mail address registered in the app")
        await self._post_json(
            f"{TWO_FACTOR_ORIGIN}/api/2factor/send/email?vendor=web", {"email": email}
        )

    async def verify_code(self, email: str, code: str) -> None:
        if not re.fullmatch(r"\d{6}", code):
            raise HejAuthError("Verification code must be six digits")
        await self._post_json(
            f"{TWO_FACTOR_ORIGIN}/api/2factor/verify", {"username": email, "authCode": code}
        )

    async def login(self, email: str, password: str) -> HejSession:
        try:
            async with self._session.post(
                f"{SQUARE_ORIGIN}/oauth/login?vendor=shop",
                headers={
                    "accept": "text/plain, */*; q=0.01",
                    "accept-language": "ko-kr",
                    "authorization": _basic(email, password),
                    "content-type": "application/x-www-form-urlencoded",
                    "x-requested-with": "XMLHttpRequest, XMLHttpRequest",
                },
                timeout=_TIMEOUT,
                allow_redirects=False,
            ) as resp:
                _raise_for_status(resp, "login")
                jsession_id = _jsession_from(resp)
            if not jsession_id:
                raise HejAuthError("Login did not return a session cookie")

            username_cookie = quote(email, safe="")
            cookie = f"username={username_cookie}; JSESSIONID={jsession_id}; autoLogin=true"

            authorize = f"{SQUARE_ORIGIN}/oauth/authorize?" + urlencode(
                {
                    "client_id": HEJ_CLIENT_ID,
                    "redirect_uri": f"{SQUARE_ORIGIN}/list",
                    "response_type": "code",
                    "scope": "shop",
                }
            )
            async with self._session.get(
                authorize, headers={"cookie": cookie}, allow_redirects=False, timeout=_TIMEOUT
            ) as resp:
                location = resp.headers.get("location") or str(resp.url)
            code = parse_qs(urlparse(urljoin(SQUARE_ORIGIN, location)).query).get("code", [None])[0]
            if not code:
                raise HejAuthError("Authorization did not return an OAuth code")

            async with self._session.post(
                f"{SQUARE_ORIGIN}/oauth/token",
                headers={
                    "accept": "application/json, text/javascript, */*; q=0.01",
                    "authorization": _basic(HEJ_CLIENT_ID, HEJ_CLIENT_SECRET),
                    "content-type": "application/x-www-form-urlencoded",
                    "cookie": cookie,
                    "x-requested-with": "XMLHttpRequest",
                },
                data=urlencode(
                    {
                        "grant_type": "authorization_code",
                        "code": code,
                        "client_id": HEJ_CLIENT_ID,
                        "redirect_uri": f"{SQUARE_ORIGIN}/list",
                    }
                ),
                timeout=_TIMEOUT,
                allow_redirects=False,
            ) as resp:
                _raise_for_status(resp, "token exchange")
                body = await resp.json(content_type=None)
        except aiohttp.ClientError as err:
            raise HejConnectionError(str(err)) from err

        token = body.get("access_token") if isinstance(body, dict) else None
        if not token:
            raise HejAuthError("Token response did not include an access token")
        return HejSession(
            identifier=email,
            access_token=token,
            jsession_id=jsession_id,
            username_cookie=username_cookie,
            expires_at=time.time() + float(body.get("expires_in") or 86400),
        )

    async def _post_json(self, url: str, payload: dict[str, Any]) -> None:
        try:
            async with self._session.post(
                url,
                data=json.dumps(payload),
                headers={"accept": "*/*", "content-type": "application/json;charset=UTF-8"},
                timeout=_TIMEOUT,
            ) as resp:
                _raise_for_status(resp, url.rsplit("/", 1)[-1])
        except aiohttp.ClientError as err:
            raise HejConnectionError(str(err)) from err


class HejClient:
    """Authenticated dashboard REST calls."""

    def __init__(self, websession: aiohttp.ClientSession, session: HejSession) -> None:
        self._http = websession
        self.session = session

    async def get_families(self) -> list[dict[str, Any]]:
        data = await self._request("GET", "dashboard/family")
        return list((data or {}).get("result") or [])

    async def get_rooms(self, family_id: int) -> list[dict[str, Any]]:
        data = await self._request("GET", f"dashboard/rooms/{family_id}")
        return list(((data or {}).get("result") or {}).get("rooms") or [])

    async def get_devices(self, family_id: int) -> list[dict[str, Any]]:
        data = await self._request("GET", f"dashboard/{family_id}/devices-state?scope=shop")
        return list(data or [])

    async def control(self, device_id: str, requirements: dict[str, Any]) -> None:
        # "requirments" (sic) is the field name the vendor API expects.
        await self._request(
            "POST", f"dashboard/control/{device_id}", {"requirments": requirements}, parse=False
        )

    async def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None, parse: bool = True
    ) -> Any:
        s = self.session
        headers = {
            "accept": "application/json, text/javascript, */*; q=0.01",
            "authorization": f"Bearer {s.access_token}",
            "content-type": "application/json;charset=UTF-8",
            "cookie": (
                f"username={s.username_cookie}; autoLogin=true; "
                f"JSESSIONID={s.jsession_id}; accessToken={s.access_token}"
            ),
            "Referer": f"{SQUARE_ORIGIN}/list",
            "x-requested-with": "XMLHttpRequest",
        }
        try:
            async with self._http.request(
                method,
                f"{SQUARE_ORIGIN}/{path}",
                headers=headers,
                data=json.dumps(payload) if payload is not None else None,
                timeout=_TIMEOUT,
                allow_redirects=False,
            ) as resp:
                _raise_for_status(resp, path)
                text = await resp.text()
        except aiohttp.ClientError as err:
            raise HejConnectionError(str(err)) from err
        if not parse:
            return None
        return json.loads(text) if text else None


def _raise_for_status(resp: aiohttp.ClientResponse, what: str) -> None:
    if resp.status in (401, 403):
        raise HejAuthError(f"{what}: HTTP {resp.status}")
    # A redirect to the login page also means the session is gone.
    if 300 <= resp.status < 400 and "login" in (resp.headers.get("location") or ""):
        raise HejAuthError(f"{what}: redirected to login")
    if resp.status >= 400:
        raise HejError(f"{what}: HTTP {resp.status}")
