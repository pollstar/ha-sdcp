"""SDCP (Smart Device Control Protocol) client — no Home Assistant imports.

SDCP is spoken by Chitu-based printers (ELEGOO Saturn/Mars/Jupiter resin
printers, ELEGOO Centauri Carbon and others):

* discovery: UDP datagram ``M99999`` to port 3000, the printer answers with a
  JSON description of itself;
* control: WebSocket ``ws://<ip>:3030/websocket``, JSON text frames.

Request (client -> printer), topic ``sdcp/request/<MainboardID>``::

    {"Id": "...", "Data": {"Cmd": N, "Data": {...}, "RequestID": "...",
     "MainboardID": "...", "TimeStamp": 0, "From": 0},
     "Topic": "sdcp/request/<MainboardID>"}

The response arrives on ``sdcp/response/<MainboardID>`` with the same
``RequestID`` and ``Data.Data.Ack`` (0 = accepted).  Status and attributes are
pushed on ``sdcp/status/<id>`` and ``sdcp/attributes/<id>``.

Most commands are silently ignored unless the request carries the real
MainboardID and the matching topic, so the client learns the id from the first
push (Cmd 0 works without it) before sending anything else.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import socket
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

WS_PORT = 3030
WS_PATH = "/websocket"
DISCOVERY_PORT = 3000
DISCOVERY_MSG = b"M99999"

# Command numbers (SDCP v3).
CMD_STATUS = 0
CMD_ATTRIBUTES = 1
CMD_START_PRINT = 128
CMD_PAUSE = 129
CMD_STOP = 130
CMD_RESUME = 131
CMD_RENAME = 192
CMD_FILE_TRANSFER_STOP = 255
CMD_FILE_LIST = 258
CMD_FILE_DELETE = 259
CMD_HISTORY = 320
CMD_TASK_DETAIL = 321
CMD_VIDEO_STREAM = 386
CMD_TIMELAPSE = 387

# Machine state — first element of Status.CurrentStatus.
MACHINE_STATUS = {
    0: "idle",
    1: "printing",
    2: "file_transferring",
    3: "exposure_testing",
    4: "devices_testing",
}

# Print stage — Status.PrintInfo.Status.  0..10 are from the SDCP v3 spec
# (8 = stopped confirmed live on the Saturn 4 Ultra 16K); 12+ are seen on FDM
# SDCP printers (Centauri Carbon).
PRINT_STATUS = {
    0: "idle",
    1: "homing",
    2: "dropping",
    3: "exposing",
    4: "lifting",
    5: "pausing",
    6: "paused",
    7: "stopping",
    8: "stopped",
    9: "complete",
    10: "file_checking",
    12: "resuming",
    13: "printing",
    15: "resuming",
    16: "heating",
    18: "heating",
    19: "leveling",
    20: "leveling",
    21: "leveling",
}
PRINT_ACTIVE = {
    "homing", "dropping", "exposing", "lifting", "file_checking",
    "resuming", "printing", "heating", "leveling",
}
PRINT_PAUSED = {"pausing", "paused"}
PRINT_FINAL = {"stopped", "complete"}

# PrintInfo.ErrorNumber.
PRINT_ERROR = {
    0: "none",
    1: "md5_check",
    2: "file_not_found",
    3: "file_read",
    4: "resolution_mismatch",
    5: "unknown_format",
    6: "wrong_model",
}

ACK_TEXT = {
    0: "ok",
    1: "busy",
    2: "file_not_found",
    3: "error_3",
    4: "error_4",
}


class SDCPError(Exception):
    """Base error."""


class SDCPConnectionError(SDCPError):
    """Printer is not reachable / connection dropped."""


class SDCPCommandError(SDCPError):
    """Printer refused a command (non-zero Ack)."""

    def __init__(self, cmd: int, ack: int) -> None:
        super().__init__(f"Cmd {cmd} refused: Ack={ack} ({ACK_TEXT.get(ack, 'unknown')})")
        self.cmd = cmd
        self.ack = ack


# --------------------------------------------------------------------------
# helpers on raw Status / Attributes dicts
# --------------------------------------------------------------------------


def machine_status_code(status: dict[str, Any] | None) -> int | None:
    """CurrentStatus is a list on v3 firmware and an int on some older ones."""
    if not status:
        return None
    cur = status.get("CurrentStatus")
    if isinstance(cur, list):
        return int(cur[0]) if cur else 0
    if isinstance(cur, (int, float)):
        return int(cur)
    return None


def print_info(status: dict[str, Any] | None) -> dict[str, Any]:
    return (status or {}).get("PrintInfo") or {}


def print_status_name(status: dict[str, Any] | None) -> str | None:
    code = print_info(status).get("Status")
    if code is None:
        return None
    return PRINT_STATUS.get(int(code), "unknown")


def is_fdm(status: dict[str, Any] | None) -> bool:
    """FDM SDCP printers report nozzle/bed temperatures."""
    return bool(status) and ("TempOfNozzle" in status or "TempOfHotbed" in status)


def ticks_scale(status: dict[str, Any] | None) -> float:
    """Seconds per tick: resin firmware counts ms, FDM firmware counts seconds."""
    return 1.0 if is_fdm(status) else 0.001


@dataclass
class DiscoveredPrinter:
    host: str
    mainboard_id: str | None
    name: str | None
    machine_name: str | None
    brand: str | None
    firmware: str | None
    protocol: str | None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def label(self) -> str:
        model = " ".join(x for x in (self.brand, self.machine_name) if x)
        name = self.name or model or "SDCP printer"
        return f"{name} — {model} ({self.host})" if model and model != name else f"{name} ({self.host})"


def split_host(value: str, default_port: int = WS_PORT) -> tuple[str, int]:
    """Accept ``host`` or ``host:port`` (IPv6 in brackets)."""
    value = value.strip()
    if value.startswith("["):
        host, _, rest = value[1:].partition("]")
        return host, int(rest[1:]) if rest.startswith(":") else default_port
    if value.count(":") == 1:
        host, port = value.split(":")
        return host, int(port)
    return value, default_port


def parse_discovery(payload: bytes, addr: str) -> DiscoveredPrinter | None:
    try:
        obj = json.loads(payload.decode("utf-8", "replace"))
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None
    data = obj.get("Data") if isinstance(obj.get("Data"), dict) else obj
    if isinstance(data.get("Attributes"), dict):  # some firmwares nest it
        data = data["Attributes"]
    return DiscoveredPrinter(
        host=data.get("MainboardIP") or addr,
        mainboard_id=data.get("MainboardID"),
        name=data.get("Name"),
        machine_name=data.get("MachineName"),
        brand=data.get("BrandName"),
        firmware=data.get("FirmwareVersion"),
        protocol=data.get("ProtocolVersion"),
        raw=obj,
    )


class _DiscoveryProtocol(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.found: dict[str, DiscoveredPrinter] = {}

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if data.strip() == DISCOVERY_MSG:  # our own broadcast looped back
            return
        p = parse_discovery(data, addr[0])
        if p:
            self.found[p.mainboard_id or p.host] = p


async def async_discover(
    timeout: float = 2.5, targets: list[str] | None = None
) -> list[DiscoveredPrinter]:
    """Broadcast M99999 and collect answers for ``timeout`` seconds."""
    loop = asyncio.get_running_loop()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setblocking(False)
    sock.bind(("", 0))
    transport, proto = await loop.create_datagram_endpoint(_DiscoveryProtocol, sock=sock)
    try:
        for target in targets or ["255.255.255.255"]:
            with contextlib.suppress(OSError):
                transport.sendto(DISCOVERY_MSG, (target, DISCOVERY_PORT))
        await asyncio.sleep(timeout)
    finally:
        transport.close()
    return list(proto.found.values())


# --------------------------------------------------------------------------
# WebSocket client
# --------------------------------------------------------------------------


class SDCPClient:
    """Keeps one WebSocket to the printer, reconnects, polls status.

    Callbacks are plain functions called from the event loop:
      on_status(dict), on_attributes(dict), on_connection(bool),
      on_message(topic, dict) for notice/error topics.
    """

    RECONNECT_MIN = 2.0
    RECONNECT_MAX = 60.0

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        port: int = WS_PORT,
        *,
        mainboard_id: str | None = None,
        poll_active: float = 3.0,
        poll_idle: float = 15.0,
        on_status: Callable[[dict[str, Any]], None] | None = None,
        on_attributes: Callable[[dict[str, Any]], None] | None = None,
        on_connection: Callable[[bool], None] | None = None,
        on_message: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.session = session
        self.host, self.port = split_host(host, port)
        self.mainboard_id = mainboard_id
        self.poll_active = poll_active
        self.poll_idle = poll_idle
        self.on_status = on_status
        self.on_attributes = on_attributes
        self.on_connection = on_connection
        self.on_message = on_message

        self.status: dict[str, Any] | None = None
        self.attributes: dict[str, Any] | None = None
        self.connected = False
        self.last_rx: float = 0.0

        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self._task: asyncio.Task | None = None
        self._closing = False
        self._send_lock = asyncio.Lock()
        self._id_event = asyncio.Event()
        if mainboard_id:
            self._id_event.set()
        self._first_status = asyncio.Event()
        self._first_attributes = asyncio.Event()

    @property
    def url(self) -> str:
        return f"ws://{self.host}:{self.port}{WS_PATH}"

    @property
    def active(self) -> bool:
        name = print_status_name(self.status)
        return machine_status_code(self.status) not in (None, 0) or name in PRINT_ACTIVE | PRINT_PAUSED

    # ---- lifecycle --------------------------------------------------------

    def start(self) -> None:
        if self._task is None:
            self._closing = False
            self._task = asyncio.ensure_future(self._run())

    async def stop(self) -> None:
        self._closing = True
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.close()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None
        self._fail_pending(SDCPConnectionError("client stopped"))

    async def wait_ready(self, timeout: float = 15.0) -> None:
        """Wait for the first Status and Attributes pushes."""
        try:
            await asyncio.wait_for(
                asyncio.gather(self._first_status.wait(), self._first_attributes.wait()),
                timeout,
            )
        except asyncio.TimeoutError as err:
            raise SDCPConnectionError(f"no answer from {self.host}") from err

    async def _run(self) -> None:
        delay = self.RECONNECT_MIN
        while not self._closing:
            try:
                await self._session_once()
                delay = self.RECONNECT_MIN
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 — any failure means reconnect
                _LOGGER.debug("SDCP %s: connection error: %r", self.host, err)
            self._set_connected(False)
            if self._closing:
                break
            await asyncio.sleep(delay)
            delay = min(delay * 2, self.RECONNECT_MAX)

    async def _session_once(self) -> None:
        # bounded connect: an unreachable host would otherwise sit in SYN_SENT
        ws = await asyncio.wait_for(
            self.session.ws_connect(self.url, autoping=True, max_msg_size=0, compress=0),
            10,
        )
        async with ws:
            self._ws = ws
            self.last_rx = time.monotonic()
            poller = asyncio.ensure_future(self._poll_loop())
            try:
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        self.last_rx = time.monotonic()
                        self._handle_text(msg.data)
                    elif msg.type == aiohttp.WSMsgType.BINARY:
                        self.last_rx = time.monotonic()
                        self._handle_text(msg.data.decode("utf-8", "replace"))
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break
            finally:
                poller.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await poller
                self._ws = None
                self._fail_pending(SDCPConnectionError("connection lost"))

    async def _poll_loop(self) -> None:
        """Ask for status regularly; also our liveness check."""
        await self._send_raw(CMD_STATUS, {}, "ha-status")
        await self._send_raw(CMD_ATTRIBUTES, {}, "ha-attr")
        last_attr = time.monotonic()
        while True:
            interval = self.poll_active if self.active else self.poll_idle
            await asyncio.sleep(interval)
            now = time.monotonic()
            if now - self.last_rx > max(30.0, interval * 3 + 10):
                _LOGGER.debug("SDCP %s: no data for %.0fs, reconnecting", self.host, now - self.last_rx)
                if self._ws is not None:
                    await self._ws.close()
                return
            await self._send_raw(CMD_STATUS, {}, "ha-status")
            if now - last_attr > 300:
                last_attr = now
                await self._send_raw(CMD_ATTRIBUTES, {}, "ha-attr")

    def _set_connected(self, value: bool) -> None:
        if value != self.connected:
            self.connected = value
            if self.on_connection:
                self.on_connection(value)

    def _fail_pending(self, err: Exception) -> None:
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(err)
        self._pending.clear()

    # ---- incoming ---------------------------------------------------------

    def _learn_id(self, obj: dict[str, Any]) -> None:
        mbid = obj.get("MainboardID")
        if not mbid and isinstance(obj.get("Data"), dict):
            mbid = obj["Data"].get("MainboardID")
        if not mbid and isinstance(obj.get("Attributes"), dict):
            mbid = obj["Attributes"].get("MainboardID")
        if mbid and mbid != self.mainboard_id:
            self.mainboard_id = mbid
        if self.mainboard_id:
            self._id_event.set()

    def _handle_text(self, text: str) -> None:
        try:
            obj = json.loads(text)
        except ValueError:
            _LOGGER.debug("SDCP %s: non-JSON frame: %.200s", self.host, text)
            return
        if not isinstance(obj, dict):
            return
        self._learn_id(obj)
        self._set_connected(True)
        topic = str(obj.get("Topic") or "")

        if isinstance(obj.get("Status"), dict):
            self.status = obj["Status"]
            self._first_status.set()
            if self.on_status:
                self.on_status(self.status)
        if isinstance(obj.get("Attributes"), dict):
            self.attributes = obj["Attributes"]
            self._first_attributes.set()
            if self.on_attributes:
                self.on_attributes(self.attributes)

        if topic.startswith("sdcp/response/") or (
            isinstance(obj.get("Data"), dict) and "RequestID" in obj["Data"]
        ):
            data = obj.get("Data") or {}
            rid = data.get("RequestID")
            fut = self._pending.pop(rid, None) if rid else None
            if fut is not None and not fut.done():
                inner = data.get("Data")
                fut.set_result(inner if isinstance(inner, dict) else {})
            return

        if topic.startswith(("sdcp/notice/", "sdcp/error/")) and self.on_message:
            self.on_message(topic.split("/")[1], obj)

    # ---- outgoing ---------------------------------------------------------

    def _envelope(self, cmd: int, data: dict[str, Any], rid: str) -> dict[str, Any]:
        mbid = self.mainboard_id or ""
        return {
            "Id": uuid.uuid4().hex,
            "Data": {
                "Cmd": cmd,
                "Data": data,
                "RequestID": rid,
                "MainboardID": mbid,
                "TimeStamp": int(time.time()),
                "From": 0,
            },
            "Topic": f"sdcp/request/{mbid}",
        }

    async def _send_raw(self, cmd: int, data: dict[str, Any], rid: str) -> None:
        ws = self._ws
        if ws is None or ws.closed:
            raise SDCPConnectionError("not connected")
        async with self._send_lock:
            await ws.send_str(json.dumps(self._envelope(cmd, data, rid), separators=(",", ":")))

    async def request(
        self,
        cmd: int,
        data: dict[str, Any] | None = None,
        *,
        timeout: float = 10.0,
        check_ack: bool = True,
    ) -> dict[str, Any]:
        """Send a command, wait for its response and return ``Data.Data``."""
        if cmd != CMD_STATUS and not self._id_event.is_set():
            try:
                await asyncio.wait_for(self._id_event.wait(), timeout)
            except asyncio.TimeoutError as err:
                raise SDCPConnectionError("MainboardID unknown yet") from err
        rid = uuid.uuid4().hex
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        try:
            await self._send_raw(cmd, data or {}, rid)
            result = await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError as err:
            raise SDCPConnectionError(f"Cmd {cmd}: no response") from err
        finally:
            self._pending.pop(rid, None)
        ack = result.get("Ack", 0)
        if check_ack and ack not in (0, None):
            raise SDCPCommandError(cmd, int(ack))
        return result

    # ---- convenience ------------------------------------------------------

    async def refresh(self) -> None:
        await self._send_raw(CMD_STATUS, {}, "ha-status")
        await self._send_raw(CMD_ATTRIBUTES, {}, "ha-attr")

    async def pause(self) -> None:
        await self.request(CMD_PAUSE)

    async def resume(self) -> None:
        await self.request(CMD_RESUME)

    async def stop_print(self) -> None:
        await self.request(CMD_STOP)

    async def start_print(self, filename: str, start_layer: int = 0) -> None:
        await self.request(CMD_START_PRINT, {"Filename": filename, "StartLayer": int(start_layer)})

    async def set_timelapse(self, enable: bool) -> None:
        await self.request(CMD_TIMELAPSE, {"Enable": 1 if enable else 0})

    async def rename(self, name: str) -> None:
        await self.request(CMD_RENAME, {"Name": name})

    async def enable_video(self, enable: bool = True) -> str | None:
        res = await self.request(CMD_VIDEO_STREAM, {"Enable": 1 if enable else 0}, check_ack=False)
        return res.get("VideoUrl") or None

    async def list_files(self, path: str = "/") -> list[dict[str, Any]]:
        res = await self.request(CMD_FILE_LIST, {"Url": path})
        return [e for e in res.get("FileList") or [] if isinstance(e, dict) and e.get("name")]

    async def delete_files(self, files: list[str], folders: list[str] | None = None) -> list[str]:
        res = await self.request(
            CMD_FILE_DELETE, {"FileList": list(files), "FolderList": list(folders or [])}, check_ack=False
        )
        return list(res.get("ErrData") or [])

    async def history(self) -> list[str]:
        res = await self.request(CMD_HISTORY)
        return [str(x) for x in res.get("HistoryData") or []]

    async def task_details(self, task_ids: list[str]) -> list[dict[str, Any]]:
        res = await self.request(CMD_TASK_DETAIL, {"Id": list(task_ids)})
        return [d for d in res.get("HistoryDetailList") or [] if isinstance(d, dict)]
