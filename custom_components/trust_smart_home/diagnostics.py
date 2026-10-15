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
    result["hub_address_found_automatically"] = coordinator.discovered_address is not None
    result["local_status"] = coordinator.local_status
    host = coordinator.local_address
    probe_dev = next(
        (d for d in sorted(coordinator.devices.values(), key=lambda d: d.id) if d.kind == "module"),
        None,
    )
    if host and probe_dev is not None:
        # how the hub answers status requests on the LAN (to develop local polling)
        replies = await hass.async_add_executor_job(
            coordinator.client.probe_local_status, probe_dev.kind, probe_dev.id, host
        )
        mac = str(coordinator.client.mac or "")
        secrets = {v for v in (mac, mac.lower(), ":".join(mac[i : i + 2] for i in range(0, len(mac), 2))) if v}
        for reply in replies:
            for key in ("data", "failed"):
                if isinstance(reply.get(key), str):
                    for secret in secrets:
                        reply[key] = reply[key].replace(secret, "**REDACTED**")
                    if host:
                        reply[key] = reply[key].replace(host, "**REDACTED**")
        result["local_status_probe"] = {
            "device_id": probe_dev.id,
            "cloud_status": (coordinator.data or {}).get(probe_dev.id),
            "replies": replies,
        }
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
