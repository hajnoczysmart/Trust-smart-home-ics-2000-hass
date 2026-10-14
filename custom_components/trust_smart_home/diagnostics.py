"""Diagnostics download (Settings > Devices & services > Trust Smart Home > ⋮).

Contains what is needed to debug device support: the device list with
types, info bytes and current status lists. Credentials, the hub key and
MAC, the home ID and the hub address are removed.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_IP_ADDRESS, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from .const import CONF_HOME, DOMAIN

TO_REDACT = {CONF_EMAIL, CONF_PASSWORD, CONF_IP_ADDRESS, CONF_HOME, "aes_key", "mac"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    result: dict[str, Any] = {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
    }
    if coordinator is None:
        result["loaded"] = False
        return result
    result["loaded"] = True
    result["local_control"] = coordinator.local_address is not None
    result["last_update_success"] = coordinator.last_update_success
    statuses = coordinator.data or {}
    devices = []
    for dev in sorted(coordinator.devices.values(), key=lambda d: d.id):
        description = asdict(dev.description)
        devices.append(
            {
                "id": dev.id,
                "kind": dev.kind,
                "name": dev.name,
                "device_type": dev.device_type,
                "type_name": dev.type_name,
                "info": dev.info,
                "members": dev.members,
                "hidden": dev.hidden,
                "supported": dev.description.supported,
                "entities": {
                    k: v for k, v in description.items() if v not in (None, False, [], {})
                },
                "status": statuses.get(dev.id),
            }
        )
    result["devices"] = devices
    return result
