"""Config, reauth and options flow for Trust Smart Home (ICS-2000)."""
from __future__ import annotations

import ipaddress
import logging
import re
from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_EMAIL, CONF_IP_ADDRESS, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er

from .api import TrustApiError, TrustAuthError, TrustClient
from .catalog import can_estimate_power
from .const import (
    BINARY_DEVICE_CLASSES,
    CONF_HOME,
    CONF_MAPPINGS,
    CONF_LOCAL_STATUS,
    CONF_POWER,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_POWER_WATTS,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    ROLE_BINARY_SENSOR,
    ROLE_NONE,
    ROLE_SENSOR,
)

_LOGGER = logging.getLogger(__name__)


def _login(email: str, password: str) -> dict[str, str]:
    """Blocking: authenticate and return {home_id: home_name}."""
    client = TrustClient(email, password)
    try:
        return client.login()
    finally:
        client.close()


_HOSTNAME = re.compile(r"^(?=.{1,253}$)([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*\.?$")


def _valid_host(value: str) -> bool:
    """The hub address must be an IP address or a host name, nothing else."""
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return bool(_HOSTNAME.match(value))


class TrustConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._homes: dict[str, str] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return TrustOptionsFlow()

    async def _try_login(self, email: str, password: str) -> str | None:
        """Returns an error key, or None on success."""
        try:
            self._homes = await self.hass.async_add_executor_job(_login, email, password)
        except TrustAuthError:
            return "invalid_auth"
        except TrustApiError:
            return "cannot_connect"
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Unexpected error during login")
            return "unknown"
        return None if self._homes else "no_homes"

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._data = {k: v for k, v in user_input.items() if v not in (None, "")}
            host = str(self._data.get(CONF_IP_ADDRESS, "")).strip()
            if host:
                self._data[CONF_IP_ADDRESS] = host
            if host and not _valid_host(host):
                error: str | None = "invalid_host"
            else:
                error = await self._try_login(user_input[CONF_EMAIL], user_input[CONF_PASSWORD])
            if error:
                errors["base"] = error
            elif len(self._homes) == 1:
                return await self._create(next(iter(self._homes)))
            else:
                return await self.async_step_home()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_EMAIL): str,
                    vol.Required(CONF_PASSWORD): str,
                    vol.Optional(CONF_IP_ADDRESS): str,
                }
            ),
            errors=errors,
        )

    async def async_step_home(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return await self._create(user_input[CONF_HOME])
        return self.async_show_form(
            step_id="home",
            data_schema=vol.Schema({vol.Required(CONF_HOME): vol.In(self._homes)}),
        )

    async def _create(self, home_id: str) -> ConfigFlowResult:
        await self.async_set_unique_id(str(home_id))
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=self._homes.get(str(home_id), str(home_id)),
            data={**self._data, CONF_HOME: str(home_id)},
        )

    # ---- reauth ----------------------------------------------------------

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            error = await self._try_login(entry.data[CONF_EMAIL], user_input[CONF_PASSWORD])
            if error:
                errors["base"] = error
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            description_placeholders={"email": entry.data[CONF_EMAIL]},
            errors=errors,
        )


class TrustOptionsFlow(OptionsFlow):
    """Settings, extra sensors for raw status positions, estimated power."""

    def __init__(self) -> None:
        self._dev_id: int | None = None

    def _coordinator(self):
        return self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id)

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="init", menu_options=["settings", "pick_device", "power_pick"]
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                title="", data={**self.config_entry.options, **user_input}
            )
        current = self.config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        local = bool(self.config_entry.options.get(CONF_LOCAL_STATUS, False))
        return self.async_show_form(
            step_id="settings",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SCAN_INTERVAL, default=current): vol.All(
                        vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL)
                    ),
                    vol.Required(CONF_LOCAL_STATUS, default=local): bool,
                }
            ),
        )

    async def async_step_pick_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        coordinator = self._coordinator()
        if coordinator is None or not coordinator.devices:
            return self.async_abort(reason="not_loaded")
        if user_input is not None:
            self._dev_id = int(user_input["device"])
            return await self.async_step_map_device()
        mapped = coordinator.mappings
        choices = {
            str(dev.id): f"{dev.name} [{dev.type_name}]" + (" *" if str(dev.id) in mapped else "")
            for dev in sorted(coordinator.devices.values(), key=lambda d: d.name.lower())
            if dev.kind in ("module", "group")
        }
        return self.async_show_form(
            step_id="pick_device",
            data_schema=vol.Schema({vol.Required("device"): vol.In(choices)}),
        )

    async def async_step_map_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        coordinator = self._coordinator()
        dev = coordinator.devices.get(self._dev_id) if coordinator else None
        if dev is None:
            return self.async_abort(reason="not_loaded")
        key = str(dev.id)
        if user_input is not None:
            mappings = dict(self.config_entry.options.get(CONF_MAPPINGS, {}))
            if user_input["role"] == ROLE_NONE:
                mappings.pop(key, None)
            else:
                mappings[key] = dict(user_input)
            return self.async_create_entry(
                title="", data={**self.config_entry.options, CONF_MAPPINGS: mappings}
            )
        current = coordinator.mappings.get(key, {})
        schema = vol.Schema(
            {
                vol.Required("role", default=current.get("role", ROLE_NONE)): vol.In(
                    {
                        ROLE_NONE: "None",
                        ROLE_BINARY_SENSOR: "Binary sensor (on/off)",
                        ROLE_SENSOR: "Numeric sensor",
                    }
                ),
                vol.Required("index", default=current.get("index", 0)): vol.All(
                    vol.Coerce(int), vol.Range(min=0, max=63)
                ),
                vol.Required("active_value", default=current.get("active_value", 1)): vol.Coerce(int),
                vol.Required("device_class", default=current.get("device_class", "none")): vol.In(
                    BINARY_DEVICE_CLASSES
                ),
            }
        )
        return self.async_show_form(
            step_id="map_device",
            data_schema=schema,
            description_placeholders={
                "name": dev.name,
                "status": str(coordinator.status(dev.id)),
            },
        )

    # ---- estimated power -------------------------------------------------

    async def async_step_power_pick(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        coordinator = self._coordinator()
        if coordinator is None or not coordinator.devices:
            return self.async_abort(reason="not_loaded")
        if user_input is not None:
            self._dev_id = int(user_input["device"])
            return await self.async_step_power_device()
        configured = coordinator.power_settings
        choices = {}
        for dev in sorted(coordinator.devices.values(), key=lambda d: d.name.lower()):
            if dev.kind not in ("module", "group") or not can_estimate_power(dev.description):
                continue
            label = f"{dev.name} [{dev.type_name}]"
            power = configured.get(str(dev.id))
            if power:
                label += f" – {power.get('watts'):g} W"
            choices[str(dev.id)] = label
        if not choices:
            return self.async_abort(reason="no_power_devices")
        return self.async_show_form(
            step_id="power_pick",
            data_schema=vol.Schema({vol.Required("device"): vol.In(choices)}),
        )

    async def async_step_power_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        coordinator = self._coordinator()
        dev = coordinator.devices.get(self._dev_id) if coordinator else None
        if dev is None or not can_estimate_power(dev.description):
            return self.async_abort(reason="not_loaded")
        dimmable = dev.description.light is not None and dev.description.light.dim_index is not None
        if user_input is not None:
            power = dict(self.config_entry.options.get(CONF_POWER, {}))
            watts = float(user_input["watts"])
            if watts <= 0:
                power.pop(str(dev.id), None)
                self._remove_entities(coordinator, dev.id)
            else:
                power[str(dev.id)] = {
                    "watts": watts,
                    "scale": bool(user_input.get("scale", True)) and dimmable,
                }
            return self.async_create_entry(
                title="", data={**self.config_entry.options, CONF_POWER: power}
            )
        current = coordinator.power_settings.get(str(dev.id), {})
        fields: dict[Any, Any] = {
            vol.Required("watts", default=current.get("watts", 0)): vol.All(
                vol.Coerce(float), vol.Range(min=0, max=MAX_POWER_WATTS)
            )
        }
        if dimmable:
            fields[vol.Required("scale", default=current.get("scale", True))] = bool
        return self.async_show_form(
            step_id="power_device",
            data_schema=vol.Schema(fields),
            description_placeholders={"name": dev.name},
        )

    def _remove_entities(self, coordinator, dev_id: int) -> None:
        """Drop the estimated sensors of a device whose power was cleared."""
        registry = er.async_get(self.hass)
        for key in ("est_power", "est_energy"):
            unique_id = f"{coordinator.home_id}_{dev_id}_{key}"
            entity_id = registry.async_get_entity_id("sensor", DOMAIN, unique_id)
            if entity_id:
                registry.async_remove(entity_id)
