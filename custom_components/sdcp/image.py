"""Thumbnail of the current print job (from Cmd 321 task detail)."""

from __future__ import annotations

import logging

import aiohttp

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import SDCPCoordinator
from .entity import SDCPEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([JobThumbnail(hass, entry.runtime_data)])


class JobThumbnail(SDCPEntity, ImageEntity):
    _attr_translation_key = "thumbnail"
    _attr_content_type = "image/png"

    def __init__(self, hass: HomeAssistant, coordinator: SDCPCoordinator) -> None:
        SDCPEntity.__init__(self, coordinator, "thumbnail")
        ImageEntity.__init__(self, hass)
        self._url: str | None = None
        self._cache: bytes | None = None

    @property
    def available(self) -> bool:
        return super().available and self._url is not None

    @callback
    def _handle_coordinator_update(self) -> None:
        url = self.coordinator.thumbnail_url()
        if url != self._url:
            self._url = url
            self._cache = None
            self._attr_image_last_updated = dt_util.utcnow()
        super()._handle_coordinator_update()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._handle_coordinator_update()

    async def async_image(self) -> bytes | None:
        if not self._url:
            return None
        if self._cache is not None:
            return self._cache
        try:
            async with async_get_clientsession(self.hass).get(
                self._url, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                resp.raise_for_status()
                self._attr_content_type = resp.headers.get("Content-Type", "image/png").split(";")[0]
                self._cache = await resp.read()
        except (aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.debug("thumbnail %s: %s", self._url, err)
            return None
        return self._cache
