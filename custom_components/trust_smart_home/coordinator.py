"""Data coordinator for Trust Smart Home (ICS-2000)."""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_IP_ADDRESS
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    SETTING_FW_UPDATE_DISABLED,
    SETTING_LOCAL_ONLY,
    SETTING_ZIGBEE_CHANNEL,
    TrustApiError,
    TrustAuthError,
    TrustClient,
)
from .catalog import (
    TrustDevice,
    derive_group_status,
    group_state_indexes,
    plausible_local_status,
    reads_locally,
    status_value,
)
from .const import (
    CONF_LOCAL_STATUS,
    CONF_MAPPINGS,
    CONF_POWER,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    REFRESH_AFTER_COMMAND,
)

# Seconds after our latest command during which the cloud reporting one of our
# older commands is treated as a late echo. Later, every change is taken over
# (so changes made in the app or on the device are never hidden for long).
ECHO_WINDOW = 60.0

# Reading statuses from the hub on the LAN (experimental option):
LOCAL_MISSES = 3  # unanswered requests in a row before a device goes back to the cloud
LOCAL_RETRY = 60.0  # seconds before trying the hub again after a round without answers

_LOGGER = logging.getLogger(__name__)


def scan_interval(entry: ConfigEntry) -> timedelta:
    """Polling interval from the options (default 3 s)."""
    try:
        seconds = int(entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
    except (TypeError, ValueError):
        seconds = DEFAULT_SCAN_INTERVAL
    return timedelta(seconds=max(MIN_SCAN_INTERVAL, min(MAX_SCAN_INTERVAL, seconds)))


def _is_dim_index(dev: TrustDevice, index: int) -> bool:
    light = dev.description.light
    return light is not None and index == light.dim_index


class TrustCoordinator(DataUpdateCoordinator[dict[int, list[int]]]):
    """Polls the status lists of all devices and groups of one home."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: TrustClient,
        devices: list[TrustDevice],
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=scan_interval(entry),
        )
        self.client = client
        self.devices: dict[int, TrustDevice] = {d.id: d for d in devices}
        # What entities see. A value only changes here when we send a command or
        # when the cloud reports a *change*: the cloud often keeps returning an
        # old value after a command (especially for groups and their members),
        # and an unchanged stale value must not overwrite what we just set.
        self._statuses: dict[int, list[int]] = {}
        self._reported: dict[int, list[int]] = {}  # last values from the cloud
        self._cancel_refresh: Any = None
        # Reading statuses from the hub on the LAN (experimental): None = not
        # tried yet, True = works, False = the hub did not answer (tried again later).
        self.local_status: bool | None = None
        self.local_ok: set[int] = set()  # read from the hub (first answer checked)
        self.local_excluded: set[int] = set()  # their answers did not fit: cloud
        self._local_misses: dict[int, int] = {}
        self._local_next = 0  # where the next round starts (round robin)
        self._local_retry_at = 0.0
        # devices whose state could not be refreshed because the cloud failed
        # while the hub answered on the LAN: shown as unavailable
        self.stale_ids: set[int] = set()
        # Hub address found on the LAN at start-up when none is configured
        self.discovered_address: str | None = None
        # (entity id, index) -> (value sent, expiry): event entities do not report
        # the change our own command causes
        self.own_changes: dict[tuple[int, int], tuple[int, float]] = {}
        # (entity id, index) -> values we sent recently, newest last. The cloud
        # often reports a burst of commands (police effect, fades, quick changes)
        # one by one, minutes late; a reported value that is one of our *older*
        # commands is such a late echo and must not overwrite the newest one.
        self._sent: dict[tuple[int, int], deque[tuple[int, float]]] = {}
        # running animations (police effect, fades): owner -> affected entity ids
        self.animations: dict[Any, set[int]] = {}
        # hub information read on the LAN: {"device": {...}, "settings": {...}}
        self.hub_info: dict[str, dict[str, Any]] = {}

    def is_own_change(self, entity_id: int, index: int, value: int) -> bool:
        """True (once) if this value change was caused by our own command."""
        pending = self.own_changes.pop((entity_id, index), None)
        if pending is None:
            return False
        sent, expiry = pending
        return sent == value and time.monotonic() < expiry

    # ---- identity --------------------------------------------------------

    @property
    def home_id(self) -> str:
        return str(self.client.home_id)

    @property
    def mac(self) -> str:
        return str(self.client.mac or self.home_id)

    @property
    def local_address(self) -> str | None:
        """Configured hub address, else the one found on the LAN."""
        return self.config_entry.data.get(CONF_IP_ADDRESS) or self.discovered_address

    @property
    def mappings(self) -> dict[str, dict[str, Any]]:
        return dict(self.config_entry.options.get(CONF_MAPPINGS, {}))

    @property
    def power_settings(self) -> dict[str, dict[str, Any]]:
        """{device id: {watts, scale}} of devices with an estimated power."""
        return dict(self.config_entry.options.get(CONF_POWER, {}))

    # ---- status ----------------------------------------------------------

    def status(self, entity_id: int) -> list[int] | None:
        return (self.data or {}).get(entity_id)

    def _poll_ids(self) -> list[int]:
        return [d.id for d in self.devices.values() if d.kind in ("module", "group", "scenario")]

    def _local_candidates(self) -> list[int]:
        """Devices to read from the hub this round, in round-robin order."""
        if not self.local_address or not self.config_entry.options.get(CONF_LOCAL_STATUS, False):
            return []
        if time.monotonic() < self._local_retry_at:
            return []
        ids = [
            d.id
            for d in sorted(self.devices.values(), key=lambda d: d.id)
            if d.id not in self.local_excluded and reads_locally(d)
        ]
        if not ids:
            return []
        start = self._local_next % len(ids)
        return ids[start:] + ids[:start]

    async def _async_update_data(self) -> dict[int, list[int]]:
        """Statuses from the cloud, and (when the experimental option is on)
        from the hub on the LAN for the devices that answer correctly there.
        Both run at the same time, so a poll does not take longer."""
        ids = self._poll_ids()
        order = self._local_candidates()
        in_order = set(order)
        cloud_ids = [i for i in ids if i not in self.local_ok or i not in in_order]
        local_job = (
            self.hass.async_add_executor_job(
                self.client.get_statuses_local, order, self.local_address
            )
            if order
            else None
        )
        cloud_job = (
            self.hass.async_add_executor_job(self.client.get_statuses, cloud_ids)
            if cloud_ids
            else None
        )
        jobs = [j for j in (local_job, cloud_job) if j is not None]
        results = await asyncio.gather(*jobs, return_exceptions=True)
        local_res = results[0] if local_job is not None else None
        cloud_res = results[-1] if cloud_job is not None else {}

        cloud: dict[int, list[int]] = {}
        cloud_error: TrustApiError | None = None
        if isinstance(cloud_res, TrustAuthError):
            raise ConfigEntryAuthFailed from cloud_res
        if isinstance(cloud_res, TrustApiError):
            cloud_error = cloud_res
        elif isinstance(cloud_res, BaseException):
            raise cloud_res
        else:
            cloud = dict(cloud_res)

        fresh = dict(cloud)
        if order:
            if isinstance(local_res, BaseException):
                _LOGGER.debug("Reading statuses on the LAN failed: %s", local_res)
                got, asked = {}, len(order)
            else:
                got, asked = local_res
            fresh.update(self._use_local(order, got, asked, cloud))
            if not got and cloud_error is None:
                # the hub did not answer this round: these come from the cloud now
                missing = [i for i in order if i in self.local_ok]
                if missing:
                    try:
                        fresh.update(
                            await self.hass.async_add_executor_job(
                                self.client.get_statuses, missing
                            )
                        )
                    except TrustAuthError as err:
                        raise ConfigEntryAuthFailed from err
                    except TrustApiError as err:
                        cloud_error = err

        if cloud_error is not None:
            local_now = [i for i in fresh if i in self.local_ok]
            if not local_now:
                raise UpdateFailed(str(cloud_error)) from cloud_error
            self.stale_ids = {
                i for i in ids if i not in self.local_ok and not self._follows_local(i)
            }
            _LOGGER.debug(
                "Cloud unavailable (%s); %d devices read on the LAN", cloud_error, len(local_now)
            )
        else:
            self.stale_ids = set()
        self._merge(fresh)
        return {k: list(v) for k, v in self._statuses.items()}

    def _follows_local(self, entity_id: int) -> bool:
        """A group whose members are all read on the LAN stays up to date."""
        dev = self.devices.get(entity_id)
        return bool(dev and dev.kind == "group" and dev.members) and all(
            m in self.local_ok for m in dev.members
        )

    def _use_local(
        self,
        order: list[int],
        got: dict[int, list[int]],
        asked: int,
        cloud: dict[int, list[int]],
    ) -> dict[int, list[int]]:
        """The hub's answers that may be used, and which devices are read on
        the LAN. A device's first answer is checked against the cloud's."""
        use: dict[int, list[int]] = {}
        if not got:
            if self.local_status is None:
                _LOGGER.info("The hub does not answer status requests on the LAN; using the cloud")
                self.local_status = False
            else:
                _LOGGER.debug("No status answers from the hub; trying again in %d s", LOCAL_RETRY)
            self._local_retry_at = time.monotonic() + LOCAL_RETRY
            return use
        if self.local_status is not True:
            _LOGGER.info(
                "Reading device states from the hub on the LAN (experimental, "
                "not tested with every device type)"
            )
        self.local_status = True
        self._local_next = (self._local_next + asked) % max(1, len(order))
        for entity_id in order[:asked]:
            status = got.get(entity_id)
            if status is None:
                misses = self._local_misses.get(entity_id, 0) + 1
                self._local_misses[entity_id] = misses
                if misses >= LOCAL_MISSES:
                    self._local_misses.pop(entity_id, None)
                    if entity_id in self.local_ok:
                        self.local_ok.discard(entity_id)  # checked again later
                    else:
                        self.local_excluded.add(entity_id)
                        _LOGGER.debug(
                            "Device %s does not answer on the LAN; using the cloud", entity_id
                        )
                continue
            self._local_misses.pop(entity_id, None)
            if entity_id in self.local_ok:
                use[entity_id] = status
            elif entity_id in cloud:
                if plausible_local_status(status, cloud[entity_id]):
                    self.local_ok.add(entity_id)  # from the next round on
                else:
                    self.local_excluded.add(entity_id)
                    _LOGGER.debug(
                        "Device %s: the hub's answer does not fit the cloud's; using the cloud",
                        entity_id,
                    )
        return use

    def _merge(self, fresh: dict[int, list[int]]) -> None:
        """Apply what the cloud reports, but only the values that changed."""
        changed: set[tuple[int, int]] = set()
        first: set[int] = set()
        for eid, new in fresh.items():
            new = [int(v) for v in new]
            old = self._reported.get(eid)
            shown = self._statuses.get(eid)
            if old is None or shown is None:
                self._statuses[eid] = list(new)
                first.add(eid)
            else:
                shown = list(shown)
                for i, value in enumerate(new):
                    if i >= len(old) or value != old[i]:
                        if self._is_late_echo(eid, i, value):
                            continue
                        if i >= len(shown):
                            shown.extend([0] * (i + 1 - len(shown)))
                        shown[i] = value
                        changed.add((eid, i))
                self._statuses[eid] = shown
            self._reported[eid] = new
        if changed and _LOGGER.isEnabledFor(logging.DEBUG):
            _LOGGER.debug(
                "Status changes: %s",
                ", ".join(f"{e}[{i}]={self._statuses[e][i]}" for e, i in sorted(changed)),
            )
        for dev in self.devices.values():
            if dev.kind == "group" and dev.members:
                self._merge_group(dev, changed, first)

    def _merge_group(self, dev: TrustDevice, changed: set[tuple[int, int]], first: set[int]) -> None:
        """A group follows its members: the hub does not keep the group's own
        status up to date, but members report what they really did."""
        on_index, others = group_state_indexes(dev.description)
        if on_index is None:
            return
        if dev.id in first or dev.id not in self._statuses:
            derived = derive_group_status(
                self._statuses.get(dev.id),
                [self._statuses.get(m) for m in dev.members],
                dev.description,
            )
            if derived is not None:
                self._statuses[dev.id] = derived
            return
        for index in [on_index, *others]:
            movers = [m for m in dev.members if (m, index) in changed]
            if not movers:
                continue
            if index == on_index:
                states = [status_value(self._statuses.get(m), on_index) for m in dev.members]
                self._write(dev.id, index, 1 if any(s == 1 for s in states) else 0)
            elif len(movers) == 1 or not _is_dim_index(dev, index):
                # one member changed: the group takes its value (colour: the last one)
                value = status_value(self._statuses.get(movers[-1]), index)
                if value is not None:
                    self._write(dev.id, index, value)
            else:
                # several members changed at once: same rule as at start-up
                # (average of the lit members), so the result does not depend on
                # the order in which the cloud reports them
                derived = derive_group_status(
                    self._statuses.get(dev.id),
                    [self._statuses.get(m) for m in dev.members],
                    dev.description,
                )
                if derived is not None and status_value(derived, index) is not None:
                    self._write(dev.id, index, derived[index])

    def _is_late_echo(self, eid: int, index: int, value: int) -> bool:
        """True if `value` is an older command of ours that the cloud reports late."""
        history = self._sent.get((eid, index))
        if not history:
            return False
        latest, sent_at = history[-1]
        if value == latest or time.monotonic() - sent_at > ECHO_WINDOW:
            return False  # the newest command confirmed, or no command of ours lately
        return any(sent == value for sent, _t in history)

    def _remember_sent(self, eid: int, index: int, value: int) -> None:
        history = self._sent.setdefault((eid, index), deque(maxlen=128))
        history.append((value, time.monotonic()))

    @callback
    def set_local(self, device: TrustDevice, index: int, value: int) -> None:
        """Change a shown value without sending anything (the hub sets it itself,
        e.g. the colour pointer after a colour command), for a group also its members."""
        targets = [device.id, *device.members] if device.kind == "group" else [device.id]
        for eid in targets:
            self._write(eid, index, int(value))
            self._remember_sent(eid, index, int(value))
        self.async_set_updated_data({k: list(v) for k, v in self._statuses.items()})

    async def async_stop_animations(self, entity_ids: set[int], keep: Any = None) -> None:
        """Stop police effects and fades that touch these entities (except `keep`)."""
        for owner, ids in list(self.animations.items()):
            if owner is not keep and ids & entity_ids:
                await owner.async_stop_animation()

    def _write(self, eid: int, index: int, value: int) -> None:
        status = list(self._statuses.get(eid, []))
        if len(status) <= index:
            status.extend([0] * (index + 1 - len(status)))
        status[index] = value
        self._statuses[eid] = status

    # ---- commands --------------------------------------------------------

    async def async_send(
        self, device: TrustDevice, index: int, value: int, optimistic: bool = True
    ) -> None:
        """Send one action, update the local state optimistically, then re-poll.

        Momentary actions (button presses) pass optimistic=False: they trigger
        something but do not set a lasting state.
        """
        try:
            await self.hass.async_add_executor_job(
                self.client.send, device.kind, device.id, index, int(value), self.local_address
            )
        except TrustApiError as err:
            raise HomeAssistantError(f"Trust Smart Home: {err}") from err
        if device.description.events:
            # Transmitters: wait for the hub instead of guessing, and remember
            # what we sent so the resulting change is not reported as a press.
            self.own_changes[(device.id, index)] = (int(value), time.monotonic() + 30)
        elif optimistic and device.kind != "scene":
            # A group command also sets its members (update_group_members=true).
            targets = [device.id, *device.members] if device.kind == "group" else [device.id]
            for eid in targets:
                self._write(eid, index, int(value))
                self._remember_sent(eid, index, int(value))
            self.async_set_updated_data({k: list(v) for k, v in self._statuses.items()})
        self._schedule_refresh()

    # ---- the hub itself -------------------------------------------------

    async def async_refresh_hub_info(self) -> None:
        """Read firmware versions and a few settings from the hub (LAN only)."""
        host = self.local_address
        if not host:
            return

        def _read() -> dict[str, dict[str, Any]]:
            settings = {
                key: self.client.get_setting(key, host)
                for key in (SETTING_FW_UPDATE_DISABLED, SETTING_ZIGBEE_CHANNEL, SETTING_LOCAL_ONLY)
            }
            return {"device": self.client.get_device_info(host) or {}, "settings": settings}

        self.hub_info = await self.hass.async_add_executor_job(_read)
        self.async_update_listeners()

    async def async_set_hub_setting(self, key: str, value: Any) -> None:
        try:
            await self.hass.async_add_executor_job(
                self.client.set_setting, {key: value}, self.local_address
            )
        except TrustApiError as err:
            raise HomeAssistantError(f"Trust Smart Home: {err}") from err
        self.hub_info.setdefault("settings", {})[key] = value
        self.async_update_listeners()

    async def async_reboot(self) -> None:
        try:
            await self.hass.async_add_executor_job(self.client.reboot, self.local_address)
        except TrustApiError as err:
            raise HomeAssistantError(f"Trust Smart Home: {err}") from err

    # ---- hub LED (experimental) ------------------------------------------

    async def async_read_led(self) -> str | None:
        """Ask the hub for its LED setting (LAN only); None if unknown."""
        return await self.hass.async_add_executor_job(self.client.get_led, self.local_address)

    async def async_set_led(self, state: str) -> None:
        try:
            await self.hass.async_add_executor_job(
                self.client.set_led, state, self.local_address
            )
        except TrustApiError as err:
            raise HomeAssistantError(f"Trust Smart Home: {err}") from err

    @callback
    def _schedule_refresh(self) -> None:
        if self._cancel_refresh:
            self._cancel_refresh()

        @callback
        def _refresh(_now: Any) -> None:
            self._cancel_refresh = None
            self.hass.async_create_task(self.async_request_refresh())

        self._cancel_refresh = async_call_later(self.hass, REFRESH_AFTER_COMMAND, _refresh)

    @callback
    def async_shutdown_timers(self) -> None:
        if self._cancel_refresh:
            self._cancel_refresh()
            self._cancel_refresh = None
