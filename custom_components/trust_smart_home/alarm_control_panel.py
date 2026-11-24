"""The ICS-2000 security system (alarm module, device type 240)."""
from __future__ import annotations

import asyncio
import hmac
import time
from datetime import timedelta

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
    CodeFormat,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .catalog import (
    ALARM_ARM,
    ARC_CONTACT,
    ARC_SIREN,
    ALARM_COMMAND_INDEX,
    ALARM_DISARM,
    ALARM_STATE_INDEX,
    SECURITY_STATES,
)
from .const import CONF_ALARM_CODE, DOMAIN, WALKOUT_SECONDS
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


SIREN_PANIC_INDEX = 1
SIREN_PANIC = 21  # SecurityDevice.VALUE_PANIC

CALM_REPEATS = 5  # the app's "calm" (panic off) sends disarm this many times
CALM_INTERVAL = 0.5  # seconds between the repeats


def find_siren(coordinator: TrustCoordinator):
    siren = next(
        (d for d in coordinator.devices.values() if d.device_type in ARC_SIREN),
        None,
    )
    if siren is None:
        raise HomeAssistantError("No security siren is paired with the hub")
    return siren


async def async_panic(coordinator: TrustCoordinator) -> None:
    """Panic: the paired siren's panic function (Siren.panicButton, index 1 = 21)."""
    await coordinator.async_send(
        find_siren(coordinator), SIREN_PANIC_INDEX, SIREN_PANIC, optimistic=False
    )


async def async_calm(coordinator: TrustCoordinator) -> None:
    """Panic off, as the app's calm button: disarm the alarm module, repeated.

    The app sends disarm (function 15, value 39) to the security module five
    times in a row; the hub's status does not show panic going back to 0.
    """
    alarm = next(d for d in coordinator.devices.values() if d.description.alarm)
    for attempt in range(CALM_REPEATS):
        if attempt:
            await asyncio.sleep(CALM_INTERVAL)
        await coordinator.async_send(alarm, ALARM_COMMAND_INDEX, ALARM_DISARM, optimistic=False)


WALKOUT_STATES = ("pre_walkout", "walkout")
ARMING_OR_ARMED = ("pre_walkout", "walkout", "armed")
# The hub's arm rule only starts from a disarmed state, on a change of the
# module's command (function 15) to arm. After an arm error, or after the
# remote disarmed the system (function 15 stays at arm), arming needs a disarm
# first. Wait this long after it so the hub's disarm rule has finished.
ARM_PREPARE_WAIT = 2.0
ARM_PREPARE_MAX = 6.0  # longest wait for the hub to report disarmed before arming
CONTACT_OPEN_INDEX = 1  # 868 MHz ContactSensor: index 1 = 22 opened, 23 closed
CONTACT_OPEN = 22
# Like the app: once a command is accepted, show the expected state at once
# (arming / disarmed) until the hub reports a change, at most this long.
PENDING_MAX = 60.0


class TrustAlarm(TrustEntity, AlarmControlPanelEntity):
    """Arms/disarms exactly like the app (function 15, value 38 / 39).

    The alarm is read from the cloud only. A failed cloud poll keeps the last
    known state instead of making the alarm unavailable; the attribute
    `cloud_stale` tells when that state is older than the last poll.
    `open_contacts` lists the open security contact sensors (they block arming).
    During the walkout the entity counts down an estimate of the time left.
    """

    _attr_supported_features = (
        AlarmControlPanelEntityFeature.ARM_AWAY | AlarmControlPanelEntityFeature.TRIGGER
    )
    _attr_code_trigger_required = False
    _attr_name = None  # use the device name

    def __init__(self, coordinator: TrustCoordinator, device) -> None:
        super().__init__(coordinator, device, "alarm")
        self._walkout_started = None  # when the walkout was first seen
        # (state shown, raw states it replaces, since): set after a command
        self._pending: tuple[AlarmControlPanelState, frozenset[str], float] | None = None

    @property
    def _security_state(self) -> str | None:
        raw = self.value(ALARM_STATE_INDEX)
        return SECURITY_STATES.get(raw) if raw is not None else None

    @property
    def _code(self) -> str:
        """The code set in the options (empty = arming and disarming need no code)."""
        return str(self.coordinator.config_entry.options.get(CONF_ALARM_CODE, "") or "")

    @property
    def code_arm_required(self) -> bool:
        return bool(self._code)

    @property
    def code_disarm_required(self) -> bool:
        return bool(self._code)

    @property
    def code_format(self) -> CodeFormat | None:
        code = self._code
        if not code:
            return None
        return CodeFormat.NUMBER if code.isdigit() else CodeFormat.TEXT

    def _check_code(self, code: str | None) -> None:
        """Arming and disarming need the code when one is set. Trigger (panic) does not."""
        expected = self._code
        if not expected:
            return
        if not hmac.compare_digest((code or "").strip(), expected):
            raise ServiceValidationError("Invalid alarm code")

    @property
    def available(self) -> bool:
        # Keep the last known state through a failed cloud poll (the coordinator
        # keeps the values); unavailable only when there was never a status.
        return self.coordinator.last_update_success and self.status is not None

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        raw = self._security_state or ""
        if self._pending is not None:
            shown, replaces, since = self._pending
            if raw in replaces and time.monotonic() - since < PENDING_MAX:
                return shown
        return _STATE_MAP.get(raw)

    def _set_pending(self, shown: AlarmControlPanelState, *also: str) -> None:
        replaces = frozenset({self._security_state or "", *also})
        self._pending = (shown, replaces, time.monotonic())
        self.async_write_ha_state()

    @property
    def walkout_remaining(self) -> int | None:
        """Estimated seconds left before the alarm is armed, None outside a walkout."""
        if self._walkout_started is None:
            return None
        elapsed = (dt_util.utcnow() - self._walkout_started).total_seconds()
        return max(0, round(WALKOUT_SECONDS - elapsed))

    @property
    def extra_state_attributes(self) -> dict[str, str | int | bool | None]:
        return {
            "trust_state": self._security_state,
            "walkout_remaining": self.walkout_remaining,
            "cloud_stale": self.trust.id in self.coordinator.stale_ids,
            "open_contacts": self.open_contacts,
        }

    @property
    def open_contacts(self) -> list[str]:
        """Names of the security contact sensors that are open: arming fails
        (arm error) while any of them is open."""
        return sorted(
            dev.name
            for dev in self.coordinator.devices.values()
            if dev.device_type in ARC_CONTACT
            and (status := self.coordinator.status(dev.id)) is not None
            and len(status) > CONTACT_OPEN_INDEX
            and status[CONTACT_OPEN_INDEX] == CONTACT_OPEN
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Refresh the countdown every second while the walkout runs
        self.async_on_remove(
            async_track_time_interval(
                self.hass, self._tick_walkout, timedelta(seconds=1)
            )
        )

    @callback
    def _tick_walkout(self, _now) -> None:
        if self._pending is not None and (
            (self._security_state or "") not in self._pending[1]
            or time.monotonic() - self._pending[2] >= PENDING_MAX
        ):
            self._pending = None  # the hub reported a change, or it took too long
            self.async_write_ha_state()
        if self._walkout_started is not None:
            self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        if self._security_state in WALKOUT_STATES:
            if self._walkout_started is None:
                self._walkout_started = dt_util.utcnow()
        else:
            self._walkout_started = None
        super()._handle_coordinator_update()

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        self._check_code(code)
        state = self._security_state
        if state in ARMING_OR_ARMED:
            return  # already arming or armed
        # shown as arming at once; during a disarm first, also over "disarmed"
        self._set_pending(AlarmControlPanelState.ARMING, "disarmed")
        try:
            if state != "disarmed" or self.value(ALARM_COMMAND_INDEX) != ALARM_DISARM:
                # arm error, alarm, unknown, or function 15 still at arm (remote
                # disarm): the hub's arm rule would not start, so disarm first
                # and wait until the hub reports disarmed (or ARM_PREPARE_MAX)
                await self.send(ALARM_COMMAND_INDEX, ALARM_DISARM)
                deadline = time.monotonic() + ARM_PREPARE_MAX
                await asyncio.sleep(ARM_PREPARE_WAIT)
                while self._security_state != "disarmed" and time.monotonic() < deadline:
                    await asyncio.sleep(0.25)
                self._set_pending(AlarmControlPanelState.ARMING, "disarmed")
            await self.send(ALARM_COMMAND_INDEX, ALARM_ARM)
        except Exception:
            self._pending = None
            self.async_write_ha_state()
            raise

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        self._check_code(code)
        await self.send(ALARM_COMMAND_INDEX, ALARM_DISARM)
        if self._security_state != "disarmed":
            self._set_pending(AlarmControlPanelState.DISARMED)

    async def async_alarm_trigger(self, code: str | None = None) -> None:
        await async_panic(self.coordinator)
