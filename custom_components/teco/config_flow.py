"""Config flow for TECO — point the integration at the sidecar."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import TecoClient, TecoSidecarError
from .const import CONF_TOKEN, CONF_URL, DEFAULT_URL, DOMAIN


class TecoConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            client = TecoClient(
                async_get_clientsession(self.hass),
                user_input[CONF_URL],
                user_input.get(CONF_TOKEN) or None,
            )
            try:
                await client.health()
            except TecoSidecarError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(user_input[CONF_URL])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title="TECO (Tampa Electric)",
                                               data=user_input)

        schema = vol.Schema({
            vol.Required(CONF_URL, default=DEFAULT_URL): str,
            vol.Optional(CONF_TOKEN): str,
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)
