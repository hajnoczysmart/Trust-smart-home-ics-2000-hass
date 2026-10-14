"""Binary sensors: motion, contact, smoke, leakage, CO, tamper, battery, siren ...

Index/value pairs come from the Trust app (see catalog.py). Extra binary
sensors can be added per device in the integration options.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .catalog import BinarySpec, TrustDevice
from .const import DOMAIN, ROLE_BINARY_SENSOR
from .coordinator import TrustCoordinator
from .entity import TrustEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: TrustCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[TrustBinarySensor] = [
        TrustBinarySensor(coordinator, dev, spec)
        for dev in coordinator.devices.values()
        for spec in dev.description.binary_sensors
    ]
    for dev_id, mapping in coordinator.mappings.items():
        dev = coordinator.devices.get(int(dev_id))
        if dev is not None and mapping.get("role") == ROLE_BINARY_SENSOR:
            entities.append(TrustBinarySensor(coordinator, dev, _mapped_spec(mapping)))
    async_add_entities(entities)


def _mapped_spec(mapping: dict[str, Any]) -> BinarySpec:
    device_class = mapping.get("device_class")
    return BinarySpec(
        key="mapped",
        index=int(mapping.get("index", 0)),
        on_values=(int(mapping.get("active_value", 1)),),
        device_class=None if device_class in (None, "none") else device_class,
    )


class TrustBinarySensor(TrustEntity, BinarySensorEntity):
    def __init__(
        self, coordinator: TrustCoordinator, device: TrustDevice, spec: BinarySpec
    ) -> None:
        super().__init__(coordinator, device, f"bs_{spec.key}")
        self.spec = spec
        if spec.device_class:
            self._attr_device_class = BinarySensorDeviceClass(spec.device_class)
        if spec.channel is not None:
            self._attr_translation_key = "channel"
            self._attr_translation_placeholders = {"channel": str(spec.channel)}
        elif spec.key in ("dusk", "pressed", "siren_active", "mapped") or not spec.device_class:
            self._attr_translation_key = spec.key
        if spec.diagnostic:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def is_on(self) -> bool | None:
        value = self.value(self.spec.index)
        return None if value is None else value in self.spec.on_values
