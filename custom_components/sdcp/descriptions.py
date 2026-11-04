"""Entity description types shared by the platforms and by printer models.

Printer modules in ``printers/`` use these to add model-specific entities;
they get the coordinator and read ``c.status`` / ``c.attributes`` /
``c.print_info`` (raw SDCP dicts) and ``c.client`` to send commands.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components.binary_sensor import BinarySensorEntityDescription
from homeassistant.components.button import ButtonEntityDescription
from homeassistant.components.sensor import SensorEntityDescription

if TYPE_CHECKING:
    from .coordinator import SDCPCoordinator
    from .protocol import SDCPClient


def num(value: Any) -> float | None:
    """float() that returns None instead of raising."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def status_value(key: str) -> Callable[[SDCPCoordinator], float | None]:
    """value_fn reading a numeric key of the Status object."""
    return lambda c: num(c.status.get(key))


def has_status(key: str) -> Callable[[SDCPCoordinator], bool]:
    """exists_fn: create the entity only when the printer reports the key."""
    return lambda c: key in c.status


@dataclass(frozen=True, kw_only=True)
class SDCPSensorDescription(SensorEntityDescription):
    value_fn: Callable[[SDCPCoordinator], Any]
    exists_fn: Callable[[SDCPCoordinator], bool] = lambda c: True
    attrs_fn: Callable[[SDCPCoordinator], dict[str, Any]] | None = None


@dataclass(frozen=True, kw_only=True)
class SDCPBinaryDescription(BinarySensorEntityDescription):
    value_fn: Callable[[SDCPCoordinator], bool | None]
    exists_fn: Callable[[SDCPCoordinator], bool] = lambda c: True
    always_available: bool = False


@dataclass(frozen=True, kw_only=True)
class SDCPButtonDescription(ButtonEntityDescription):
    press_fn: Callable[[SDCPClient], Awaitable[Any]]
    exists_fn: Callable[[SDCPCoordinator], bool] = lambda c: True
