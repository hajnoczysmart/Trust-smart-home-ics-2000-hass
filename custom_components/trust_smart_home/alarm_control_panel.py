"""The ICS-2000 security system (alarm module, device type 240)."""
from __future__ import annotations

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .catalog import (
    ALARM_ARM,
    ALARM_COMMAND_INDEX,
    ALARM_DISARM,
    ALARM_STATE_INDEX,
    SECURITY_STATES,
)
from .const import DOMAIN
from .coordinator import TrustCoordinator
from .entity import TrustEntity

_STATE_MAP = {
    "disarmed": AlarmControlPanelState.DISARMED,
    "pre_walkout": AlarmControlPanelState.ARMING,
    "walkout": AlarmControlPanelState.ARMING,
    "armed": AlarmControlPanelState.ARMED_AWAY,
    "arm_error": AlarmControlPanelState.DISARMED,
    "alarm_activated": AlarmControlPanelState.TRIGGERED,
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: TrustCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        TrustAlarm(coordinator, dev)
        for dev in coordinator.devices.values()
        if dev.description.alarm
    )


class TrustAlarm(TrustEntity, AlarmControlPanelEntity):
    """Arms/disarms exactly like the app (function 15, value 38 / 39)."""

    _attr_code_arm_required = False
    _attr_supported_features = AlarmControlPanelEntityFeature.ARM_AWAY
    _attr_name = None  # use the device name

    def __init__(self, coordinator: TrustCoordinator, device) -> None:
        super().__init__(coordinator, device, "alarm")

    @property
    def _security_state(self) -> str | None:
        raw = self.value(ALARM_STATE_INDEX)
        return SECURITY_STATES.get(raw) if raw is not None else None

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        return _STATE_MAP.get(self._security_state or "")

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        return {"trust_state": self._security_state}

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        await self.send(ALARM_COMMAND_INDEX, ALARM_ARM)

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        await self.send(ALARM_COMMAND_INDEX, ALARM_DISARM)
