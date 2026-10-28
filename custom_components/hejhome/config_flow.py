"""Config flow: e-mail verification code -> password login -> pick a family (home)."""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_CODE, CONF_EMAIL, CONF_PASSWORD

from .api import HejAuth, HejAuthError, HejClient, HejConnectionError, HejError, HejSession
from .const import CONF_FAMILY_ID, CONF_SESSION, DOMAIN

_LOGGER = logging.getLogger(__name__)


class HejhomeConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._email: str = ""
        self._password: str = ""
        self._session: HejSession | None = None
        self._families: dict[int, str] = {}

    def _auth(self) -> HejAuth:
        from . import create_websession

        return HejAuth(create_websession(self.hass))

    async def _send_code(self, errors: dict[str, str]) -> bool:
        try:
            await self._auth().send_verification_code(self._email)
        except HejAuthError:
            errors["base"] = "invalid_email"
        except HejConnectionError:
            errors["base"] = "cannot_connect"
        except HejError:
            _LOGGER.exception("Sending Hejhome verification code failed")
            errors["base"] = "unknown"
        return not errors

    # ---- initial setup -------------------------------------------------

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._email = user_input[CONF_EMAIL].strip().lower()
            if await self._send_code(errors):
                return await self.async_step_verify()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_EMAIL, default=self._email): str}),
            errors=errors,
        )

    async def async_step_verify(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            auth = self._auth()
            try:
                await auth.verify_code(self._email, user_input[CONF_CODE].strip())
            except HejAuthError:
                errors["base"] = "invalid_code"
            except HejConnectionError:
                errors["base"] = "cannot_connect"
            else:
                self._password = user_input[CONF_PASSWORD]
                try:
                    self._session = await auth.login(self._email, self._password)
                    families = await HejClient(auth._session, self._session).get_families()
                except HejAuthError:
                    errors["base"] = "invalid_auth"
                except HejConnectionError:
                    errors["base"] = "cannot_connect"
                except HejError:
                    _LOGGER.exception("Hejhome login failed")
                    errors["base"] = "unknown"
                else:
                    if self.source == "reauth":
                        return self._finish_reauth()
                    self._families = {
                        int(f["familyId"]): str(f.get("name") or f["familyId"])
                        for f in families
                        if isinstance(f, dict) and f.get("familyId") is not None
                    }
                    if not self._families:
                        errors["base"] = "no_family"
                    elif len(self._families) == 1:
                        return await self._create(next(iter(self._families)))
                    else:
                        return await self.async_step_family()
        return self.async_show_form(
            step_id="verify",
            data_schema=vol.Schema(
                {vol.Required(CONF_CODE): str, vol.Required(CONF_PASSWORD): str}
            ),
            description_placeholders={"email": self._email},
            errors=errors,
        )

    async def async_step_family(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return await self._create(int(user_input[CONF_FAMILY_ID]))
        return self.async_show_form(
            step_id="family",
            data_schema=vol.Schema(
                {vol.Required(CONF_FAMILY_ID): vol.In({str(k): v for k, v in self._families.items()})}
            ),
        )

    async def _create(self, family_id: int) -> ConfigFlowResult:
        assert self._session is not None
        await self.async_set_unique_id(f"{self._email}_{family_id}")
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=f"Hejhome - {self._families.get(family_id, family_id)}",
            data={
                CONF_EMAIL: self._email,
                CONF_PASSWORD: self._password,
                CONF_FAMILY_ID: family_id,
                CONF_SESSION: self._session.to_dict(),
            },
        )

    # ---- reauth --------------------------------------------------------

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        self._email = entry_data[CONF_EMAIL]
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None and await self._send_code(errors):
            return await self.async_step_verify()
        return self.async_show_form(
            step_id="reauth_confirm",
            description_placeholders={"email": self._email},
            errors=errors,
        )

    def _finish_reauth(self) -> ConfigFlowResult:
        assert self._session is not None
        entry = self._get_reauth_entry()
        return self.async_update_reload_and_abort(
            entry,
            data={**entry.data, CONF_PASSWORD: self._password, CONF_SESSION: self._session.to_dict()},
        )
