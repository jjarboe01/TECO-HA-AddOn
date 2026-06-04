"""TECO sensors (account, current bill, latest service period)."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import TecoCoordinator


def _latest_bill(data: dict) -> dict:
    bills = data.get("bills") or []
    return bills[0] if bills else {}


@dataclass(frozen=True, kw_only=True)
class TecoSensorDesc(SensorEntityDescription):
    value: Callable[[dict], object] = lambda d: None


SENSORS: tuple[TecoSensorDesc, ...] = (
    TecoSensorDesc(
        key="amount_due", name="Amount due", icon="mdi:cash",
        device_class=SensorDeviceClass.MONETARY, native_unit_of_measurement="USD",
        value=lambda d: (d.get("current_bill") or {}).get("total_amount_due"),
    ),
    TecoSensorDesc(
        key="due_date", name="Payment due date", icon="mdi:calendar-clock",
        device_class=SensorDeviceClass.DATE,
        value=lambda d: (d.get("current_bill") or {}).get("due_date"),
    ),
    TecoSensorDesc(
        key="last_bill_cost", name="Last bill cost", icon="mdi:receipt-text",
        device_class=SensorDeviceClass.MONETARY, native_unit_of_measurement="USD",
        value=lambda d: _latest_bill(d).get("cost"),
    ),
    TecoSensorDesc(
        key="last_bill_kwh", name="Last bill usage",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL,
        value=lambda d: _latest_bill(d).get("kwh_used"),
    ),
    TecoSensorDesc(
        key="last_bill_rate", name="Last bill $/kWh", icon="mdi:cash-multiple",
        native_unit_of_measurement="USD/kWh",
        value=lambda d: _latest_bill(d).get("cost_per_kwh"),
    ),
    TecoSensorDesc(
        key="service_period_start", name="Service period start", icon="mdi:calendar-start",
        device_class=SensorDeviceClass.DATE,
        value=lambda d: _latest_bill(d).get("service_period_start"),
    ),
    TecoSensorDesc(
        key="service_period_end", name="Service period end", icon="mdi:calendar-end",
        device_class=SensorDeviceClass.DATE,
        value=lambda d: _latest_bill(d).get("service_period_end"),
    ),
    TecoSensorDesc(
        key="service_days", name="Service period days", icon="mdi:calendar-range",
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda d: _latest_bill(d).get("service_days"),
    ),
    TecoSensorDesc(
        key="account_status", name="Account status", icon="mdi:account-check",
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda d: (d.get("account") or {}).get("status"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: TecoCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(TecoSensor(coordinator, entry, d) for d in SENSORS)


class TecoSensor(CoordinatorEntity[TecoCoordinator], SensorEntity):
    entity_description: TecoSensorDesc
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry, description: TecoSensorDesc):
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "TECO (Tampa Electric)",
            "manufacturer": "Tampa Electric",
            "entry_type": "service",
        }

    @property
    def native_value(self):
        return self.entity_description.value(self.coordinator.data or {})

    @property
    def extra_state_attributes(self):
        # surface the full latest bill (incl. meter reads) on the cost sensor
        if self.entity_description.key == "last_bill_cost":
            return {k: v for k, v in _latest_bill(self.coordinator.data or {}).items()
                    if k != "daily_usage"}
        return None
