"""Device triggers for remotes and wall switches: "Channel 2: ON pressed" etc.

Each button press seen by the event entities is also fired on the event bus
as `trust_smart_home_press`; these triggers listen to it for one device.
"""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA
from homeassistant.components.homeassistant.triggers import event as event_trigger
from homeassistant.const import CONF_DEVICE_ID, CONF_DOMAIN, CONF_PLATFORM, CONF_TYPE
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.trigger import TriggerActionType, TriggerInfo
from homeassistant.helpers.typing import ConfigType

from .catalog import TrustDevice
from .const import DOMAIN, EVENT_PRESS

CONF_SUBTYPE = "subtype"
TRIGGER_TYPES = ("on", "off", "arm", "disarm")

TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend(
    {
        vol.Required(CONF_TYPE): vol.In(TRIGGER_TYPES),
        vol.Required(CONF_SUBTYPE): str,
    }
)


def _trust_device(hass: HomeAssistant, device_id: str) -> TrustDevice | None:
    """The Trust device behind a Home Assistant device id."""
    entry = dr.async_get(hass).async_get(device_id)
    if entry is None:
        return None
    for domain, identifier in entry.identifiers:
        if domain != DOMAIN:
            continue
        home, _, raw_id = identifier.rpartition("_")
        if not raw_id.isdigit():
            continue
        for coordinator in hass.data.get(DOMAIN, {}).values():
            if getattr(coordinator, "home_id", None) == home:
                return coordinator.devices.get(int(raw_id))
    return None


async def async_get_triggers(hass: HomeAssistant, device_id: str) -> list[dict[str, Any]]:
    dev = _trust_device(hass, device_id)
    if dev is None:
        return []
    return [
        {
            CONF_PLATFORM: "device",
            CONF_DOMAIN: DOMAIN,
            CONF_DEVICE_ID: device_id,
            CONF_TYPE: trigger_type,
            CONF_SUBTYPE: subtype,
        }
        for spec in dev.description.events
        for trigger_type, subtype in spec.triggers
    ]


async def async_attach_trigger(
    hass: HomeAssistant,
    config: ConfigType,
    action: TriggerActionType,
    trigger_info: TriggerInfo,
) -> CALLBACK_TYPE:
    event_config = event_trigger.TRIGGER_SCHEMA(
        {
            event_trigger.CONF_PLATFORM: "event",
            event_trigger.CONF_EVENT_TYPE: EVENT_PRESS,
            event_trigger.CONF_EVENT_DATA: {
                CONF_DEVICE_ID: config[CONF_DEVICE_ID],
                CONF_TYPE: config[CONF_TYPE],
                CONF_SUBTYPE: config[CONF_SUBTYPE],
            },
        }
    )
    return await event_trigger.async_attach_trigger(
        hass, event_config, action, trigger_info, platform_type="device"
    )
