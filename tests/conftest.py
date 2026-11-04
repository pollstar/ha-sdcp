"""Fixtures."""
import sys
from pathlib import Path

import pytest
from aiohttp import web

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "stubs"))
from printer_sim import Printer, make_app  # noqa: E402

pytest_plugins = ["pytest_homeassistant_custom_component"]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
async def printer(socket_enabled):
    p = Printer()
    runner = web.AppRunner(make_app(p), shutdown_timeout=0.2)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    p.port = site._server.sockets[0].getsockname()[1]
    p.host = f"127.0.0.1:{p.port}"
    p.runner = runner
    yield p
    for ws in list(p.clients):
        await ws.close()
    await runner.cleanup()
