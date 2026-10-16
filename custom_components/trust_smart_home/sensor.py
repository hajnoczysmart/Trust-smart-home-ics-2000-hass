"""Sensors: temperature, humidity, battery, P1 energy meter, raw status."""
from __future__ import annotations

import time
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
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .catalog import (
    SensorSpec,
    TrustDevice,
    can_estimate_power,
    estimated_power,
    sensor_value,
)
from .const import DOMAIN, ROLE_SENSOR
from .coordinator import TrustCoordinator
from .entity import TrustEntity
from .hub import TrustHubEntity
from .api import SETTING_ZIGBEE_CHANNEL

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
    for dev_id, power in coordinator.power_settings.items():
        dev = coordinator.devices.get(int(dev_id))
        if dev is None or not can_estimate_power(dev.description):
            continue
        watts, scale = float(power.get("watts", 0)), bool(power.get("scale", True))
        if watts > 0:
            entities.append(TrustEstimatedPower(coordinator, dev, watts, scale))
            entities.append(TrustEstimatedEnergy(coordinator, dev, watts, scale))
    entities += [
        TrustHubSensor(coordinator, "firmware", "device", "SW_revision"),
        TrustHubSensor(coordinator, "zigbee_firmware", "device", "ZigBee_firmware_version"),
        TrustHubSensor(coordinator, "zigbee_channel", "settings", SETTING_ZIGBEE_CHANNEL),
    ]
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


class TrustEstimatedPower(TrustEntity, SensorEntity):
    """Power of a light or plug, estimated from its on/off state (and dim level)
    and the nominal power the user entered. Not a measurement."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_translation_key = "estimated_power"

    def __init__(
        self,
        coordinator: TrustCoordinator,
        device: TrustDevice,
        watts: float,
        scale: bool,
        key: str = "est_power",
    ) -> None:
        super().__init__(coordinator, device, key)
        self._watts = watts
        self._scale = scale

    def current_watts(self) -> float | None:
        return estimated_power(self.trust.description, self.status, self._watts, self._scale)

    @property
    def native_value(self) -> float | None:
        return self.current_watts()


class TrustEstimatedEnergy(TrustEstimatedPower, RestoreEntity):
    """Energy (kWh) summed from the estimated power, for the Energy dashboard.

    The power is taken as constant between two status updates. A gap longer
    than MAX_GAP (Home Assistant or the cloud was unavailable) is not counted
    beyond that many seconds, so a long outage cannot add phantom energy.
    """

    MAX_GAP = 120.0

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 3
    _attr_translation_key = "estimated_energy"

    def __init__(
        self, coordinator: TrustCoordinator, device: TrustDevice, watts: float, scale: bool
    ) -> None:
        super().__init__(coordinator, device, watts, scale, "est_energy")
        self._kwh = 0.0
        self._last: float | None = None  # monotonic time of the last accumulation
        self._last_watts = 0.0

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None:
            try:
                self._kwh = max(0.0, float(last.state))
            except (TypeError, ValueError):
                pass  # unknown / unavailable
        self._last = time.monotonic()
        self._last_watts = self.current_watts() or 0.0

    def _accumulate(self) -> None:
        now = time.monotonic()
        if self._last is not None:
            self._kwh += self._last_watts * min(now - self._last, self.MAX_GAP) / 3_600_000
        self._last = now
        watts = self.current_watts()
        if watts is not None:  # an unknown state keeps the last known power
            self._last_watts = watts

    @callback
    def _handle_coordinator_update(self) -> None:
        self._accumulate()
        super()._handle_coordinator_update()

    @property
    def native_value(self) -> float:
        return round(self._kwh, 6)


class TrustHubSensor(TrustHubEntity, SensorEntity):
    """Information about the hub, read on the LAN."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: TrustCoordinator, key: str, section: str, field: str) -> None:
        super().__init__(coordinator, key)
        self._section, self._field = section, field

    @property
    def native_value(self) -> str | None:
        value = self.hub_value(self._section, self._field)
        return str(value)[:64] if value not in (None, "") else None
