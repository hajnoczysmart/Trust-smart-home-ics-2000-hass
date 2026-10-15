"""Trust Smart Home (ICS-2000) integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_IP_ADDRESS, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr

from .api import TrustApiError, TrustAuthError, TrustClient
from .catalog import TrustDevice, parse_sync
from .const import CONF_HOME, DOMAIN, MANUFACTURER, PLATFORMS
from .coordinator import TrustCoordinator

_LOGGER = logging.getLogger(__name__)


def _connect(email: str, password: str, home: str) -> tuple[TrustClient, list[TrustDevice]]:
    """Blocking: log in, select the home and read all devices, groups and scenes."""
    client = TrustClient(email, password)
    try:
        client.login()
        client.select_home(home)
        return client, parse_sync(client.sync())
    except Exception:
        client.close()
        raise


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    try:
        client, devices = await hass.async_add_executor_job(
            _connect,
            entry.data[CONF_EMAIL],
            entry.data[CONF_PASSWORD],
            str(entry.data[CONF_HOME]),
        )
    except TrustAuthError as err:
        raise ConfigEntryAuthFailed from err
    except TrustApiError as err:
        raise ConfigEntryNotReady(f"Cannot reach the Trust cloud: {err}") from err

    coordinator = TrustCoordinator(hass, entry, client, devices)
    if not entry.data.get(CONF_IP_ADDRESS):
        # No address entered: find the hub on the LAN by its MAC (from the account)
        coordinator.discovered_address = await hass.async_add_executor_job(client.discover)
        if coordinator.discovered_address:
            _LOGGER.info("Found the ICS-2000 at %s", coordinator.discovered_address)
        else:
            _LOGGER.info("ICS-2000 not found on the LAN; using the Trust cloud only")
    await coordinator.async_config_entry_first_refresh()

    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, coordinator.mac)},
        manufacturer=MANUFACTURER,
        model="ICS-2000",
        name=client.home_name or "ICS-2000",
    )
    # 1.6-1.8 put the hub LED on a separate "ICS-2000" device; it now lives on
    # the hub device above, so remove the empty duplicate.
    registry = dr.async_get(hass)
    old_hub = (DOMAIN, f"{coordinator.home_id}_hub")
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if old_hub in device.identifiers:
            registry.async_remove_device(device.id)

    for dev in devices:
        if not dev.description.supported:
            _LOGGER.info(
                "Trust device '%s' (%s, type %s) has no dedicated entity; it is "
                "exposed as a diagnostic 'raw status' sensor",
                dev.name,
                dev.type_name,
                dev.device_type,
            )

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(
        entry, [Platform(p) for p in PLATFORMS]
    )
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    entry.async_on_unload(coordinator.async_shutdown_timers)
    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(
        entry, [Platform(p) for p in PLATFORMS]
    )
    if ok:
        coordinator = hass.data[DOMAIN].pop(entry.entry_id, None)
        if coordinator is not None:
            await hass.async_add_executor_job(coordinator.client.close)
    return ok
