"""Binary sensors: link, printing, vat heating, self-test results."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import SDCPCoordinator
from .descriptions import SDCPBinaryDescription
from .entity import SDCPEntity


BINARY: tuple[SDCPBinaryDescription, ...] = (
    SDCPBinaryDescription(
        key="connected", translation_key="connected", device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC, value_fn=lambda c: c.connected, always_available=True,
    ),
    SDCPBinaryDescription(
        key="printing", translation_key="printing", device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=lambda c: c.is_active or c.is_paused,
    ),
    SDCPBinaryDescription(
        key="vat_heating", translation_key="vat_heating", device_class=BinarySensorDeviceClass.HEAT,
        value_fn=lambda c: int(c.status.get("HeatStatus") or 0) == 1, exists_fn=lambda c: "HeatStatus" in c.status,
    ),
    SDCPBinaryDescription(
        key="usb_disk", translation_key="usb_disk", device_class=BinarySensorDeviceClass.PLUG,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: int(c.attributes.get("UsbDiskStatus") or 0) == 1,
        exists_fn=lambda c: "UsbDiskStatus" in c.attributes,
    ),
)


def _health(key: str, tk: str | None) -> SDCPBinaryDescription:
    return SDCPBinaryDescription(
        key=f"health_{key}",
        translation_key=tk,
        name=None if tk else key,
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: (c.attributes.get("DevicesStatus") or {}).get(key) not in (1, None),
    )


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    coord: SDCPCoordinator = entry.runtime_data
    descs = [d for d in (*BINARY, *coord.model.binary_sensors) if d.exists_fn(coord)]
    descs += [_health(k, coord.model.health_keys.get(k)) for k in (coord.attributes.get("DevicesStatus") or {})]
    async_add_entities(SDCPBinary(coord, d) for d in descs)


class SDCPBinary(SDCPEntity, BinarySensorEntity):
    entity_description: SDCPBinaryDescription

    def __init__(self, coordinator: SDCPCoordinator, description: SDCPBinaryDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        return self.entity_description.always_available or super().available

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self.coordinator)
