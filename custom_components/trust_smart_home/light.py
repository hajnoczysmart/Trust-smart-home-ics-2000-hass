"""Lights: 433 MHz dimmers/bulbs and Zigbee lights (brightness, colour, white)."""
from __future__ import annotations

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

# ZigbeeLight colour pointer (index 10): which colour attribute was set last
POINTER_COLOR_TEMP = 9


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
        if spec.effect_index is not None:
            self._attr_supported_features = LightEntityFeature.EFFECT
            self._attr_effect_list = [EFFECT_OFF, EFFECT_COLORLOOP, EFFECT_DISCO]

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
        if self.spec.effect_index is None:
            return None
        running = decode_effect(self.value(self.spec.effect_index))
        if running is None:
            return None
        return running or EFFECT_OFF

    # ---- commands --------------------------------------------------------

    async def async_turn_on(self, **kwargs: Any) -> None:
        spec = self.spec
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
        if ATTR_EFFECT in kwargs and spec.effect_index is not None:
            effect = kwargs[ATTR_EFFECT]
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
        await self.send(self.spec.on_index, 0)
