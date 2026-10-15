"""Hub LED (experimental): the "led" setting of the ICS-2000, as in the app's settings."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .api import LED_STATES
from .const import DOMAIN, MANUFACTURER
from .coordinator import TrustCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: TrustCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([TrustHubLed(coordinator)])


class TrustHubLed(SelectEntity, RestoreEntity):
    """LED setting of the hub: on, off or disco.

    The hub is only asked for the current setting when its LAN address is
    configured; otherwise the last value set from Home Assistant is shown.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "hub_led"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = list(LED_STATES)
    _attr_should_poll = False

    def __init__(self, coordinator: TrustCoordinator) -> None:
        self.coordinator = coordinator
        home = coordinator.home_id
        self._attr_unique_id = f"{home}_hub_led"
        self._attr_current_option = None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{home}_hub")},
            name="ICS-2000",
            manufacturer=MANUFACTURER,
            model="ICS-2000 hub",
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        state = await self.coordinator.async_read_led()
        if state is None and (last := await self.async_get_last_state()) is not None:
            state = last.state if last.state in LED_STATES else None
        self._attr_current_option = state

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_led(option)
        self._attr_current_option = option
        self.async_write_ha_state()
