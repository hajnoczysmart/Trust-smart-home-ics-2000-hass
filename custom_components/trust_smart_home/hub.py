"""Entities of the ICS-2000 hub itself: restart, automatic firmware updates,
firmware/Zigbee information and the "local communication only" setting.

The values are read from the hub on the LAN (GET_DEVICE_INFO and
GET_USER_SETTINGS, as the app does) at start-up and every few hours.
"""
from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import TrustCoordinator


class TrustHubEntity(CoordinatorEntity[TrustCoordinator]):
    """An entity on the hub device (identified by the hub's MAC)."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: TrustCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.home_id}_hub_{key}"
        self._attr_translation_key = f"hub_{key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, coordinator.mac)})

    def hub_value(self, section: str, key: str) -> Any:
        return (self.coordinator.hub_info.get(section) or {}).get(key)
