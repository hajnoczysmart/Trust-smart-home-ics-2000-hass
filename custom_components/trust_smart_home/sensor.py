"""Sensors: temperature, humidity, battery, P1 energy meter, raw status."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfVolume,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .catalog import SensorSpec, TrustDevice, sensor_value
from .const import DOMAIN, ROLE_SENSOR
from .coordinator import TrustCoordinator
from .entity import TrustEntity

# kind -> (device class, unit, state class, entity category)
_KINDS: dict[str, tuple[Any, Any, Any, Any]] = {
    "temperature": (
        SensorDeviceClass.TEMPERATURE,
        UnitOfTemperature.CELSIUS,
        SensorStateClass.MEASUREMENT,
        None,
    ),
    "humidity": (SensorDeviceClass.HUMIDITY, PERCENTAGE, SensorStateClass.MEASUREMENT, None),
    "battery": (
        SensorDeviceClass.BATTERY,
        PERCENTAGE,
        SensorStateClass.MEASUREMENT,
        EntityCategory.DIAGNOSTIC,
    ),
    "power": (SensorDeviceClass.POWER, UnitOfPower.WATT, SensorStateClass.MEASUREMENT, None),
    "energy": (
        SensorDeviceClass.ENERGY,
        UnitOfEnergy.KILO_WATT_HOUR,
        SensorStateClass.TOTAL_INCREASING,
        None,
    ),
    "gas": (
        SensorDeviceClass.GAS,
        UnitOfVolume.CUBIC_METERS,
        SensorStateClass.TOTAL_INCREASING,
        None,
    ),
    "water": (
        SensorDeviceClass.WATER,
        UnitOfVolume.CUBIC_METERS,
        SensorStateClass.TOTAL_INCREASING,
        None,
    ),
    "tariff": (SensorDeviceClass.ENUM, None, None, None),
}

_SIMPLE = (str, int, float, bool, type(None))


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: TrustCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = []
    for dev in coordinator.devices.values():
        for spec in dev.description.sensors:
            entities.append(TrustSensor(coordinator, dev, spec))
        if dev.kind != "scene":
            entities.append(TrustRawStatus(coordinator, dev))
    for dev_id, mapping in coordinator.mappings.items():
        dev = coordinator.devices.get(int(dev_id))
        if dev is not None and mapping.get("role") == ROLE_SENSOR:
            entities.append(TrustMappedSensor(coordinator, dev, int(mapping.get("index", 0))))
    async_add_entities(entities)


class TrustSensor(TrustEntity, SensorEntity):
    def __init__(
        self, coordinator: TrustCoordinator, device: TrustDevice, spec: SensorSpec
    ) -> None:
        super().__init__(coordinator, device, f"s_{spec.key}")
        self.spec = spec
        device_class, unit, state_class, category = _KINDS[spec.kind]
        self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = unit
        self._attr_state_class = state_class
        self._attr_entity_category = category
        if spec.kind == "tariff":
            self._attr_options = ["low", "high"]
        if spec.key not in ("temperature", "humidity", "battery"):
            self._attr_translation_key = spec.key  # P1 meter values need names

    @property
    def native_value(self) -> Any:
        return sensor_value(self.spec.kind, self.value(self.spec.index))


class TrustRawStatus(TrustEntity, SensorEntity):
    """Diagnostic: the raw status list. Enabled by default only for devices
    without a dedicated entity, so they can be mapped or reported."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "raw_status"
    _attr_icon = "mdi:code-brackets"

    def __init__(self, coordinator: TrustCoordinator, device: TrustDevice) -> None:
        super().__init__(coordinator, device, "raw_status")
        self._attr_entity_registry_enabled_default = not device.description.supported

    @property
    def native_value(self) -> str | None:
        status = self.status
        if status is None:
            return None
        text = ",".join(str(v) for v in status)
        return text if len(text) <= 255 else text[:252] + "..."

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        dev = self.trust
        return {
            "entity_id_trust": dev.id,
            "kind": dev.kind,
            "device_type": dev.device_type,
            "type_name": dev.type_name,
            "info": dev.info,
            "status": self.status,
        }


class TrustMappedSensor(TrustEntity, SensorEntity):
    """User-mapped numeric value from one status position."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_translation_key = "mapped"

    def __init__(self, coordinator: TrustCoordinator, device: TrustDevice, index: int) -> None:
        super().__init__(coordinator, device, "mapped_value")
        self._index = index

    @property
    def native_value(self) -> int | None:
        return self.value(self._index)
