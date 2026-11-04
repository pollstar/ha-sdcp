"""End-to-end tests against the SDCP printer emulator."""
from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events

from custom_components.sdcp.const import DOMAIN, EVENT_PRINTER
from printer_sim import MBID

DISCOVER = "custom_components.sdcp.config_flow.async_discover"


async def _setup(hass, printer) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=MBID, data={CONF_HOST: printer.host}, title="SATURN")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _eid(hass, key, platform="sensor"):
    return er.async_get(hass).async_get_entity_id(platform, DOMAIN, f"{MBID}_{key}")


async def _wait(hass, pred, n=50):
    import asyncio
    for _ in range(n):
        await hass.async_block_till_done()
        if pred():
            return True
        await asyncio.sleep(0.05)
    return pred()


async def test_config_flow(hass: HomeAssistant, printer) -> None:
    with patch(DISCOVER, return_value=[]):
        r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        assert r["type"] == "form"
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {CONF_HOST: printer.host})
    assert r["type"] == "create_entry", r
    assert r["result"].unique_id == MBID
    assert r["title"] == "SATURN (Saturn 4 Ultra 16K)"


async def test_config_flow_cannot_connect(hass: HomeAssistant, socket_enabled) -> None:
    with patch(DISCOVER, return_value=[]), patch("custom_components.sdcp.config_flow.SDCPClient.wait_ready",
                                                  side_effect=__import__("custom_components.sdcp.protocol", fromlist=["x"]).SDCPConnectionError("x")):
        r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {CONF_HOST: "127.0.0.1:1"})
    assert r["errors"] == {"base": "cannot_connect"}


async def test_idle_state(hass: HomeAssistant, printer) -> None:
    entry = await _setup(hass, printer)
    assert entry.state is ConfigEntryState.LOADED
    dev = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, MBID)})
    assert dev.model == "Saturn 4 Ultra 16K" and dev.manufacturer == "ELEGOO" and dev.sw_version == "V1.2.3"
    assert hass.states.get(_eid(hass, "print_status")).state == "idle"
    assert hass.states.get(_eid(hass, "machine_status")).state == "idle"
    assert float(hass.states.get(_eid(hass, "vat_temp")).state) == 29
    assert float(hass.states.get(_eid(hass, "release_film")).state) == 23087
    assert float(hass.states.get(_eid(hass, "release_film_wear")).state) == pytest.approx(38.5, 0.1)
    assert hass.states.get(_eid(hass, "connected", "binary_sensor")).state == "on"
    assert hass.states.get(_eid(hass, "health_TankStatus", "binary_sensor")).state == "off"
    assert hass.states.get(_eid(hass, "timelapse", "switch")).state == "off"
    # FDM sensors are not created for a resin printer
    assert _eid(hass, "nozzle_temp") is None
    # camera exists but is disabled by default
    cam = er.async_get(hass).async_get(_eid(hass, "camera", "camera"))
    assert cam is not None and cam.disabled_by is not None
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_printing_controls_and_events(hass: HomeAssistant, printer) -> None:
    printer.set_printing()
    await _setup(hass, printer)
    events = async_capture_events(hass, EVENT_PRINTER)
    st = hass.states.get
    assert st(_eid(hass, "print_status")).state == "exposing"
    assert float(st(_eid(hass, "progress")).state) == pytest.approx(38.5, 0.1)
    assert float(st(_eid(hass, "print_remaining")).state) == pytest.approx((93224007 - 31865667) / 60000, 0.01)
    assert st(_eid(hass, "print_finish")).state not in ("unknown", "unavailable")
    assert st(_eid(hass, "printing", "binary_sensor")).state == "on"
    # task detail (Cmd 321) arrives in the background
    assert await _wait(hass, lambda: "layer_height" in st(_eid(hass, "filename")).attributes, 100)
    fa = st(_eid(hass, "filename")).attributes
    assert fa["z_height"] == pytest.approx(60.8) and fa["resin_volume"] == 12.4
    assert fa["thumbnail"].endswith("/thumb/1e129be0-1dd2-11b2-a533-d18a25d8094d.png")

    await hass.services.async_call("button", "press", {"entity_id": _eid(hass, "pause", "button")}, blocking=True)
    assert await _wait(hass, lambda: st(_eid(hass, "print_status")).state == "paused")
    with pytest.raises(HomeAssistantError):  # firmware refuses pause while paused
        await hass.services.async_call("button", "press", {"entity_id": _eid(hass, "pause", "button")}, blocking=True)
    await hass.services.async_call("button", "press", {"entity_id": _eid(hass, "resume", "button")}, blocking=True)
    assert await _wait(hass, lambda: st(_eid(hass, "print_status")).state == "exposing")
    await hass.services.async_call("button", "press", {"entity_id": _eid(hass, "stop", "button")}, blocking=True)
    assert await _wait(hass, lambda: st(_eid(hass, "print_status")).state == "stopped")
    assert float(st(_eid(hass, "print_remaining")).state) == 0
    kinds = [e.data["type"] for e in events]
    assert kinds == ["print_paused", "print_resumed", "print_stopped"], kinds

    await hass.services.async_call("switch", "turn_on", {"entity_id": _eid(hass, "timelapse", "switch")}, blocking=True)
    assert await _wait(hass, lambda: st(_eid(hass, "timelapse", "switch")).state == "on")


async def test_services_and_ws(hass: HomeAssistant, printer, hass_ws_client) -> None:
    await _setup(hass, printer)
    dev = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, MBID)})
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(DOMAIN, "start_print", {"device_id": dev.id, "filename": "nope.ctb"}, blocking=True)
    await hass.services.async_call(DOMAIN, "start_print", {"device_id": dev.id, "filename": "adapter_v2.ctb"}, blocking=True)
    assert await _wait(hass, lambda: hass.states.get(_eid(hass, "print_status")).state == "exposing")
    assert hass.states.get(_eid(hass, "filename")).state == "adapter_v2.ctb"
    sent = [r for r in printer.received if r["Data"]["Cmd"] == 128][-1]
    assert sent["Topic"] == f"sdcp/request/{MBID}" and sent["Data"]["MainboardID"] == MBID

    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "sdcp/files", "device_id": dev.id})
    res = await client.receive_json()
    assert res["success"] and res["result"]["items"][0] == {"path": "/local", "dir": True, "used": 3587702784,
                                                           "total": 6742212608, "storage": 0}
    await client.send_json({"id": 2, "type": "sdcp/files", "device_id": dev.id, "path": "/local"})
    res = await client.receive_json()
    assert [i["path"] for i in res["result"]["items"]][0] == "/local/lock_holder.ctb"

    await hass.services.async_call(DOMAIN, "delete_files", {"device_id": dev.id, "files": ["/local/minis_batch.goo"]},
                                   blocking=True, return_response=False)
    assert "/local/minis_batch.goo" not in printer.files["/local"]
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(DOMAIN, "delete_files", {"device_id": dev.id, "files": ["/local/x.ctb"]}, blocking=True)


async def test_thumbnail_and_card(hass: HomeAssistant, printer, hass_client, hass_client_no_auth) -> None:
    printer.set_printing()
    await _setup(hass, printer)
    eid = _eid(hass, "thumbnail", "image")
    assert await _wait(hass, lambda: hass.states.get(eid).state not in ("unavailable", "unknown"), 100)
    client = await hass_client()
    resp = await client.get(f"/api/image_proxy/{eid}")
    assert resp.status == 200 and (await resp.read()).startswith(b"\x89PNG")

    assert (await anon_get(hass_client_no_auth, "/sdcp_static/sdcp-printer-card.js")) == 404


async def anon_get(factory, path):
    client = await factory()
    return (await client.get(path)).status


async def test_reconnect(hass: HomeAssistant, printer) -> None:
    entry = await _setup(hass, printer)
    coord = entry.runtime_data
    coord.client.RECONNECT_MIN = 0.1
    for ws in list(printer.clients):
        await ws.close()
    assert await _wait(hass, lambda: hass.states.get(_eid(hass, "vat_temp")).state == "unavailable")
    assert hass.states.get(_eid(hass, "connected", "binary_sensor")).state == "off"
    assert await _wait(hass, lambda: hass.states.get(_eid(hass, "vat_temp")).state == "29.0", 100)


async def test_legacy_resource_removed(hass: HomeAssistant, printer) -> None:
    from homeassistant.setup import async_setup_component
    assert await async_setup_component(hass, "lovelace", {})
    res = hass.data["lovelace"].resources
    await res.async_get_info()
    await res.async_create_item({"res_type": "module", "url": "/sdcp_static/sdcp-printer-card.js?v=old"})
    keep = await res.async_create_item({"res_type": "module", "url": "/hacsfiles/sdcp-printer-card/sdcp-printer-card.js"})
    await _setup(hass, printer)
    assert [i["id"] for i in res.async_items()] == [keep["id"]]


async def test_printer_profile(hass: HomeAssistant, printer) -> None:
    await _setup(hass, printer)
    a = hass.states.get(_eid(hass, "machine_status")).attributes
    assert a["printer_profile"] == "saturn-4-ultra" and a["printer_kind"] == "resin"


async def test_camera_stream_source(hass: HomeAssistant, printer) -> None:
    from custom_components.sdcp.camera import PrinterCamera, go2rtc_source
    entry = await _setup(hass, printer)
    cam = PrinterCamera(entry.runtime_data)
    cam.hass = hass
    assert await cam.stream_source() == "rtsp://127.0.0.1:554/video"
    hass.config.components.add("go2rtc")
    assert await cam.stream_source() == go2rtc_source("rtsp://127.0.0.1:554/video")
    assert go2rtc_source("rtsp://x/video") == "ffmpeg:rtsp://x/video#input=rtsp/udp#video=copy"
    # the stream is enabled once and reused (no Cmd 386 toggling)
    assert sum(1 for r in printer.received if r["Data"]["Cmd"] == 386) == 1
