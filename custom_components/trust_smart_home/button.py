"""Buttons: chime and doorbell ring, siren test/alarm, Zigbee identify,
and transmitting as a remote."""
from __future__ import annotations

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .alarm_control_panel import async_panic
from .catalog import ButtonSpec, TrustDevice
from .const import DOMAIN
from .coordinator import TrustCoordinator
from .entity import TrustEntity
from .hub import TrustHubEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: TrustCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[ButtonEntity] = [
        TrustButton(coordinator, dev, spec)
        for dev in coordinator.devices.values()
        for spec in dev.description.buttons
    ]
    entities.extend(
        TrustPanicButton(coordinator, dev)
        for dev in coordinator.devices.values()
        if dev.description.alarm
    )
    entities.append(TrustHubRestart(coordinator))
    async_add_entities(entities)


class TrustButton(TrustEntity, ButtonEntity):
    def __init__(
        self, coordinator: TrustCoordinator, device: TrustDevice, spec: ButtonSpec
    ) -> None:
        super().__init__(coordinator, device, spec.key)
        self.spec = spec
        self._attr_entity_registry_enabled_default = spec.enabled_default
        if spec.device_class:
            self._attr_device_class = ButtonDeviceClass(spec.device_class)
            self._attr_entity_category = EntityCategory.CONFIG
        elif device.kind == "scenario":
            self._attr_translation_key = "rule_run"
            self._attr_translation_placeholders = {"name": device.name}
        elif spec.channel is not None:
            self._attr_translation_key = "channel_on" if spec.value == 1 else "channel_off"
            self._attr_translation_placeholders = {"channel": str(spec.channel)}
        else:
            self._attr_translation_key = spec.key

    async def async_press(self) -> None:
        # A press triggers something; it does not set a lasting state.
        await self.send(self.spec.index, self.spec.value, optimistic=False)


class TrustPanicButton(TrustEntity, ButtonEntity):
    """Panic on the alarm device, next to the arm and disarm controls."""

    _attr_translation_key = "panic"
    _attr_entity_registry_enabled_default = True

    def __init__(self, coordinator: TrustCoordinator, device: TrustDevice) -> None:
        super().__init__(coordinator, device, "panic")

    @property
    def available(self) -> bool:
        # Panic controls do not depend on the device's cloud status: a slow cloud
        # poll after a panic must not grey them out.
        return self.coordinator.last_update_success

    async def async_press(self) -> None:
        await async_panic(self.coordinator)


class TrustHubRestart(TrustHubEntity, ButtonEntity):
    """Restarts the hub, like "Reboot" in the app's settings."""

    _attr_device_class = ButtonDeviceClass.RESTART
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: TrustCoordinator) -> None:
        super().__init__(coordinator, "restart")

    async def async_press(self) -> None:
        await self.coordinator.async_reboot()
