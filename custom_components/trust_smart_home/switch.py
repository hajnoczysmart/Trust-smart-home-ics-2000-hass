"""Switches: 433 MHz switches, Zigbee sockets and built-in switches."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .catalog import TrustDevice
from .const import DOMAIN
from .coordinator import TrustCoordinator
from .entity import TrustEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: TrustCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        TrustSwitch(coordinator, dev)
        for dev in coordinator.devices.values()
        if dev.description.switch is not None
    )


class TrustSwitch(TrustEntity, SwitchEntity):
    def __init__(self, coordinator: TrustCoordinator, device: TrustDevice) -> None:
        super().__init__(coordinator, device)
        assert device.description.switch is not None
        self._index = device.description.switch.index

    @property
    def is_on(self) -> bool | None:
        value = self.value(self._index)
        return None if value is None else value == 1  # 2 = group off on 433 MHz

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.send(self._index, 1)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.send(self._index, 0)
