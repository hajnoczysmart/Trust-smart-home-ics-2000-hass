"""Scenes created in the Trust Smart Home app."""
from __future__ import annotations

from typing import Any

from homeassistant.components.scene import Scene
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .catalog import SCENE_PLAY, SCENE_PLAYBACK_INDEX
from .const import DOMAIN
from .coordinator import TrustCoordinator
from .entity import TrustEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: TrustCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        TrustScene(coordinator, dev)
        for dev in coordinator.devices.values()
        if dev.description.scene
    )


class TrustScene(TrustEntity, Scene):
    """Plays the scene on the hub (scene function 0 = 1, as the app does)."""

    async def async_activate(self, **kwargs: Any) -> None:
        await self.send(SCENE_PLAYBACK_INDEX, SCENE_PLAY)
