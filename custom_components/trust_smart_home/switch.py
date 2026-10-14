"""Switches: 433 MHz switches, Zigbee sockets and built-in switches, and
enabling/disabling the rules made in the Trust Smart Home app."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .catalog import RULE_DISABLED, RULE_ENABLED, RULE_INDEX, TrustDevice
from .const import DOMAIN
from .coordinator import TrustCoordinator
from .entity import TrustEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: TrustCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SwitchEntity] = [
        TrustSwitch(coordinator, dev)
        for dev in coordinator.devices.values()
        if dev.description.switch is not None
    ]
    entities += [
        TrustRuleSwitch(coordinator, dev)
        for dev in coordinator.devices.values()
        if dev.description.rule
    ]
    async_add_entities(entities)


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


class TrustRuleSwitch(TrustEntity, SwitchEntity):
    """Enable/disable an app rule (Rule.enable()/disable(): function 0 = 1/0)."""

    _attr_icon = "mdi:script-text-outline"

    def __init__(self, coordinator: TrustCoordinator, device: TrustDevice) -> None:
        super().__init__(coordinator, device)

    @property
    def is_on(self) -> bool | None:
        value = self.value(RULE_INDEX)
        # 2 = being played; the rule is not disabled
        return None if value is None else value != RULE_DISABLED

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"trust_rule_id": self.trust.id}

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.send(RULE_INDEX, RULE_ENABLED)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.send(RULE_INDEX, RULE_DISABLED)
