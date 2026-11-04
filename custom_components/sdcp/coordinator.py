"""Push coordinator: one SDCP connection per config entry."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CONF_POLL_ACTIVE,
    CONF_POLL_IDLE,
    DEFAULT_POLL_ACTIVE,
    DEFAULT_POLL_IDLE,
    DOMAIN,
    EVENT_PRINTER,
    MANUFACTURER_FALLBACK,
)
from .printers import PrinterModel, resolve
from .protocol import SDCPClient, SDCPError, print_info

_LOGGER = logging.getLogger(__name__)


class SDCPCoordinator(DataUpdateCoordinator[int]):
    """Holds the latest Status/Attributes; entities read from it."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=f"{DOMAIN} {entry.data[CONF_HOST]}")
        self.host: str = entry.data[CONF_HOST]
        opts = entry.options
        self.client = SDCPClient(
            async_get_clientsession(hass),
            self.host,
            mainboard_id=entry.unique_id,
            poll_active=float(opts.get(CONF_POLL_ACTIVE, DEFAULT_POLL_ACTIVE)),
            poll_idle=float(opts.get(CONF_POLL_IDLE, DEFAULT_POLL_IDLE)),
            on_status=self._on_status,
            on_attributes=self._on_attributes,
            on_connection=self._on_connection,
            on_message=self._on_message,
        )
        self.task: dict[str, Any] | None = None  # Cmd 321 detail of the current TaskId
        self.finish_at: datetime | None = None
        self.last_update: datetime | None = None
        self.last_message: dict[str, Any] | None = None
        self._seq = 0
        self._prev_stage: str | None = None
        self._prev_error = 0
        self._task_fetch: asyncio.Task | None = None
        self._fetched_task_id: str | None = None
        self._video_url: str | None = None
        self._video_at = 0.0
        self._video_lock = asyncio.Lock()
        self.model: PrinterModel = resolve(None, None)

    # ---- lifecycle --------------------------------------------------------

    async def async_start(self) -> None:
        self.client.start()
        try:
            await self.client.wait_ready(20)
        except SDCPError:
            await self.client.stop()
            raise
        self.model = resolve(self.client.attributes, self.client.status)
        _LOGGER.debug("%s: printer model %s (%s)", self.host, self.model.id, self.model.name)

    async def async_stop(self) -> None:
        if self._task_fetch:
            self._task_fetch.cancel()
        await self.client.stop()

    async def _async_update_data(self) -> int:  # not polled; pushes only
        return self._seq

    # ---- accessors --------------------------------------------------------

    @property
    def status(self) -> dict[str, Any]:
        return self.client.status or {}

    @property
    def attributes(self) -> dict[str, Any]:
        return self.client.attributes or {}

    @property
    def connected(self) -> bool:
        return self.client.connected

    @property
    def mainboard_id(self) -> str:
        return self.client.mainboard_id or self.config_entry.unique_id or self.host

    @property
    def print_info(self) -> dict[str, Any]:
        return print_info(self.client.status)

    @property
    def stage(self) -> str | None:
        return self.model.stage(self.print_info.get("Status"))

    @property
    def is_active(self) -> bool:
        return self.stage in self.model.active_stages

    @property
    def is_paused(self) -> bool:
        return self.stage in self.model.paused_stages

    @property
    def has_job(self) -> bool:
        pi = self.print_info
        return bool(pi.get("Filename")) and int(pi.get("TotalLayer") or 0) > 0

    @property
    def tick_seconds(self) -> float:
        return self.model.tick_seconds

    @property
    def slice_info(self) -> dict[str, Any]:
        return (self.task or {}).get("SliceInformation") or {}

    def device_info(self) -> dr.DeviceInfo:
        a = self.attributes
        return dr.DeviceInfo(
            identifiers={(DOMAIN, self.mainboard_id)},
            name=a.get("Name") or a.get("MachineName") or self.config_entry.title,
            manufacturer=a.get("BrandName") or MANUFACTURER_FALLBACK,
            model=a.get("MachineName"),
            sw_version=a.get("FirmwareVersion"),
            hw_version=a.get("ProtocolVersion"),
            serial_number=self.mainboard_id,
        )

    # ---- client callbacks (event loop) ------------------------------------

    def _push(self) -> None:
        self._seq += 1
        self.last_update = dt_util.utcnow()
        self.async_set_updated_data(self._seq)

    @callback
    def _on_connection(self, up: bool) -> None:
        if not up:
            self._video_url = None
            self._video_at = 0.0
        self._push()

    @callback
    def _on_attributes(self, _attrs: dict[str, Any]) -> None:
        self._update_device()
        self._push()

    @callback
    def _on_message(self, kind: str, obj: dict[str, Any]) -> None:
        self.last_message = obj
        self.hass.bus.async_fire(
            EVENT_PRINTER, {"device_id": self._device_id(), "type": kind, "message": obj}
        )

    @callback
    def _on_status(self, status: dict[str, Any]) -> None:
        pi = print_info(status)
        stage = self.model.stage(pi.get("Status"))
        self._update_finish(pi, stage)
        self._detect_events(pi, stage)

        tid = pi.get("TaskId")
        if tid and tid != self._fetched_task_id:
            self._fetched_task_id = tid
            self._schedule_task_fetch(tid, delay=1.0)
        self._push()

    def _update_finish(self, pi: dict[str, Any], stage: str | None) -> None:
        if stage not in self.model.active_stages:
            if stage not in self.model.paused_stages:
                self.finish_at = None
            return
        cur = float(pi.get("CurrentTicks") or 0)
        tot = float(pi.get("TotalTicks") or 0)
        if tot <= 0:
            return
        new = dt_util.utcnow() + timedelta(seconds=max(0.0, tot - cur) * self.tick_seconds)
        # keep the state steady: only move it when the estimate shifts by > 1 min
        if self.finish_at is None or abs((new - self.finish_at).total_seconds()) > 60:
            self.finish_at = new.replace(microsecond=0)

    def _detect_events(self, pi: dict[str, Any], stage: str | None) -> None:
        prev = self._prev_stage
        self._prev_stage = stage
        err = int(pi.get("ErrorNumber") or 0)
        prev_err, self._prev_error = self._prev_error, err
        if prev is None:
            return  # first status after start: no transitions yet
        kind = None
        if stage in self.model.active_stages and prev not in self.model.active_stages | self.model.paused_stages:
            kind = "print_started"
        elif stage in self.model.active_stages and prev in self.model.paused_stages:
            kind = "print_resumed"
        elif stage == "paused" and prev != "paused":
            kind = "print_paused"
        elif stage == "complete" and prev != "complete":
            kind = "print_finished"
        elif stage == "stopped" and prev != "stopped":
            kind = "print_stopped"
        if err and not prev_err:
            self._fire("print_error", pi, error=err)
        if kind:
            self._fire(kind, pi)
            if kind in ("print_finished", "print_stopped") and pi.get("TaskId"):
                self._schedule_task_fetch(pi["TaskId"], delay=3.0)

    def _fire(self, kind: str, pi: dict[str, Any], **extra: Any) -> None:
        self.hass.bus.async_fire(
            EVENT_PRINTER,
            {
                "device_id": self._device_id(),
                "type": kind,
                "filename": pi.get("Filename"),
                "task_id": pi.get("TaskId"),
                "layer": pi.get("CurrentLayer"),
                "total_layers": pi.get("TotalLayer"),
                **extra,
            },
        )

    def _device_id(self) -> str | None:
        dev = dr.async_get(self.hass).async_get_device(identifiers={(DOMAIN, self.mainboard_id)})
        return dev.id if dev else None

    def _update_device(self) -> None:
        """Keep name / firmware in the device registry current."""
        reg = dr.async_get(self.hass)
        dev = reg.async_get_device(identifiers={(DOMAIN, self.mainboard_id)})
        if dev is None:
            return
        a = self.attributes
        changes = {}
        if a.get("FirmwareVersion") and dev.sw_version != a["FirmwareVersion"]:
            changes["sw_version"] = a["FirmwareVersion"]
        if a.get("Name") and dev.name != a["Name"]:
            changes["name"] = a["Name"]
        if changes:
            reg.async_update_device(dev.id, **changes)

    # ---- task detail (thumbnail, slicer data) ------------------------------

    def _schedule_task_fetch(self, task_id: str, delay: float) -> None:
        if self._task_fetch and not self._task_fetch.done():
            self._task_fetch.cancel()
        self._task_fetch = self.config_entry.async_create_background_task(
            self.hass, self._fetch_task(task_id, delay), f"{DOMAIN} task detail"
        )

    async def _fetch_task(self, task_id: str, delay: float) -> None:
        await asyncio.sleep(delay)
        for attempt in range(3):
            try:
                details = await self.client.task_details([task_id])
            except SDCPError as err:
                _LOGGER.debug("task detail %s failed: %s", task_id, err)
                await asyncio.sleep(5 * (attempt + 1))
                continue
            for d in details:
                if d.get("TaskId") == task_id or len(details) == 1:
                    self.task = d
                    self._push()
                    return
            return

    def thumbnail_url(self) -> str | None:
        thumb = (self.task or {}).get("Thumbnail")
        if not thumb or (self.task or {}).get("TaskId") != self.print_info.get("TaskId"):
            return None
        thumb = str(thumb)
        if thumb.startswith(("http://", "https://")):
            return thumb
        return f"http://{self.client.host}:{self.client.port}/{thumb.lstrip('/')}"

    # ---- video ------------------------------------------------------------

    async def async_video_url(self) -> str | None:
        """Enable the camera stream once and reuse the URL.

        The firmware has a use-after-free when the stream is toggled quickly
        (Cmd 386 enable/disable), so we only ever *enable*, at most once per
        the model's camera.reenable_after, and never disable from Home Assistant.
        """
        async with self._video_lock:
            now = time.monotonic()
            if self._video_url and now - self._video_at < self.model.camera.reenable_after:
                return self._video_url
            try:
                url = await self.client.enable_video(True)
            except SDCPError as err:
                _LOGGER.debug("video enable failed: %s", err)
                url = None
            self._video_at = now
            self._video_url = url or self._video_url or self.model.camera.fallback_url.format(host=self.client.host)
            return self._video_url
