"""Data coordinator for Trust Smart Home (ICS-2000)."""
from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_IP_ADDRESS
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import TrustApiError, TrustAuthError, TrustClient
from .catalog import TrustDevice, derive_group_status, group_state_indexes, status_value
from .const import (
    CONF_LOCAL_STATUS,
    CONF_MAPPINGS,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    REFRESH_AFTER_COMMAND,
)

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
        # tried yet, True = works, False = the hub did not answer -> cloud.
        self.local_status: bool | None = None
        # Hub address found on the LAN at start-up when none is configured
        self.discovered_address: str | None = None
        # (entity id, index) -> (value sent, expiry): event entities do not report
        # the change our own command causes
        self.own_changes: dict[tuple[int, int], tuple[int, float]] = {}

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

    # ---- status ----------------------------------------------------------

    def status(self, entity_id: int) -> list[int] | None:
        return (self.data or {}).get(entity_id)

    def _poll_ids(self) -> list[int]:
        return [d.id for d in self.devices.values() if d.kind in ("module", "group", "scenario")]

    def _fetch_statuses(self) -> dict[int, list[int]]:
        """Runs in the executor: LAN first when the hub answers, else the cloud."""
        ids = self._poll_ids()
        host = self.local_address
        enabled = bool(self.config_entry.options.get(CONF_LOCAL_STATUS, False))
        if host and enabled and self.local_status is not False:
            entities = [(self.devices[i].kind, i) for i in ids]
            local = self.client.get_statuses_local(entities, host)
            if local:
                if self.local_status is None:
                    _LOGGER.info("Reading statuses from the hub on the LAN")
                    self.local_status = True
                missing = [i for i in ids if i not in local]
                if missing:
                    local.update(self.client.get_statuses(missing))
                return local
            if self.local_status is None:
                _LOGGER.debug("The hub does not answer status requests; using the cloud")
                self.local_status = False
        return self.client.get_statuses(ids)

    async def _async_update_data(self) -> dict[int, list[int]]:
        try:
            fresh = await self.hass.async_add_executor_job(self._fetch_statuses)
        except TrustAuthError as err:
            raise ConfigEntryAuthFailed from err
        except TrustApiError as err:
            raise UpdateFailed(str(err)) from err
        self._merge(fresh)
        return {k: list(v) for k, v in self._statuses.items()}

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
                        if i >= len(shown):
                            shown.extend([0] * (i + 1 - len(shown)))
                        shown[i] = value
                        changed.add((eid, i))
                self._statuses[eid] = shown
            self._reported[eid] = new
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
            self.async_set_updated_data({k: list(v) for k, v in self._statuses.items()})
        self._schedule_refresh()

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
