"""Switches: 433 MHz switches, Zigbee sockets and built-in switches,
enabling/disabling the rules made in the Trust Smart Home app, and the hub LED."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .catalog import RULE_DISABLED, RULE_ENABLED, RULE_INDEX, TrustDevice
from .const import DOMAIN
from .coordinator import TrustCoordinator
from .entity import TrustEntity
from .hub import TrustHubEntity
from .api import SETTING_FW_UPDATE_DISABLED


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
    entities.append(TrustHubLed(coordinator))
    entities.append(TrustFirmwareAutoUpdate(coordinator))
    async_add_entities(entities)


class TrustHubLed(SwitchEntity, RestoreEntity):
    """The hub's LED setting, as the switch in the app's settings.

    The hub is asked for the current setting when its LAN address is
    configured; otherwise the last value set from Home Assistant is shown.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "hub_led"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_should_poll = False

    def __init__(self, coordinator: TrustCoordinator) -> None:
        self.coordinator = coordinator
        home = coordinator.home_id
        self._attr_unique_id = f"{home}_hub_led_switch"
        self._attr_is_on = None
        # the hub device created at set-up (identified by the hub's MAC)
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, coordinator.mac)})

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        state = await self.coordinator.async_read_led()
        if state is not None:
            self._attr_is_on = state != "off"
        elif (last := await self.async_get_last_state()) is not None and last.state in ("on", "off"):
            self._attr_is_on = last.state == "on"

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_led("on")
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_led("off")
        self._attr_is_on = False
        self.async_write_ha_state()


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


class TrustFirmwareAutoUpdate(TrustHubEntity, SwitchEntity):
    """Automatic firmware updates of the hub (the app's settings switch)."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: TrustCoordinator) -> None:
        super().__init__(coordinator, "firmware_auto_update")

    @property
    def available(self) -> bool:
        return super().available and self.is_on is not None

    @property
    def is_on(self) -> bool | None:
        disabled = self.hub_value("settings", SETTING_FW_UPDATE_DISABLED)
        try:
            return int(disabled) == 0
        except (TypeError, ValueError):
            return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_hub_setting(SETTING_FW_UPDATE_DISABLED, 0)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_hub_setting(SETTING_FW_UPDATE_DISABLED, 1)
