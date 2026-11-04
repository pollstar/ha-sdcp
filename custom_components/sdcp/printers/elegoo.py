"""ELEGOO printers."""

from __future__ import annotations

from homeassistant.const import EntityCategory

from ..descriptions import SDCPSensorDescription, has_status
from ..protocol import PRINT_STATUS
from . import CameraProfile, PrinterModel, extend, name_contains, register

# Saturn 4 Ultra / Saturn 4 Ultra 16K — verified on a 16K with firmware V1.2.3 / V1.5.1:
# stage 8 = stopped, timers in ms, RTSP camera on :554 that refuses TCP, and the
# firmware crashes (use-after-free) when Cmd 386 is toggled quickly.
SATURN_4_ULTRA = register(PrinterModel(
    id="elegoo-saturn-4-ultra",
    name="ELEGOO Saturn 4 Ultra / 16K",
    kind="resin",
    match=name_contains("saturn 4 ultra"),
    priority=10,
    card_profile="saturn-4-ultra",
    tick_seconds=0.001,
    camera=CameraProfile(rtsp_transport="udp", fallback_url="rtsp://{host}:554/video", reenable_after=600.0),
))

# Centauri Carbon (FDM). Timers in seconds and the extra stage codes come from
# community reports of its SDCP v3 firmware and are not verified on hardware here.
CENTAURI_CARBON = register(PrinterModel(
    id="elegoo-centauri-carbon",
    name="ELEGOO Centauri Carbon",
    kind="fdm",
    match=name_contains("centauri carbon", "centauri"),
    priority=10,
    card_profile="generic-fdm",
    tick_seconds=1.0,
    print_status=extend(PRINT_STATUS, **{"13": "printing", "16": "heating", "20": "leveling", "21": "leveling"}),
    sensors=(
        # example of a model-specific entity: toolhead position as reported by the firmware
        SDCPSensorDescription(
            key="toolhead_position",
            translation_key="toolhead_position",
            entity_category=EntityCategory.DIAGNOSTIC,
            entity_registry_enabled_default=False,
            value_fn=lambda c: c.status.get("CurrenCoord"),  # sic, firmware spelling
            exists_fn=has_status("CurrenCoord"),
        ),
    ),
))
