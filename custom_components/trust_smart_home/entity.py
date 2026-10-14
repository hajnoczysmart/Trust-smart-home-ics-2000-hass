"""Base entity for Trust Smart Home."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .catalog import TrustDevice, status_value
from .const import DOMAIN, MANUFACTURER
from .coordinator import TrustCoordinator


class TrustEntity(CoordinatorEntity[TrustCoordinator]):
    """One Home Assistant entity backed by one Trust device, group or scene."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: TrustCoordinator, device: TrustDevice, key: str | None = None
    ) -> None:
        super().__init__(coordinator)
        self.trust = device
        base = f"{coordinator.home_id}_{device.id}"
        self._attr_unique_id = f"{base}_{key}" if key else base
        if key is None:
            self._attr_name = None  # use the device name
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, base)},
            name=device.name,
            manufacturer=MANUFACTURER,
            model=device.type_name,
            model_id=str(device.device_type) if device.device_type is not None else None,
        )

    @property
    def status(self) -> list[int] | None:
        return self.coordinator.status(self.trust.id)

    def value(self, index: int | None) -> int | None:
        return status_value(self.status, index)

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        online = self.trust.description.online_index
        if online is not None and self.value(online) == 0:
            return False  # Zigbee device reports itself offline
        return True

    async def send(self, index: int, value: int, optimistic: bool = True) -> None:
        await self.coordinator.async_send(self.trust, index, value, optimistic)
