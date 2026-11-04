"""Clean-up of the card that versions up to 0.1.x bundled with the integration.

The card now lives in its own repository (pollstar/sdcp-printer-card) and is
installed through HACS, which registers its own resource. Older versions of
this integration registered `/sdcp_static/sdcp-printer-card.js`; that URL no
longer exists, so the stale Lovelace resource is removed on start.
"""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant

from .const import LEGACY_CARD_PATH
from .resources import plan_removal

_LOGGER = logging.getLogger(__name__)


def _lovelace_resources(hass: HomeAssistant):
    data = hass.data.get("lovelace")
    if data is None:
        return None, None
    if isinstance(data, dict):  # older HA
        return data.get("resource_mode") or data.get("mode"), data.get("resources")
    mode = getattr(data, "resource_mode", None) or getattr(data, "mode", None)
    return mode, getattr(data, "resources", None)


async def async_remove_legacy_resource(hass: HomeAssistant) -> None:
    mode, resources = _lovelace_resources(hass)
    if mode != "storage" or resources is None or not hasattr(resources, "async_delete_item"):
        return
    try:
        await resources.async_get_info()
        for rid in plan_removal(list(resources.async_items()), LEGACY_CARD_PATH):
            await resources.async_delete_item(rid)
            _LOGGER.info("Removed the old bundled card resource %s; install sdcp-printer-card via HACS", LEGACY_CARD_PATH)
    except Exception as err:  # noqa: BLE001 — never fail setup over this
        _LOGGER.debug("Could not clean up the old card resource: %s", err)
