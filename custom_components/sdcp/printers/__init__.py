"""Printer models: everything that differs between SDCP printers lives here.

The core (protocol, coordinator, platforms) speaks plain SDCP v3. A
``PrinterModel`` tells it how a particular machine deviates: how to recognise
it, its stage / state / error codes, the unit of the print timer, its self-test
checks, camera transport, extra entities, and which drawing the Lovelace card
should load.

To add a printer, create a module next to this file that calls ``register()``
and list it in ``MODULES`` below. See CONTRIBUTING.md.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from ..protocol import MACHINE_STATUS, PRINT_ERROR, PRINT_STATUS, is_fdm

Match = Callable[[dict[str, Any], dict[str, Any]], bool]

# SDCP v3 stage names that mean "a job is running" / "a job is paused".
ACTIVE_STAGES = frozenset({
    "homing", "dropping", "exposing", "lifting", "file_checking",
    "resuming", "printing", "heating", "leveling",
})
PAUSED_STAGES = frozenset({"pausing", "paused"})

# Attributes.DevicesStatus key -> translation key of the self-test "problem" sensor.
HEALTH_KEYS: Mapping[str, str] = {
    "TempSensorStatusOfUVLED": "health_uv_sensor",
    "TankStatus": "health_tank",
    "LCDStatus": "health_lcd",
    "SgStatus": "health_strain_gauge",
    "ZMotorStatus": "health_z_motor",
    "RotateMotorStatus": "health_tilt_motor",
    "RelaseFilmState": "health_release_film",  # sic, firmware typo
    "ReleaseFilmState": "health_release_film",
    "XMotorStatus": "health_x_motor",
    "YMotorStatus": "health_y_motor",
}


@dataclass(frozen=True, kw_only=True)
class CameraProfile:
    """How to reach the printer's video after Cmd 386."""

    rtsp_transport: str = "udp"        # "udp" or "tcp"
    fallback_url: str = "rtsp://{host}:554/video"  # when Cmd 386 returns no VideoUrl
    reenable_after: float = 600.0      # s between Cmd 386 enables; the stream is never disabled


@dataclass(frozen=True, kw_only=True)
class PrinterModel:
    """Description of one printer (or a family of printers)."""

    id: str                            # stable identifier, e.g. "elegoo-saturn-4-ultra"
    name: str                          # human-readable, for logs and diagnostics
    kind: str                          # "resin" or "fdm"
    match: Match = lambda attrs, status: False
    priority: int = 0                  # higher wins when several models match
    card_profile: str | None = None    # drawing of sdcp-printer-card; default generic-<kind>
    tick_seconds: float = 0.001        # unit of PrintInfo.CurrentTicks / TotalTicks
    machine_status: Mapping[int, str] = field(default_factory=lambda: dict(MACHINE_STATUS))
    print_status: Mapping[int, str] = field(default_factory=lambda: dict(PRINT_STATUS))
    print_errors: Mapping[int, str] = field(default_factory=lambda: dict(PRINT_ERROR))
    active_stages: frozenset[str] = ACTIVE_STAGES
    paused_stages: frozenset[str] = PAUSED_STAGES
    health_keys: Mapping[str, str] = field(default_factory=lambda: dict(HEALTH_KEYS))
    camera: CameraProfile = CameraProfile()
    sensors: tuple = ()                # extra SDCPSensorDescription
    binary_sensors: tuple = ()         # extra SDCPBinaryDescription
    buttons: tuple = ()                # extra SDCPButtonDescription

    @property
    def profile(self) -> str:
        return self.card_profile or f"generic-{self.kind}"

    def stage(self, code: Any) -> str | None:
        if code is None:
            return None
        try:
            return self.print_status.get(int(code), "unknown")
        except (TypeError, ValueError):
            return "unknown"


def name_contains(*fragments: str, brand: str | None = None) -> Match:
    """Match when Attributes.MachineName contains any fragment (case/space-insensitive)."""
    norm = [" ".join(f.lower().split()) for f in fragments]

    def _match(attrs: dict[str, Any], status: dict[str, Any]) -> bool:
        name = " ".join(str(attrs.get("MachineName") or "").lower().split())
        if brand and brand.lower() not in str(attrs.get("BrandName") or "").lower():
            return False
        return any(f in name for f in norm)

    return _match


def extend(base: Mapping, **extra) -> dict:
    """Copy of a code map with entries added or replaced: extend(PRINT_STATUS, **{"13": "printing"})."""
    out = dict(base)
    out.update({int(k): v for k, v in extra.items()})
    return out


REGISTRY: list[PrinterModel] = []


def register(model: PrinterModel) -> PrinterModel:
    if any(m.id == model.id for m in REGISTRY):
        raise ValueError(f"printer model {model.id!r} registered twice")
    REGISTRY.append(model)
    return model


def resolve(attributes: dict[str, Any] | None, status: dict[str, Any] | None) -> PrinterModel:
    """The best model for a printer, falling back to the generic resin / FDM model."""
    attributes, status = attributes or {}, status or {}
    found = [m for m in REGISTRY if m.match(attributes, status)]
    if found:
        return max(found, key=lambda m: m.priority)
    kind = "fdm" if is_fdm(status) else "resin"
    return next(m for m in REGISTRY if m.id == f"generic-{kind}")


# Printer modules, imported in order. Add yours here.
MODULES = ("generic", "elegoo")

for _mod in MODULES:
    importlib.import_module(f"{__name__}.{_mod}")
