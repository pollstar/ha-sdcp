"""Minimal SDCP v3 printer emulator (aiohttp), modelled on live Saturn 4 Ultra 16K frames.

Run standalone:  python tests/printer_sim.py [port]
"""

from __future__ import annotations

import asyncio
import copy
import json
import sys
import time

from aiohttp import web

MBID = "c439447916520100"

ATTRIBUTES = {
    "Name": "SATURN", "MachineName": "Saturn 4 Ultra 16K", "BrandName": "ELEGOO",
    "ProtocolVersion": "V3.0.0", "FirmwareVersion": "V1.2.3", "Resolution": "15120x6230",
    "XYZsize": "218.88x128.88x220", "MainboardIP": "127.0.0.1", "MainboardID": MBID,
    "SDCPStatus": 0, "MaximumCloudSDCPSercicesAllowed": 0, "NumberOfCloudSDCPServicesConnected": 0,
    "NumberOfVideoStreamConnected": 0, "MaximumVideoStreamAllowed": 2, "NetworkStatus": "",
    "UsbDiskStatus": 0, "Capabilities": ["FILE_TRANSFER", "PRINT_CONTROL", "VIDEO_STREAM"],
    "SupportFileType": ["CTB", "GOO"],
    "DevicesStatus": {"TempSensorStatusOfUVLED": 1, "TankStatus": 1, "LCDStatus": 1, "SgStatus": 1,
                      "ZMotorStatus": 1, "RotateMotorStatus": 1, "RelaseFilmState": 1},
    "ReleaseFilmMax": 60000, "CameraStatus": 1, "RemainingMemory": 3154509824,
    "TLPNoCapPos": 50, "TLPStartCapPos": 30, "TLPInterLayers": 10,
}

IDLE_STATUS = {
    "CurrentStatus": [0], "PrintScreen": 550531.23, "ReleaseFilm": 23087, "TempOfUVLED": 31.5,
    "TimeLapseStatus": 0, "HeatStatus": 1, "TempOfTank": 29, "TempTargetTank": 30,
    "PrintInfo": {"Status": 0, "CurrentLayer": 0, "TotalLayer": 0, "CurrentTicks": 0, "TotalTicks": 0,
                  "ErrorNumber": 0, "Filename": "", "TaskId": ""},
}

FILES = {
    "/local": ["/local/lock_holder.ctb", "/local/adapter_v2.ctb", "/local/minis_batch.goo"],
}

# 1x1 PNG
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360f8cfc0f01f0005fe02fea7d7a0"
    "a00000000049454e44ae426082"
)


def load_fixture(path) -> dict:
    """A capture made with scripts/capture_fixture.py (or by hand)."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class Printer:
    def __init__(self, fixture: dict | None = None) -> None:
        self.status = copy.deepcopy(fixture["status"] if fixture else IDLE_STATUS)
        self.attrs = copy.deepcopy(fixture["attributes"] if fixture else ATTRIBUTES)
        self.mbid = self.attrs.get("MainboardID", MBID)
        self.files = copy.deepcopy(FILES)
        self.clients: set[web.WebSocketResponse] = set()
        self.received: list[dict] = []
        self.task_id = ""
        self.mute = False  # simulate a hung printer

    # ---- push helpers ----------------------------------------------------
    async def push(self, ws, obj) -> None:
        if not ws.closed:
            await ws.send_str(json.dumps(obj))

    def status_msg(self):
        return {"Status": self.status, "MainboardID": self.mbid, "TimeStamp": int(time.time()),
                "Topic": f"sdcp/status/{self.mbid}"}

    def attr_msg(self):
        return {"Attributes": self.attrs, "MainboardID": self.mbid, "TimeStamp": int(time.time()),
                "Topic": f"sdcp/attributes/{self.mbid}"}

    async def broadcast_status(self):
        for ws in list(self.clients):
            await self.push(ws, self.status_msg())

    # ---- state changes ---------------------------------------------------
    def start(self, name: str, layer: int = 0) -> int:
        base = name.split("/")[-1]
        if not any(f.endswith("/" + base) for f in self.files["/local"]):
            return 2
        self.task_id = f"task-{int(time.time() * 1000)}"
        self.status["CurrentStatus"] = [1]
        self.status["PrintInfo"] = {"Status": 3, "CurrentLayer": layer, "TotalLayer": 7901,
                                    "CurrentTicks": 1000, "TotalTicks": 93224007, "ErrorNumber": 0,
                                    "Filename": base, "TaskId": self.task_id}
        return 0

    def set_printing(self, layer=3040, total=7901, stage=3):
        self.task_id = "1e129be0-1dd2-11b2-a533-d18a25d8094d"
        self.status["CurrentStatus"] = [1]
        self.status["PrintInfo"] = {"Status": stage, "CurrentLayer": layer, "TotalLayer": total,
                                    "CurrentTicks": 31865667, "TotalTicks": 93224007, "ErrorNumber": 0,
                                    "Filename": "lock_holder.STL_0.020_4.000_2026_10_29_10_55.ctb",
                                    "TaskId": self.task_id}

    # ---- request handling ------------------------------------------------
    async def handle(self, ws, req: dict) -> None:
        self.received.append(req)
        d = req.get("Data") or {}
        cmd = d.get("Cmd")
        body = d.get("Data") or {}
        # real firmware ignores most commands without MainboardID + topic
        if cmd != 0 and (d.get("MainboardID") != self.mbid or req.get("Topic") != f"sdcp/request/{self.mbid}"):
            return
        out: dict = {"Ack": 0}
        pi = self.status["PrintInfo"]
        if cmd == 0:
            pass
        elif cmd == 1:
            pass
        elif cmd == 128:
            out["Ack"] = self.start(body.get("Filename", ""), int(body.get("StartLayer") or 0))
        elif cmd == 129:
            if pi["Status"] in (3, 4, 2):
                pi["Status"] = 6
            else:
                out["Ack"] = 1
        elif cmd == 131:
            if pi["Status"] == 6:
                pi["Status"] = 3
            else:
                out["Ack"] = 1
        elif cmd == 130:
            pi["Status"] = 8
            self.status["CurrentStatus"] = [0]
        elif cmd == 258:
            url = body.get("Url", "/")
            if url == "/":
                out["FileList"] = [{"name": "/local", "usedSize": 3587702784, "totalSize": 6742212608,
                                    "storageType": 0, "type": 0}]
            else:
                out["FileList"] = [{"name": f, "type": 1} for f in self.files.get(url.rstrip("/"), [])]
        elif cmd == 259:
            err = []
            for f in body.get("FileList") or []:
                if f in self.files["/local"]:
                    self.files["/local"].remove(f)
                else:
                    err.append(f)
            out["ErrData"] = err
        elif cmd == 320:
            out["HistoryData"] = [self.task_id] if self.task_id else []
        elif cmd == 321:
            out["HistoryDetailList"] = [{
                "TaskId": i, "Thumbnail": f"/thumb/{i}.png", "TaskStatus": 0,
                "SliceInformation": {"layer_height": 0.02, "volume": 12.4, "weight": 13.6,
                                     "normal_layer_exposure_time": 4.0, "bottom_layer_numbers": 6,
                                     "bottom_layer_exposure_time": 30.0, "print_time": 93224,
                                     "machine_name": "ELEGOO Saturn 4 Ultra 16K"},
            } for i in body.get("Id") or []]
        elif cmd == 386:
            out["VideoUrl"] = "rtsp://127.0.0.1:554/video"
        elif cmd == 387:
            self.status["TimeLapseStatus"] = 1 if body.get("Enable") else 0
        elif cmd == 192:
            self.attrs["Name"] = body.get("Name", "")
        else:
            out["Ack"] = 1
        await self.push(ws, {"Id": req.get("Id"), "Data": {"Cmd": cmd, "Data": out,
                             "RequestID": d.get("RequestID"), "MainboardID": self.mbid,
                             "TimeStamp": int(time.time())}, "Topic": f"sdcp/response/{self.mbid}"})
        if cmd == 0 or cmd in (128, 129, 130, 131, 387):
            await self.push(ws, self.status_msg())
        if cmd in (1, 192):
            await self.push(ws, self.attr_msg())


def make_app(printer: Printer) -> web.Application:
    async def ws_handler(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        printer.clients.add(ws)
        await printer.push(ws, printer.status_msg())  # hello
        try:
            async for msg in ws:
                if msg.type == 1 and not printer.mute:
                    await printer.handle(ws, json.loads(msg.data))
        finally:
            printer.clients.discard(ws)
        return ws

    async def thumb(request):
        return web.Response(body=PNG, content_type="image/png")

    app = web.Application()
    app.router.add_get("/websocket", ws_handler)
    app.router.add_get("/thumb/{name}", thumb)
    return app


async def _main(port: int) -> None:
    printer = Printer()
    printer.set_printing()
    runner = web.AppRunner(make_app(printer))
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    print(f"SDCP emulator on ws://0.0.0.0:{port}/websocket")
    while True:
        await asyncio.sleep(2)
        pi = printer.status["PrintInfo"]
        if pi["Status"] in (2, 3, 4) and pi["CurrentLayer"] < pi["TotalLayer"]:
            pi["CurrentLayer"] += 1
            pi["CurrentTicks"] += 11800
            pi["Status"] = 3 if pi["Status"] != 3 else 4


if __name__ == "__main__":
    asyncio.run(_main(int(sys.argv[1]) if len(sys.argv) > 1 else 3030))
