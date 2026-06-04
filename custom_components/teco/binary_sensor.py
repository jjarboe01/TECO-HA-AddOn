"""TECO program/enrollment flags as binary sensors."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import TecoCoordinator

FLAGS = {
    "paperless": ("Paperless billing", "mdi:file-document-outline"),
    "autopay": ("Autopay", "mdi:bank-transfer"),
    "budget_billing": ("Budget billing", "mdi:scale-balance"),
    "sun_select": ("SunSelect", "mdi:solar-power"),
    "energy_planner": ("Energy Planner", "mdi:calendar-clock"),
    "prime_time_plus": ("Prime Time Plus", "mdi:clock-star-four-points"),
    "power_updates": ("Power updates", "mdi:transmission-tower"),
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: TecoCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(TecoFlag(coordinator, entry, key, name, icon)
                       for key, (name, icon) in FLAGS.items())


class TecoFlag(CoordinatorEntity[TecoCoordinator], BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry, key, name, icon):
        super().__init__(coordinator)
        self._key = key
        self._attr_name = name
        self._attr_icon = icon
        self._attr_unique_id = f"{entry.entry_id}_flag_{key}"
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}}

    @property
    def is_on(self) -> bool | None:
        return ((self.coordinator.data or {}).get("flags") or {}).get(self._key)
