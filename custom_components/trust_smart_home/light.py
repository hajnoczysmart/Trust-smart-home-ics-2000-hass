"""Lights: 433 MHz dimmers/bulbs and Zigbee lights (brightness, colour, white)."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_EFFECT,
    ATTR_HS_COLOR,
    EFFECT_OFF,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .catalog import (
    EFFECT_COLORLOOP,
    EFFECT_DISCO,
    LightSpec,
    TrustDevice,
    brightness_to_level,
    decode_effect,
    decode_hs,
    effect_stop_value,
    effect_value,
    encode_hs,
    kelvin_to_mired,
    level_to_brightness,
    mired_to_kelvin,
)
from .const import DOMAIN, MAX_KELVIN, MIN_KELVIN
from .coordinator import TrustCoordinator
from .entity import TrustEntity

_LOGGER = logging.getLogger(__name__)

# ZigbeeLight colour pointer (index 10): which colour attribute was set last
POINTER_COLOR_TEMP = 9

# "police": red/blue flashing done by the integration (not a bulb effect).
# One colour command per light per step, so it is only offered when the hub is
# reached on the LAN; through the cloud it would be slow and flood the API.
EFFECT_POLICE = "police"
POLICE_INTERVAL = 1.0  # seconds per step
POLICE_HUES = (0, 240)  # red, blue


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: TrustCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        TrustLight(coordinator, dev)
        for dev in coordinator.devices.values()
        if dev.description.light is not None
    )


class TrustLight(TrustEntity, LightEntity):
    _attr_min_color_temp_kelvin = MIN_KELVIN
    _attr_max_color_temp_kelvin = MAX_KELVIN
    _attr_effect_list: list[str] | None = None  # as in LightEntity; set when effects exist

    def __init__(self, coordinator: TrustCoordinator, device: TrustDevice) -> None:
        super().__init__(coordinator, device)
        spec = device.description.light
        assert spec is not None
        self.spec: LightSpec = spec
        modes: set[ColorMode] = set()
        if spec.hs_index is not None:
            modes.add(ColorMode.HS)
        if spec.ct_index is not None:
            modes.add(ColorMode.COLOR_TEMP)
        if not modes:
            modes.add(ColorMode.BRIGHTNESS if spec.dim_index is not None else ColorMode.ONOFF)
        self._attr_supported_color_modes = modes
        self._police_task: asyncio.Task | None = None
        self._police_restore: tuple[int, int] | None = None  # (index, value)
        effects: list[str] = []
        if spec.effect_index is not None:
            effects += [EFFECT_COLORLOOP, EFFECT_DISCO]
        if spec.hs_index is not None and coordinator.local_address:
            effects.append(EFFECT_POLICE)
        if effects:
            self._attr_supported_features = LightEntityFeature.EFFECT
            self._attr_effect_list = [EFFECT_OFF, *effects]

    # ---- state -----------------------------------------------------------

    @property
    def is_on(self) -> bool | None:
        value = self.value(self.spec.on_index)
        return None if value is None else value == 1

    @property
    def brightness(self) -> int | None:
        level = self.value(self.spec.dim_index)
        if level is None or level <= 0:
            return None
        return level_to_brightness(level, self.spec)

    @property
    def color_mode(self) -> ColorMode:
        modes = self._attr_supported_color_modes or set()
        if ColorMode.HS in modes and ColorMode.COLOR_TEMP in modes:
            pointer = self.value(self.spec.pointer_index)
            return ColorMode.COLOR_TEMP if pointer == POINTER_COLOR_TEMP else ColorMode.HS
        return next(iter(modes))

    @property
    def hs_color(self) -> tuple[float, float] | None:
        raw = self.value(self.spec.hs_index)
        return decode_hs(raw) if raw is not None else None

    @property
    def color_temp_kelvin(self) -> int | None:
        mired = self.value(self.spec.ct_index)
        kelvin = mired_to_kelvin(mired) if mired is not None else None
        if kelvin is None:
            return None
        return max(MIN_KELVIN, min(MAX_KELVIN, kelvin))

    @property
    def effect(self) -> str | None:
        if self._police_task is not None:
            return EFFECT_POLICE
        if not self._attr_effect_list:
            return None
        if self.spec.effect_index is None:
            return EFFECT_OFF
        running = decode_effect(self.value(self.spec.effect_index))
        if running is None:
            return None
        return running or EFFECT_OFF

    # ---- commands --------------------------------------------------------

    async def async_turn_on(self, **kwargs: Any) -> None:
        spec = self.spec
        effect = kwargs.get(ATTR_EFFECT)
        if self._police_task is not None and effect != EFFECT_POLICE:
            # any other command ends the flashing; "off" also restores the colour
            colour = ATTR_HS_COLOR in kwargs or ATTR_COLOR_TEMP_KELVIN in kwargs
            await self._stop_police(restore=effect == EFFECT_OFF and not colour)
        attrs = [
            k
            for k in (ATTR_BRIGHTNESS, ATTR_HS_COLOR, ATTR_COLOR_TEMP_KELVIN, ATTR_EFFECT)
            if k in kwargs
        ]
        # Zigbee lights may ignore level/colour while off, so switch on first.
        if not attrs or not self.is_on or spec.dim_index is None:
            await self.send(spec.on_index, 1)
        if ATTR_HS_COLOR in kwargs and spec.hs_index is not None:
            hue, sat = kwargs[ATTR_HS_COLOR]
            await self.send(spec.hs_index, encode_hs(hue, sat))
        if ATTR_COLOR_TEMP_KELVIN in kwargs and spec.ct_index is not None:
            await self.send(spec.ct_index, kelvin_to_mired(kwargs[ATTR_COLOR_TEMP_KELVIN]))
        if ATTR_BRIGHTNESS in kwargs and spec.dim_index is not None:
            await self.send(spec.dim_index, brightness_to_level(kwargs[ATTR_BRIGHTNESS], spec))
        if effect == EFFECT_POLICE and EFFECT_POLICE in (self._attr_effect_list or []):
            if spec.effect_index is not None and decode_effect(self.value(spec.effect_index)):
                # a bulb effect would override the colours
                for running in (EFFECT_DISCO, EFFECT_COLORLOOP):
                    await self.send(spec.effect_index, effect_stop_value(running))
            self._start_police()
        elif effect is not None and spec.effect_index is not None:
            # The bulb keeps an effect running until that effect type is stopped,
            # so stop the other one first, and "off" stops both.
            if effect in (EFFECT_COLORLOOP, EFFECT_DISCO):
                other = EFFECT_DISCO if effect == EFFECT_COLORLOOP else EFFECT_COLORLOOP
                await self.send(spec.effect_index, effect_stop_value(other))
                await self.send(spec.effect_index, effect_value(effect))
            elif effect == EFFECT_OFF:
                for running in (EFFECT_DISCO, EFFECT_COLORLOOP):
                    await self.send(spec.effect_index, effect_stop_value(running))

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._stop_police(restore=False)
        await self.send(self.spec.on_index, 0)

    async def async_will_remove_from_hass(self) -> None:
        await self._stop_police(restore=False)
        await super().async_will_remove_from_hass()

    # ---- police effect ---------------------------------------------------

    def _police_targets(self) -> list[TrustDevice]:
        """A group flashes its members alternately; a single light alternates itself."""
        members = [
            dev
            for m in self.trust.members
            if (dev := self.coordinator.devices.get(m)) is not None
            and dev.description.light is not None
            and dev.description.light.hs_index is not None
        ]
        return members if len(members) >= 2 else [self.trust]

    def _start_police(self) -> None:
        if self._police_task is not None:
            return
        spec = self.spec
        pointer = self.value(spec.pointer_index)
        if pointer == POINTER_COLOR_TEMP and spec.ct_index is not None and self.value(spec.ct_index):
            self._police_restore = (spec.ct_index, self.value(spec.ct_index) or 0)
        elif spec.hs_index is not None and self.value(spec.hs_index) is not None:
            self._police_restore = (spec.hs_index, self.value(spec.hs_index) or 0)
        else:
            self._police_restore = None
        self._police_task = self.hass.async_create_background_task(
            self._police_loop(), f"trust_smart_home police {self.trust.id}"
        )

    async def _police_loop(self) -> None:
        targets = self._police_targets()
        hs_index = self.spec.hs_index
        assert hs_index is not None
        step = 0
        try:
            while True:
                for i, dev in enumerate(targets):
                    hue = POLICE_HUES[(i + step) % 2]
                    await self.coordinator.async_send(dev, hs_index, encode_hs(hue, 100))
                step += 1
                await asyncio.sleep(POLICE_INTERVAL)
        except HomeAssistantError as err:
            _LOGGER.warning("Police effect on %s stopped: %s", self.trust.name, err)
            self._police_task = None
            self.async_write_ha_state()

    async def _stop_police(self, restore: bool) -> None:
        task, self._police_task = self._police_task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        if restore and self._police_restore is not None:
            index, value = self._police_restore
            await self.send(index, value)
        self._police_restore = None
