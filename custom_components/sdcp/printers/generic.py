"""Generic SDCP v3 printers — used when no specific model matches."""

from __future__ import annotations

from . import PrinterModel, register

GENERIC_RESIN = register(PrinterModel(
    id="generic-resin",
    name="Generic SDCP resin (MSLA) printer",
    kind="resin",
    tick_seconds=0.001,  # resin firmware counts milliseconds
))

GENERIC_FDM = register(PrinterModel(
    id="generic-fdm",
    name="Generic SDCP FDM printer",
    kind="fdm",
    tick_seconds=1.0,    # FDM firmware counts seconds
))
