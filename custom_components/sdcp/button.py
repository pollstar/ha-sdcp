"""Print control buttons."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import SDCPCoordinator
from .descriptions import SDCPButtonDescription
from .entity import SDCPEntity
from .protocol import SDCPCommandError, SDCPError


BUTTONS = (
    SDCPButtonDescription(key="pause", translation_key="pause", press_fn=lambda c: c.pause()),
    SDCPButtonDescription(key="resume", translation_key="resume", press_fn=lambda c: c.resume()),
    SDCPButtonDescription(key="stop", translation_key="stop", press_fn=lambda c: c.stop_print()),
    SDCPButtonDescription(key="refresh", translation_key="refresh", entity_category=EntityCategory.DIAGNOSTIC,
                          press_fn=lambda c: c.refresh()),
)


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    coord: SDCPCoordinator = entry.runtime_data
    async_add_entities(SDCPButton(coord, d) for d in (*BUTTONS, *coord.model.buttons) if d.exists_fn(coord))


class SDCPButton(SDCPEntity, ButtonEntity):
    entity_description: SDCPButtonDescription

    def __init__(self, coordinator: SDCPCoordinator, description: SDCPButtonDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    async def async_press(self) -> None:
        try:
            await self.entity_description.press_fn(self.coordinator.client)
        except SDCPCommandError as err:
            raise HomeAssistantError(f"Printer refused the command (Ack {err.ack})") from err
        except SDCPError as err:
            raise HomeAssistantError(f"Printer is not reachable: {err}") from err
