from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.entity import EntityCategory

from .entity import ZyxelEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([ResetTrafficTotalsButton(entry.runtime_data)])


class ResetTrafficTotalsButton(ZyxelEntity, ButtonEntity):
    """Reset Home Assistant's persistent accumulated traffic totals."""

    _attr_name = "Reset accumulated traffic totals"
    _attr_icon = "mdi:counter"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator):
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.data.serial}_reset_traffic_totals"

    async def async_press(self) -> None:
        await self.coordinator.async_reset_traffic_totals()
