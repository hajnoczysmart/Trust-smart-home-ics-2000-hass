"""Button-press events from 433 MHz remotes, wall switches and the ARC alarm remote.

The hub keeps, per channel, which button was pressed last (1 = on, 0 = off).
An event fires when a polled value changes. Presses repeated faster than the
poll interval, or the same button pressed twice, cannot be seen.
"""
from __future__ import annotations

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .catalog import EventSpec, TrustDevice
from .const import DOMAIN, EVENT_PRESS
from .coordinator import TrustCoordinator
from .entity import TrustEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: TrustCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        TrustEvent(coordinator, dev, spec)
        for dev in coordinator.devices.values()
        for spec in dev.description.events
    )


class TrustEvent(TrustEntity, EventEntity):
    def __init__(
        self, coordinator: TrustCoordinator, device: TrustDevice, spec: EventSpec
    ) -> None:
        super().__init__(coordinator, device, f"ev_{spec.key}")
        self.spec = spec
        self._attr_event_types = spec.event_types
        self._attr_translation_key = spec.key
        if spec.device_class:
            self._attr_device_class = EventDeviceClass(spec.device_class)
        self._last: dict[int, int | None] = {i: self.value(i) for i in spec.indexes}

    @callback
    def _handle_coordinator_update(self) -> None:
        fired = False
        for index in self.spec.indexes:
            new = self.value(index)
            old = self._last.get(index)
            self._last[index] = new
            if new is None or old is None or new == old:
                continue
            if self.coordinator.is_own_change(self.trust.id, index, new):
                continue  # caused by our own button entity, not a real press
            event_type = self.spec.event_type(index, new)
            if event_type is not None:
                attrs = {"raw_value": new}
                if len(self.spec.indexes) > 1:
                    attrs["channel"] = index + 1
                self._trigger_event(event_type, attrs)
                self._fire_press(index, new)
                fired = True
        if fired:
            self.async_write_ha_state()
        else:
            super()._handle_coordinator_update()

    def _fire_press(self, index: int, raw: int) -> None:
        """Bus event for the device triggers ("Channel 2: ON pressed")."""
        trigger = self.spec.trigger(index, raw)
        entry = self.registry_entry
        if trigger is None or entry is None or entry.device_id is None:
            return
        trigger_type, subtype = trigger
        self.hass.bus.async_fire(
            EVENT_PRESS,
            {"device_id": entry.device_id, "type": trigger_type, "subtype": subtype},
        )
