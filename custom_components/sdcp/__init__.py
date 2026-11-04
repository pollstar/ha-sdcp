"""SDCP 3D printer integration (ELEGOO Saturn / Mars / Jupiter / Centauri and other Chitu boards)."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.typing import ConfigType

from . import legacy
from .const import DOMAIN, SERVICE_DELETE_FILES, SERVICE_START_PRINT
from .coordinator import SDCPCoordinator
from .protocol import SDCPCommandError, SDCPError

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SWITCH,
    Platform.CAMERA,
    Platform.IMAGE,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type SDCPConfigEntry = ConfigEntry[SDCPCoordinator]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    await legacy.async_remove_legacy_resource(hass)
    _register_services(hass)
    websocket_api.async_register_command(hass, ws_files)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: SDCPConfigEntry) -> bool:
    coord = SDCPCoordinator(hass, entry)
    try:
        await coord.async_start()
    except SDCPError as err:
        raise ConfigEntryNotReady(f"Printer at {coord.host} does not answer: {err}") from err
    entry.runtime_data = coord
    dr.async_get(hass).async_get_or_create(config_entry_id=entry.entry_id, **coord.device_info())
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: SDCPConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: SDCPConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        await entry.runtime_data.async_stop()
    return ok


# ---- helpers --------------------------------------------------------------


@callback
def coordinator_for_device(hass: HomeAssistant, device_id: str) -> SDCPCoordinator:
    dev = dr.async_get(hass).async_get(device_id)
    if dev is None:
        raise ServiceValidationError(f"Unknown device {device_id}")
    for entry_id in dev.config_entries:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry and entry.domain == DOMAIN and entry.state is ConfigEntryState.LOADED:
            return entry.runtime_data
    raise ServiceValidationError(f"Device {device_id} is not a loaded SDCP printer")


def _as_ha_error(err: SDCPError) -> HomeAssistantError:
    if isinstance(err, SDCPCommandError):
        return HomeAssistantError(f"Printer refused the command (Ack {err.ack})")
    return HomeAssistantError(f"Printer is not reachable: {err}")


def _register_services(hass: HomeAssistant) -> None:
    async def start_print(call: ServiceCall) -> None:
        coord = coordinator_for_device(hass, call.data["device_id"])
        try:
            await coord.client.start_print(call.data["filename"], call.data.get("start_layer", 0))
        except SDCPError as err:
            raise _as_ha_error(err) from err

    async def delete_files(call: ServiceCall) -> dict[str, Any]:
        coord = coordinator_for_device(hass, call.data["device_id"])
        try:
            failed = await coord.client.delete_files(call.data["files"])
        except SDCPError as err:
            raise _as_ha_error(err) from err
        if failed:
            raise HomeAssistantError(f"Could not delete: {', '.join(failed)}")
        return {"failed": failed}

    hass.services.async_register(
        DOMAIN,
        SERVICE_START_PRINT,
        start_print,
        schema=vol.Schema(
            {
                vol.Required("device_id"): cv.string,
                vol.Required("filename"): cv.string,
                vol.Optional("start_layer", default=0): vol.All(vol.Coerce(int), vol.Range(min=0)),
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DELETE_FILES,
        delete_files,
        schema=vol.Schema(
            {
                vol.Required("device_id"): cv.string,
                vol.Required("files"): vol.All(cv.ensure_list, [cv.string]),
            }
        ),
    )


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/files", vol.Required("device_id"): str, vol.Optional("path", default="/"): str}
)
@websocket_api.async_response
async def ws_files(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    """List files on the printer (Cmd 258) for the card's file browser."""
    try:
        coord = coordinator_for_device(hass, msg["device_id"])
        entries = await coord.client.list_files(msg["path"])
    except ServiceValidationError as err:
        connection.send_error(msg["id"], "not_found", str(err))
        return
    except SDCPError as err:
        connection.send_error(msg["id"], "printer_error", str(err))
        return
    items = []
    for e in entries:
        item = {"path": e.get("name"), "dir": e.get("type") == 0}
        for src, dst in (("usedSize", "used"), ("totalSize", "total"), ("storageType", "storage"),
                         ("size", "size"), ("FileSize", "size"), ("CreateTime", "created")):
            if src in e:
                item[dst] = e[src]
        items.append(item)
    connection.send_result(msg["id"], {"path": msg["path"], "items": items})
