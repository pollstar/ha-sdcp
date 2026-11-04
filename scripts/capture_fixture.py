#!/usr/bin/env python3
"""Capture an SDCP printer's Attributes and Status into a test fixture.

    pip install aiohttp
    python scripts/capture_fixture.py 192.168.1.50 tests/fixtures/brand_model.json

Run it while the printer prints (best) or idles. The script talks only Cmd 0 and
Cmd 1 (read-only). The printer's IP is replaced with 127.0.0.1 so the fixture
works with the emulator; pass --keep-id to keep the real MainboardID (it is
replaced with a placeholder by default).

Then edit the "expect" block: which model and card profile should match, the
stage the capture shows, minutes remaining, entities that must exist / must not.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import aiohttp

_PROTO = Path(__file__).resolve().parents[1] / "custom_components" / "sdcp" / "protocol.py"
_spec = importlib.util.spec_from_file_location("sdcp_protocol", _PROTO)
proto = importlib.util.module_from_spec(_spec)
sys.modules["sdcp_protocol"] = proto
_spec.loader.exec_module(proto)


async def capture(host: str, keep_id: bool) -> dict:
    async with aiohttp.ClientSession() as session:
        client = proto.SDCPClient(session, host)
        client.start()
        try:
            await client.wait_ready(20)
            await asyncio.sleep(1.5)  # let a fresh status push arrive
            attrs = dict(client.attributes or {})
            status = dict(client.status or {})
        finally:
            await client.stop()
    attrs["MainboardIP"] = "127.0.0.1"
    if not keep_id and attrs.get("MainboardID"):
        attrs["MainboardID"] = "0123456789abcdef"
    stage = proto.print_status_name(status)
    return {
        "_source": f"Live capture of {attrs.get('BrandName', '')} {attrs.get('MachineName', '')}, "
                   f"firmware {attrs.get('FirmwareVersion', '?')}, protocol {attrs.get('ProtocolVersion', '?')}.",
        "attributes": attrs,
        "status": status,
        "expect": {
            "model": "generic-fdm" if proto.is_fdm(status) else "generic-resin",
            "profile": "generic-fdm" if proto.is_fdm(status) else "generic-resin",
            "kind": "fdm" if proto.is_fdm(status) else "resin",
            "print_status": stage,
            "remaining_min": 0,
            "sensors": [],
            "absent": [],
            "camera": attrs.get("CameraStatus") == 1 or "VIDEO_STREAM" in (attrs.get("Capabilities") or []),
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("host", help="printer IP (or host:port)")
    ap.add_argument("out", help="fixture file to write")
    ap.add_argument("--keep-id", action="store_true", help="keep the real MainboardID")
    args = ap.parse_args()
    fx = asyncio.run(capture(args.host, args.keep_id))
    Path(args.out).write_text(json.dumps(fx, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {args.out}: {fx['attributes'].get('MachineName')} · stage {fx['expect']['print_status']}")
    print("now fill in the 'expect' block (model, remaining_min, sensors, absent)")


if __name__ == "__main__":
    main()
