"""Sensors."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfInformation,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import SDCPCoordinator
from .descriptions import SDCPSensorDescription, has_status as _has, num as _num, status_value as _st
from .entity import SDCPEntity
from .protocol import machine_status_code

Value = Callable[[SDCPCoordinator], Any]


def _progress(c: SDCPCoordinator) -> float | None:
    pi = c.print_info
    if "Progress" in pi:  # FDM firmware reports it directly
        return _num(pi["Progress"])
    tl = int(pi.get("TotalLayer") or 0)
    if not tl:
        return 0
    if c.stage == "complete":
        return 100
    return round(min(100.0, int(pi.get("CurrentLayer") or 0) / tl * 100), 1)


def _elapsed(c: SDCPCoordinator) -> float | None:
    if not c.has_job:
        return 0
    return round(float(c.print_info.get("CurrentTicks") or 0) * c.tick_seconds / 60, 1)


def _remaining(c: SDCPCoordinator) -> float | None:
    if not (c.is_active or c.is_paused):
        return 0
    pi = c.print_info
    left = float(pi.get("TotalTicks") or 0) - float(pi.get("CurrentTicks") or 0)
    return round(max(0.0, left) * c.tick_seconds / 60, 1)


def _finish(c: SDCPCoordinator) -> datetime | None:
    return c.finish_at if (c.is_active or c.is_paused) else None


def _machine(c: SDCPCoordinator) -> str | None:
    code = machine_status_code(c.status)
    return None if code is None else c.model.machine_status.get(code, "unknown")


def _error(c: SDCPCoordinator) -> str:
    return c.model.print_errors.get(int(c.print_info.get("ErrorNumber") or 0), "unknown")


def _job_attrs(c: SDCPCoordinator) -> dict[str, Any]:
    pi = c.print_info
    si = c.slice_info
    attrs: dict[str, Any] = {
        "task_id": pi.get("TaskId") or None,
        "current_layer": pi.get("CurrentLayer"),
        "total_layers": pi.get("TotalLayer"),
        "thumbnail": c.thumbnail_url(),
    }
    lh = _num(si.get("layer_height"))
    if lh:
        attrs["layer_height"] = lh
        attrs["z_height"] = round(lh * int(pi.get("CurrentLayer") or 0), 2)
        attrs["model_height"] = round(lh * int(pi.get("TotalLayer") or 0), 2)
    for src, dst in (
        ("volume", "resin_volume"), ("weight", "resin_weight"), ("price", "price"),
        ("normal_layer_exposure_time", "exposure_time"), ("bottom_layer_exposure_time", "bottom_exposure_time"),
        ("bottom_layer_numbers", "bottom_layers"), ("print_time", "sliced_print_time"),
        ("resin_color", "resin_color"), ("machine_name", "sliced_for"),
    ):
        if si.get(src) not in (None, ""):
            attrs[dst] = si[src]
    return {k: v for k, v in attrs.items() if v is not None}


def _fans(key: str) -> Value:
    return lambda c: _num((c.status.get("CurrentFanSpeed") or {}).get(key))


def _has_fan(key: str) -> Callable[[SDCPCoordinator], bool]:
    return lambda c: key in (c.status.get("CurrentFanSpeed") or {})


TEMP = dict(
    device_class=SensorDeviceClass.TEMPERATURE,
    native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    state_class=SensorStateClass.MEASUREMENT,
    suggested_display_precision=1,
)
TARGET = dict(TEMP, state_class=None, entity_category=EntityCategory.DIAGNOSTIC, suggested_display_precision=0)
MINUTES = dict(
    device_class=SensorDeviceClass.DURATION,
    native_unit_of_measurement=UnitOfTime.MINUTES,
    suggested_display_precision=0,
)

SENSORS: tuple[SDCPSensorDescription, ...] = (
    SDCPSensorDescription(
        key="machine_status", translation_key="machine_status", device_class=SensorDeviceClass.ENUM,
        options=[], value_fn=_machine,
        attrs_fn=lambda c: {"printer_model": c.model.id, "printer_profile": c.model.profile,
                            "printer_kind": c.model.kind},
    ),
    SDCPSensorDescription(
        key="print_status", translation_key="print_status", device_class=SensorDeviceClass.ENUM,
        options=[], value_fn=lambda c: c.stage,
    ),
    SDCPSensorDescription(
        key="progress", translation_key="progress", native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT, suggested_display_precision=1, value_fn=_progress,
    ),
    SDCPSensorDescription(
        key="current_layer", translation_key="current_layer", state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda c: _num(c.print_info.get("CurrentLayer")),
    ),
    SDCPSensorDescription(
        key="total_layers", translation_key="total_layers",
        value_fn=lambda c: _num(c.print_info.get("TotalLayer")),
    ),
    SDCPSensorDescription(key="print_elapsed", translation_key="print_elapsed", value_fn=_elapsed, **MINUTES),
    SDCPSensorDescription(key="print_remaining", translation_key="print_remaining", value_fn=_remaining, **MINUTES),
    SDCPSensorDescription(
        key="print_finish", translation_key="print_finish", device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_finish,
    ),
    SDCPSensorDescription(
        key="filename", translation_key="filename", value_fn=lambda c: c.print_info.get("Filename") or None,
        attrs_fn=_job_attrs,
    ),
    SDCPSensorDescription(
        key="print_error", translation_key="print_error", device_class=SensorDeviceClass.ENUM,
        options=[], entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_error,
    ),
    # --- resin ---
    SDCPSensorDescription(key="uv_led_temp", translation_key="uv_led_temp", value_fn=_st("TempOfUVLED"),
                          exists_fn=_has("TempOfUVLED"), **TEMP),
    SDCPSensorDescription(key="vat_temp", translation_key="vat_temp", value_fn=_st("TempOfTank"),
                          exists_fn=_has("TempOfTank"), **TEMP),
    SDCPSensorDescription(key="vat_target_temp", translation_key="vat_target_temp",
                          value_fn=_st("TempTargetTank"), exists_fn=_has("TempTargetTank"), **TARGET),
    SDCPSensorDescription(
        key="release_film", translation_key="release_film", state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=_st("ReleaseFilm"), exists_fn=_has("ReleaseFilm"),
        attrs_fn=lambda c: {"max": c.attributes.get("ReleaseFilmMax")} if c.attributes.get("ReleaseFilmMax") else {},
    ),
    SDCPSensorDescription(
        key="release_film_wear", translation_key="release_film_wear", native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=0, entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: round(float(c.status["ReleaseFilm"]) / float(c.attributes["ReleaseFilmMax"]) * 100, 1)
        if c.attributes.get("ReleaseFilmMax") and _num(c.status.get("ReleaseFilm")) is not None else None,
        exists_fn=lambda c: "ReleaseFilm" in c.status and bool(c.attributes.get("ReleaseFilmMax")),
    ),
    SDCPSensorDescription(
        key="lcd_usage", translation_key="lcd_usage", device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS, suggested_display_precision=1,
        state_class=SensorStateClass.TOTAL_INCREASING, entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: round(float(c.status["PrintScreen"]) / 3600, 2)
        if _num(c.status.get("PrintScreen")) is not None else None,
        exists_fn=_has("PrintScreen"),
    ),
    # --- FDM ---
    SDCPSensorDescription(key="nozzle_temp", translation_key="nozzle_temp", value_fn=_st("TempOfNozzle"),
                          exists_fn=_has("TempOfNozzle"), **TEMP),
    SDCPSensorDescription(key="nozzle_target_temp", translation_key="nozzle_target_temp",
                          value_fn=_st("TempTargetNozzle"), exists_fn=_has("TempTargetNozzle"), **TARGET),
    SDCPSensorDescription(key="bed_temp", translation_key="bed_temp", value_fn=_st("TempOfHotbed"),
                          exists_fn=_has("TempOfHotbed"), **TEMP),
    SDCPSensorDescription(key="bed_target_temp", translation_key="bed_target_temp",
                          value_fn=_st("TempTargetHotbed"), exists_fn=_has("TempTargetHotbed"), **TARGET),
    SDCPSensorDescription(key="chamber_temp", translation_key="chamber_temp", value_fn=_st("TempOfBox"),
                          exists_fn=_has("TempOfBox"), **TEMP),
    SDCPSensorDescription(key="model_fan", translation_key="model_fan", native_unit_of_measurement=PERCENTAGE,
                          value_fn=_fans("ModelFan"), exists_fn=_has_fan("ModelFan")),
    SDCPSensorDescription(key="aux_fan", translation_key="aux_fan", native_unit_of_measurement=PERCENTAGE,
                          value_fn=_fans("AuxiliaryFan"), exists_fn=_has_fan("AuxiliaryFan")),
    SDCPSensorDescription(key="box_fan", translation_key="box_fan", native_unit_of_measurement=PERCENTAGE,
                          value_fn=_fans("BoxFan"), exists_fn=_has_fan("BoxFan")),
    SDCPSensorDescription(
        key="print_speed", translation_key="print_speed", native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda c: _num(c.print_info.get("PrintSpeedPct", c.status.get("PrintSpeed"))),
        exists_fn=lambda c: "PrintSpeedPct" in c.print_info or "PrintSpeed" in c.status,
    ),
    SDCPSensorDescription(key="z_offset", translation_key="z_offset", native_unit_of_measurement="mm",
                          entity_category=EntityCategory.DIAGNOSTIC, value_fn=_st("ZOffset"),
                          exists_fn=_has("ZOffset")),
    # --- common diagnostics ---
    SDCPSensorDescription(
        key="free_storage", translation_key="free_storage", device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES, suggested_unit_of_measurement=UnitOfInformation.GIGABYTES,
        suggested_display_precision=2, entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: _num(c.attributes.get("RemainingMemory")),
        exists_fn=lambda c: "RemainingMemory" in c.attributes,
    ),
    SDCPSensorDescription(
        key="video_streams", translation_key="video_streams", entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda c: _num(c.attributes.get("NumberOfVideoStreamConnected")),
        exists_fn=lambda c: "NumberOfVideoStreamConnected" in c.attributes,
        attrs_fn=lambda c: {"max": c.attributes.get("MaximumVideoStreamAllowed")},
    ),
)


def _options(codes) -> list[str]:
    return sorted(set(codes.values()) | {"unknown"})


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    coord: SDCPCoordinator = entry.runtime_data
    model = coord.model
    # enum options follow the printer model's code tables
    options = {"machine_status": _options(model.machine_status), "print_status": _options(model.print_status),
               "print_error": _options(model.print_errors)}
    descs = [replace(d, options=options[d.key]) if d.key in options else d for d in SENSORS]
    descs += list(model.sensors)
    async_add_entities(SDCPSensor(coord, d) for d in descs if d.exists_fn(coord))


class SDCPSensor(SDCPEntity, SensorEntity):
    entity_description: SDCPSensorDescription

    def __init__(self, coordinator: SDCPCoordinator, description: SDCPSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        try:
            return self.entity_description.value_fn(self.coordinator)
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        fn = self.entity_description.attrs_fn
        return fn(self.coordinator) if fn else None
