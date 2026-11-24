"""Diagnostics download (Settings > Devices & services > Trust Smart Home > ⋮).

Contains what is needed to debug device support: the device list with
types, info bytes and current status lists. Credentials, the hub key and
MAC, the home ID and the hub address are removed.
"""
from __future__ import annotations

import time
from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_IP_ADDRESS, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from .const import CONF_ALARM_CODE, CONF_HOME, DOMAIN

SECURITY_TYPES = {16, 17, 18, 19, 20, 240}  # 868 MHz security devices, alarm module


def _summary(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0}
    ordered = sorted(values)
    return {
        "count": len(values),
        "last": round(values[-1]),
        "median": round(ordered[len(ordered) // 2]),
        "max": round(ordered[-1]),
    }

TO_REDACT = {CONF_EMAIL, CONF_PASSWORD, CONF_IP_ADDRESS, CONF_HOME, "aes_key", "mac"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    result: dict[str, Any] = {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": async_redact_data(dict(entry.options), {CONF_ALARM_CODE}),
        },
    }
    if coordinator is None:
        result["loaded"] = False
        return result
    result["loaded"] = True
    result["local_control"] = coordinator.local_address is not None
    result["hub_address_found_automatically"] = coordinator.discovered_address is not None
    result["local_status"] = coordinator.local_status
    result["local_status_devices"] = {
        "read_on_lan": sorted(coordinator.local_ok),
        "kept_on_cloud": sorted(coordinator.local_excluded),
        "unavailable_cloud_down": sorted(coordinator.stale_ids),
        "polls_without_hub_answer": coordinator._local_silent_rounds,
    }
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
        # does acknowledging an answer remove the hub's ~1 s pause? (issue #2)
        ack_dev = next(
            (
                d
                for d in sorted(coordinator.devices.values(), key=lambda d: d.id)
                if d.kind == "module"
                and (d.description.light is not None or d.description.switch is not None)
            ),
            probe_dev,
        )
        runs = await hass.async_add_executor_job(
            coordinator.client.probe_acknowledgement, ack_dev.id, host
        )
        result["local_status_ack_test"] = {
            "device_id": ack_dev.id,
            "device_type": ack_dev.type_name,
            "runs": runs,
        }
    result["last_update_success"] = coordinator.last_update_success
    # where the status delay comes from: the cloud request time and the real
    # time between polls (the poll waits for the previous one to finish)
    cloud_ms = list(coordinator.cloud_poll_ms)
    starts = list(coordinator.poll_starts)
    gaps = [(b - a) * 1000 for a, b in zip(starts, starts[1:])]
    result["poll_timing"] = {
        "scan_interval_s": coordinator.update_interval.total_seconds()
        if coordinator.update_interval
        else None,
        "cloud_request_ms": _summary(cloud_ms),
        "time_between_polls_ms": _summary(gaps),
    }
    if host:
        # Does the hub answer the security devices' statuses on the LAN, and
        # do they match the cloud? (Reading them there would be much faster.)
        security = [
            d
            for d in sorted(coordinator.devices.values(), key=lambda d: d.id)
            if d.kind == "module" and d.device_type in SECURITY_TYPES
        ]

        def _read_security() -> list[dict[str, Any]]:
            out = []
            for dev in security:
                start = time.monotonic()
                lan = coordinator.client.get_status_local(dev.id, host)
                out.append(
                    {
                        "id": dev.id,
                        "type_name": dev.type_name,
                        "cloud": (coordinator.data or {}).get(dev.id),
                        "lan": lan,
                        "lan_ms": round((time.monotonic() - start) * 1000),
                    }
                )
            return out

        result["security_lan_probe"] = await hass.async_add_executor_job(_read_security)
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
