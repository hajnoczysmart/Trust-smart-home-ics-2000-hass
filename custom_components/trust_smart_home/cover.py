"""Covers: 433 MHz actuators, Somfy RTS and Brel motors."""
from __future__ import annotations

from typing import Any

from homeassistant.components.cover import ATTR_POSITION, CoverEntity, CoverEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .catalog import CoverSpec, TrustDevice, level_to_position, position_to_level
from .const import DOMAIN
from .coordinator import TrustCoordinator
from .entity import TrustEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: TrustCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        TrustCover(coordinator, dev)
        for dev in coordinator.devices.values()
        if dev.description.cover is not None
    )


class TrustCover(TrustEntity, CoverEntity):
    """These motors give no position feedback, so the state is assumed."""

    _attr_assumed_state = True

    def __init__(self, coordinator: TrustCoordinator, device: TrustDevice) -> None:
        super().__init__(coordinator, device)
        spec = device.description.cover
        assert spec is not None
        self.spec: CoverSpec = spec
        features = CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE
        if spec.stop is not None:
            features |= CoverEntityFeature.STOP
        if spec.position_index is not None:
            features |= CoverEntityFeature.SET_POSITION
        self._attr_supported_features = features
        self._closed: bool | None = None

    @property
    def current_cover_position(self) -> int | None:
        level = self.value(self.spec.position_index)
        return level_to_position(level, self.spec) if level else None

    @property
    def is_closed(self) -> bool | None:
        position = self.current_cover_position
        if position is not None:
            return position == 0
        return self._closed

    async def async_open_cover(self, **kwargs: Any) -> None:
        await self.send(*self.spec.open)
        self._closed = False

    async def async_close_cover(self, **kwargs: Any) -> None:
        await self.send(*self.spec.close)
        self._closed = True

    async def async_stop_cover(self, **kwargs: Any) -> None:
        if self.spec.stop is not None:
            await self.send(*self.spec.stop)
            self._closed = None

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        if self.spec.position_index is not None:
            await self.send(
                self.spec.position_index, position_to_level(kwargs[ATTR_POSITION], self.spec)
            )
