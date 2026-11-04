"""Every capture in tests/fixtures/ is set up against the emulator and checked
against its "expect" block. Adding a printer = adding a fixture (see CONTRIBUTING.md)."""

import json
from pathlib import Path

import pytest
from aiohttp import web
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sdcp.const import DOMAIN
from custom_components.sdcp.printers import REGISTRY, resolve
from printer_sim import Printer, load_fixture, make_app

FIXTURES = sorted((Path(__file__).parent / "fixtures").glob("*.json"))


@pytest.fixture(params=FIXTURES, ids=[f.stem for f in FIXTURES])
async def sim(request, socket_enabled):
    fx = load_fixture(request.param)
    p = Printer(fx)
    runner = web.AppRunner(make_app(p), shutdown_timeout=0.2)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    p.host = f"127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    yield p, fx
    for ws in list(p.clients):
        await ws.close()
    await runner.cleanup()


def test_fixture_resolves_to_expected_model():
    for path in FIXTURES:
        fx = json.loads(path.read_text(encoding="utf-8"))
        m = resolve(fx["attributes"], fx["status"])
        assert m.id == fx["expect"]["model"], path.name
        assert m.profile == fx["expect"]["profile"], path.name
        assert m.kind == fx["expect"]["kind"], path.name


def test_registry_is_consistent():
    ids = [m.id for m in REGISTRY]
    assert len(ids) == len(set(ids))
    assert {"generic-resin", "generic-fdm"} <= set(ids)
    for m in REGISTRY:
        assert m.kind in ("resin", "fdm"), m.id
        assert m.tick_seconds > 0, m.id
        assert m.camera.rtsp_transport in ("udp", "tcp"), m.id
        assert set(m.active_stages).isdisjoint(m.paused_stages), m.id


async def test_fixture_in_home_assistant(hass: HomeAssistant, sim) -> None:
    printer, fx = sim
    exp = fx["expect"]
    mbid = fx["attributes"]["MainboardID"]
    entry = MockConfigEntry(domain=DOMAIN, unique_id=mbid, data={CONF_HOST: printer.host})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    reg = er.async_get(hass)

    def eid(key, platform="sensor"):
        return reg.async_get_entity_id(platform, DOMAIN, f"{mbid}_{key}")

    ms = hass.states.get(eid("machine_status"))
    assert ms.attributes["printer_model"] == exp["model"]
    assert ms.attributes["printer_profile"] == exp["profile"]
    assert ms.attributes["printer_kind"] == exp["kind"]
    assert hass.states.get(eid("print_status")).state == exp["print_status"]
    assert float(hass.states.get(eid("print_remaining")).state) == pytest.approx(exp["remaining_min"], abs=0.2)
    for key in exp["sensors"]:
        assert eid(key) is not None, f"missing sensor {key}"
    for key in exp["absent"]:
        assert eid(key) is None, f"unexpected sensor {key}"
    assert (eid("camera", "camera") is not None) == exp["camera"]
    for state in hass.states.async_all():
        if state.entity_id.startswith(("sensor.", "binary_sensor.")) and reg.async_get(state.entity_id).platform == DOMAIN:
            assert state.state != "unavailable", state.entity_id
    assert await hass.config_entries.async_unload(entry.entry_id)
