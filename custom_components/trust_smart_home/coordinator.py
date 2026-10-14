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
from .catalog import TrustDevice
from .const import CONF_MAPPINGS, DOMAIN, REFRESH_AFTER_COMMAND, SCAN_INTERVAL

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
        self._statuses.update(fresh)
        return dict(self._statuses)

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
            status = list(self._statuses.get(device.id, []))
            if len(status) <= index:
                status.extend([0] * (index + 1 - len(status)))
            status[index] = int(value)
            self._statuses[device.id] = status
            self.async_set_updated_data(dict(self._statuses))
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
