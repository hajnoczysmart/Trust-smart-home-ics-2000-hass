"""Data coordinator for Trust Smart Home (ICS-2000)."""
from __future__ import annotations

import logging
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_IP_ADDRESS
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import TrustApiError, TrustAuthError, TrustClient
from .catalog import TrustDevice, derive_group_status
from .const import (
    CONF_MAPPINGS,
    DOMAIN,
    HOLD_AFTER_COMMAND,
    REFRESH_AFTER_COMMAND,
    SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


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
            update_interval=SCAN_INTERVAL,
        )
        self.client = client
        self.devices: dict[int, TrustDevice] = {d.id: d for d in devices}
        self._statuses: dict[int, list[int]] = {}
        self._cancel_refresh: Any = None
        # (entity id, index) -> (value sent, expiry): event entities do not report
        # the change our own command causes
        self.own_changes: dict[tuple[int, int], tuple[int, float]] = {}
        # (entity id, index) -> (value sent, expiry): keep showing what we sent
        # until the cloud confirms it, instead of jumping back to a stale value
        self._pending: dict[tuple[int, int], tuple[int, float]] = {}

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
        return self.config_entry.data.get(CONF_IP_ADDRESS) or None

    @property
    def mappings(self) -> dict[str, dict[str, Any]]:
        return dict(self.config_entry.options.get(CONF_MAPPINGS, {}))

    # ---- status ----------------------------------------------------------

    def status(self, entity_id: int) -> list[int] | None:
        return (self.data or {}).get(entity_id)

    def _poll_ids(self) -> list[int]:
        return [d.id for d in self.devices.values() if d.kind in ("module", "group")]

    async def _async_update_data(self) -> dict[int, list[int]]:
        try:
            fresh = await self.hass.async_add_executor_job(
                self.client.get_statuses, self._poll_ids()
            )
        except TrustAuthError as err:
            raise ConfigEntryAuthFailed from err
        except TrustApiError as err:
            raise UpdateFailed(str(err)) from err
        self._statuses.update({k: list(v) for k, v in fresh.items()})
        self._apply_pending(fresh)
        return self._compose()

    def _apply_pending(self, fresh: dict[int, list[int]]) -> None:
        """Re-apply values we sent until the cloud reports them (or they expire)."""
        now = time.monotonic()
        for (eid, index), (value, expiry) in list(self._pending.items()):
            reported = fresh.get(eid)
            confirmed = reported is not None and index < len(reported) and reported[index] == value
            if now >= expiry or confirmed:
                del self._pending[(eid, index)]
            elif eid in self._statuses:
                self._write(eid, index, value)

    def _write(self, eid: int, index: int, value: int) -> None:
        status = list(self._statuses.get(eid, []))
        if len(status) <= index:
            status.extend([0] * (index + 1 - len(status)))
        status[index] = value
        self._statuses[eid] = status

    def _compose(self) -> dict[int, list[int]]:
        """Statuses as entities see them; groups are derived from their members."""
        data = dict(self._statuses)
        for dev in self.devices.values():
            if dev.kind == "group" and dev.members:
                derived = derive_group_status(
                    self._statuses.get(dev.id),
                    [self._statuses.get(m) for m in dev.members],
                    dev.description,
                )
                if derived is not None:
                    data[dev.id] = derived
        return data

    # ---- commands --------------------------------------------------------

    async def async_send(self, device: TrustDevice, index: int, value: int) -> None:
        """Send one action, update the local state optimistically, then re-poll."""
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
        elif device.kind != "scene":
            # A group command also sets its members (update_group_members=true);
            # a group with known members is derived from them, so hold those.
            held = device.members if (device.kind == "group" and device.members) else [device.id]
            expiry = time.monotonic() + HOLD_AFTER_COMMAND
            self._write(device.id, index, int(value))
            for eid in held:
                self._write(eid, index, int(value))
                self._pending[(eid, index)] = (int(value), expiry)
            self.async_set_updated_data(self._compose())
        self._schedule_refresh()

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
