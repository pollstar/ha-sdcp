"""Base entity."""

from __future__ import annotations

from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import SDCPCoordinator


class SDCPEntity(CoordinatorEntity[SDCPCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: SDCPCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.mainboard_id}_{key}"
        self._attr_device_info = coordinator.device_info()

    @property
    def available(self) -> bool:
        return self.coordinator.connected
