"""Config flow: pick a discovered printer or enter its address."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CONF_POLL_ACTIVE,
    CONF_POLL_IDLE,
    DEFAULT_POLL_ACTIVE,
    DEFAULT_POLL_IDLE,
    DOMAIN,
)
from .protocol import SDCPClient, SDCPError, async_discover

_LOGGER = logging.getLogger(__name__)


async def _probe(hass, host: str) -> dict[str, Any]:
    """Connect once and return the printer's Attributes."""
    client = SDCPClient(async_get_clientsession(hass), host)
    client.start()
    try:
        await client.wait_ready(12)
        return dict(client.attributes or {}, MainboardID=client.mainboard_id or (client.attributes or {}).get("MainboardID"))
    finally:
        await client.stop()


class SDCPConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._discovered: dict[str, str] = {}

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return SDCPOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = str(user_input[CONF_HOST]).strip()
            try:
                attrs = await _probe(self.hass, host)
            except SDCPError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error probing %s", host)
                errors["base"] = "unknown"
            else:
                mbid = attrs.get("MainboardID")
                if not mbid:
                    errors["base"] = "no_id"
                else:
                    await self.async_set_unique_id(mbid)
                    self._abort_if_unique_id_configured(updates={CONF_HOST: host})
                    title = attrs.get("Name") or attrs.get("MachineName") or host
                    model = attrs.get("MachineName")
                    if model and model != title:
                        title = f"{title} ({model})"
                    return self.async_create_entry(title=title, data={CONF_HOST: host})

        if not self._discovered:
            try:
                found = await async_discover(2.0)
            except OSError as err:
                _LOGGER.debug("discovery failed: %s", err)
                found = []
            configured = {e.unique_id for e in self._async_current_entries()}
            self._discovered = {
                p.host: p.label for p in found if p.mainboard_id not in configured
            }

        options = [SelectOptionDict(value=h, label=l) for h, l in self._discovered.items()]
        default = next(iter(self._discovered), "")
        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=(user_input or {}).get(CONF_HOST, default)): SelectSelector(
                    SelectSelectorConfig(options=options, custom_value=True, mode=SelectSelectorMode.DROPDOWN)
                )
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
            description_placeholders={"found": str(len(self._discovered))},
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            host = str(user_input[CONF_HOST]).strip()
            try:
                attrs = await _probe(self.hass, host)
            except SDCPError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(attrs.get("MainboardID"))
                self._abort_if_unique_id_mismatch(reason="wrong_printer")
                return self.async_update_reload_and_abort(entry, data_updates={CONF_HOST: host})
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema({vol.Required(CONF_HOST, default=entry.data[CONF_HOST]): str}),
            errors=errors,
        )


class SDCPOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        o = self.config_entry.options

        def num(lo, hi):
            return NumberSelector(
                NumberSelectorConfig(min=lo, max=hi, step=1, mode=NumberSelectorMode.BOX, unit_of_measurement="s")
            )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_POLL_ACTIVE, default=o.get(CONF_POLL_ACTIVE, DEFAULT_POLL_ACTIVE)): num(1, 60),
                    vol.Required(CONF_POLL_IDLE, default=o.get(CONF_POLL_IDLE, DEFAULT_POLL_IDLE)): num(5, 300),
                }
            ),
        )
