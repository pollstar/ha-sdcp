"""Time-lapse switch (Cmd 387)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import SDCPCoordinator
from .entity import SDCPEntity
from .protocol import SDCPError


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    coord: SDCPCoordinator = entry.runtime_data
    if "TimeLapseStatus" in coord.status:
        async_add_entities([TimeLapseSwitch(coord)])


class TimeLapseSwitch(SDCPEntity, SwitchEntity):
    _attr_translation_key = "timelapse"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: SDCPCoordinator) -> None:
        super().__init__(coordinator, "timelapse")

    @property
    def is_on(self) -> bool:
        return int(self.coordinator.status.get("TimeLapseStatus") or 0) == 1

    async def _set(self, on: bool) -> None:
        try:
            await self.coordinator.client.set_timelapse(on)
            await self.coordinator.client.refresh()
        except SDCPError as err:
            raise HomeAssistantError(f"Could not switch time-lapse: {err}") from err

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)
