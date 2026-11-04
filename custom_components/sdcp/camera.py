"""Printer camera (RTSP, enabled through Cmd 386).

Disabled by default: some firmwares (Saturn 4 Ultra) crash when the stream is
toggled quickly, so the integration only ever enables it and never disables it.
Transport and fallback URL come from the printer model (printers/).
"""

from __future__ import annotations

import logging

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import SDCPCoordinator
from .entity import SDCPEntity

_LOGGER = logging.getLogger(__name__)


def go2rtc_source(url: str, transport: str = "udp") -> str:
    """go2rtc stream source for the printer's RTSP.

    go2rtc's own RTSP client speaks TCP only; printers that need UDP are read
    through go2rtc's ffmpeg source with its UDP input template (video copied).
    """
    if transport == "udp":
        return f"ffmpeg:{url}#input=rtsp/udp#video=copy"
    return url


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    coord: SDCPCoordinator = entry.runtime_data
    a = coord.attributes
    if a.get("CameraStatus") == 1 or "VIDEO_STREAM" in (a.get("Capabilities") or []):
        async_add_entities([PrinterCamera(coord)])


class PrinterCamera(SDCPEntity, Camera):
    _attr_translation_key = "camera"
    _attr_supported_features = CameraEntityFeature.STREAM
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: SDCPCoordinator) -> None:
        SDCPEntity.__init__(self, coordinator, "camera")
        Camera.__init__(self)
        self._transport = coordinator.model.camera.rtsp_transport
        self.stream_options["rtsp_transport"] = self._transport

    async def stream_source(self) -> str | None:
        url = await self.coordinator.async_video_url()
        if url and url.startswith("rtsp") and "go2rtc" in self.hass.config.components:
            return go2rtc_source(url, self._transport)
        return url

    async def async_camera_image(self, width: int | None = None, height: int | None = None) -> bytes | None:
        url = await self.coordinator.async_video_url()
        if not url:
            return None
        try:
            from homeassistant.components.ffmpeg import async_get_image  # noqa: PLC0415
        except ImportError:
            return None
        try:
            return await async_get_image(self.hass, f"-rtsp_transport {self._transport} -i {url}", width=width, height=height)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("snapshot failed: %s", err)
            return None
