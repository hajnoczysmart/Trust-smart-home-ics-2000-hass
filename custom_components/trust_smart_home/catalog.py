"""What each Trust device type is and how its status list is laid out.

Every value here comes from the official Trust Smart Home Android app
(device classes under com.trust.smarthome.commons.models.devices): the
DeviceType ids, the function indices (INDEX_*) and their values (VALUE_*).

Pure Python, no Home Assistant imports, so it can be unit tested.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Device type ids (com.trust.smarthome.commons.models.devices.DeviceType)
# ---------------------------------------------------------------------------

DEVICE_TYPE_NAMES: dict[int, str] = {
    0: "Unknown",
    1: "Switch (433 MHz)",
    2: "Dimmer (433 MHz)",
    3: "Actuator (433 MHz)",
    4: "Motion sensor (433 MHz)",
    5: "Contact sensor (433 MHz)",
    6: "Doorbell ACDB-7000A",
    7: "Wall control 1 channel",
    8: "Wall control 2 channel",
    9: "Remote control 1 channel",
    10: "Remote control 2 channel",
    11: "Remote control 3 channel",
    12: "Remote control 16 channel",
    13: "Chime",
    14: "Dusk sensor",
    15: "Remote control AYCT-202",
    16: "ARC remote",
    17: "ARC contact sensor",
    18: "ARC motion sensor",
    19: "ARC smoke sensor",
    20: "ARC siren",
    21: "Doorbell ACDB-7000B",
    22: "AWMT built-in wall switch",
    23: "Somfy actuator",
    24: "Light bulb (433 MHz)",
    25: "Wall switch AGST-8800",
    26: "Wall switch AGST-8802",
    27: "Brel actuator",
    28: "Contact sensor 2 (433 MHz)",
    29: "ARC keychain remote",
    30: "ARC action button",
    31: "ARC rotary dimmer",
    32: "Zigbee unknown device",
    33: "Zigbee switch",
    34: "Zigbee dimmer",
    35: "Zigbee RGB light",
    36: "Zigbee tunable white light",
    37: "Zigbee multi-purpose sensor",
    38: "Zigbee lock",
    39: "Zigbee Light Link remote",
    40: "Zigbee light",
    41: "Zigbee socket",
    42: "Zigbee leakage sensor",
    43: "Zigbee smoke sensor",
    46: "Zigbee temperature and humidity sensor",
    47: "Zigbee carbon monoxide sensor",
    48: "Zigbee light group",
    49: "Zigbee FireAngel sensor",
    50: "Doorbell ACDB-8000A",
    51: "Forest Shuttle",
    52: "Forest 5121",
    53: "Zigbee built-in switch",
    54: "Zigbee built-in dimmer",
    55: "Remote control AYCT-303",
    56: "Actuator with slider (433 MHz)",
    57: "Rotary dimmer with Matter",
    58: "Actuator with Matter",
    59: "Zigbee socket dimmer",
    65: "Motion Blinds lift",
    66: "Motion Blinds tilt and lift",
    67: "Motion Blinds tilt",
    238: "Energy module (P1)",
    240: "Security module",
    241: "Camera module",
    242: "Location module",
    243: "System module",
    244: "Weather module",
}

# Families
RF_SWITCH = {1}
RF_DIMMER = {2, 24, 57}  # Light and RotaryDimmerWithMatter extend Dimmer
RF_ACTUATOR = {3}
RF_ACTUATOR_SLIDER = {56, 58}
RF_MOTION = {4}
RF_CONTACT = {5, 28}
RF_DUSK = {14}
RF_DOORBELL = {6, 21, 50}
RF_CHIME = {13}
SOMFY = {23}
BREL = {27}
ARC_CONTACT = {17}
ARC_MOTION = {18}
ARC_SMOKE = {19}
ARC_SIREN = {20}
# Transmitters (IRemoteControl): channel i is status index i, 1 = on/pressed,
# 0 = off/released. Channel counts from RemoteControl/WallSwitch.channels.
REMOTE_CHANNELS = {
    9: 1, 10: 2, 11: 4, 15: 5, 55: 5, 12: 17, 29: 1,  # RemoteControl
    7: 1, 8: 2, 30: 1, 22: 2, 25: 1, 26: 2, 31: 1,  # WallSwitch
}
ARC_REMOTE = {16}  # security remote: index 0 = 38 arm / 39 disarm, index 1 battery
ZB_SWITCH = {33, 41, 53}
ZB_DIMMER = {34, 54, 59}
ZB_LIGHT = {35, 36, 40, 48}
ZB_SENSOR = {37, 42, 43, 46, 47, 49}
ZIGBEE_TYPES = {32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 46, 47, 48, 49, 53, 54, 59}
SECURITY_MODULE = 240
ENERGY_MODULE = 238
# Virtual modules without anything useful to expose.
IGNORED_TYPES = {241, 242, 243, 244}
VIRTUAL_TYPES = {238, 240, 241, 242, 243, 244}

# ZigbeeDevice.getFunctionMask(): fixed masks for some types, else info[12..13]
_ZB_FIXED_FUNCTIONS = {
    35: set(range(0, 13)),  # createZigbeeRgbBitMask
    36: {0, 1, 2, 3, 4, 9, 10},  # createZigbeeTunableBitMask
    34: {0, 1, 2, 3, 4},  # createZigbeeDimmerBitMask
    54: {0, 1, 2, 3, 4},
    59: {0, 1, 2, 3, 4},
    33: {0, 1, 2, 3},  # createZigbeeSwitchBitMask
    53: {0, 1, 2, 3},
}

# ZigbeeSensor.IasZoneType -> Home Assistant binary sensor device class
IAS_ZONE_CLASSES = {
    13: "motion",
    21: "opening",
    40: "smoke",
    42: "moisture",
    43: "carbon_monoxide",
}
_ZB_SENSOR_DEFAULT_CLASS = {42: "moisture", 43: "smoke", 47: "carbon_monoxide", 49: "smoke"}

# Security module (SecurityModule / SecurityState)
ALARM_STATE_INDEX = 0
ALARM_COMMAND_INDEX = 15
ALARM_ARM = 38
ALARM_DISARM = 39
SECURITY_STATES = {
    0: "undefined",
    1: "disarmed",
    2: "pre_walkout",
    3: "walkout",
    4: "armed",
    5: "arm_error",
    6: "alarm_activated",
}

# Scene playback (Scene.INDEX_PLAYBACK / VALUE_PLAY)
SCENE_PLAYBACK_INDEX = 0
SCENE_PLAY = 1

# Brel: toValue(LONG_PRESS=2, channel 1) = (1 << 16) | 2
BREL_PRESS = (1 << 16) | 2


# ---------------------------------------------------------------------------
# Specs: what an entity needs to read/write
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LightSpec:
    on_index: int
    dim_index: int | None = None
    dim_min: int = 1
    dim_max: int = 254
    ct_index: int | None = None  # mireds
    hs_index: int | None = None
    pointer_index: int | None = None


@dataclass(frozen=True)
class SwitchSpec:
    index: int


@dataclass(frozen=True)
class CoverSpec:
    open: tuple[int, int]
    close: tuple[int, int]
    stop: tuple[int, int] | None = None
    position_index: int | None = None
    position_max: int = 15


@dataclass(frozen=True)
class BinarySpec:
    key: str
    index: int
    on_values: tuple[int, ...]
    device_class: str | None = None
    diagnostic: bool = False
    channel: int | None = None  # remote channel number (1-based), for naming


@dataclass(frozen=True)
class SensorSpec:
    key: str
    index: int
    kind: str  # temperature, humidity, battery, power, energy, gas, water, tariff


@dataclass(frozen=True)
class ButtonSpec:
    key: str
    index: int
    value: int
    channel: int | None = None
    enabled_default: bool = True
    device_class: str | None = None  # e.g. "identify"
    action: str = "control"  # "control": entity action; "identify": hub identify message


@dataclass(frozen=True)
class EventSpec:
    """Fires an event when a watched status position changes.

    The event type is the name for the new value, prefixed with
    'channel_<n>_' when several channels are watched.
    """

    key: str
    indexes: tuple[int, ...]
    values: tuple[tuple[int, str], ...]  # (raw value, event name)
    device_class: str | None = "button"

    def event_type(self, index: int, raw: int) -> str | None:
        name = dict(self.values).get(raw)
        if name is None:
            return None
        if len(self.indexes) == 1:
            return name
        return f"channel_{index + 1}_{name}"

    @property
    def event_types(self) -> list[str]:
        return [
            self.event_type(i, raw)  # type: ignore[misc]
            for i in self.indexes
            for raw, _ in self.values
        ]


@dataclass
class Description:
    light: LightSpec | None = None
    switch: SwitchSpec | None = None
    cover: CoverSpec | None = None
    alarm: bool = False
    scene: bool = False
    binary_sensors: list[BinarySpec] = field(default_factory=list)
    sensors: list[SensorSpec] = field(default_factory=list)
    buttons: list[ButtonSpec] = field(default_factory=list)
    events: list[EventSpec] = field(default_factory=list)
    online_index: int | None = None
    supported: bool = True


# ---------------------------------------------------------------------------
# Raw entity from the sync call
# ---------------------------------------------------------------------------


@dataclass
class TrustDevice:
    id: int
    kind: str  # module, group or scene
    name: str
    device_type: int | None
    info: list[int]
    hidden: bool
    description: Description = field(default_factory=Description)
    members: list[int] = field(default_factory=list)  # group member module ids

    @property
    def type_name(self) -> str:
        if self.kind == "scene":
            return "Scene"
        if self.device_type is None:
            return "Group" if self.kind == "group" else "Unknown"
        return DEVICE_TYPE_NAMES.get(self.device_type, f"Type {self.device_type}")

    @property
    def is_paired(self) -> bool:
        return any(int(v or 0) for v in self.info)

    def info_word(self, low: int) -> int:
        """Little-endian 16-bit value from info[low], info[low + 1]."""
        try:
            return (int(self.info[low]) & 255) | ((int(self.info[low + 1]) & 255) << 8)
        except (IndexError, TypeError, ValueError):
            return 0

    @property
    def zigbee_functions(self) -> set[int]:
        """ZigbeeDevice.getFunctions(): function i exists if bit i of (mask<<1)|1."""
        if self.device_type in _ZB_FIXED_FUNCTIONS:
            return set(_ZB_FIXED_FUNCTIONS[self.device_type])
        mask = (self.info_word(12) << 1) | 1
        return {i for i in range(17) if (mask >> i) & 1}

    @property
    def ias_zone_type(self) -> int:
        return self.info_word(14)

    @property
    def short_id(self) -> int:
        """ZigbeeDevice.getShortId(): the device's Zigbee network address."""
        return self.info_word(0)


def parse_sync(rows: list[dict[str, Any]]) -> list[TrustDevice]:
    """Turn decrypted sync rows into devices, groups and scenes.

    The data comes from the cloud, so malformed rows are skipped instead of
    breaking the whole setup.
    """
    out: list[TrustDevice] = []
    for row in rows:
        try:
            dev = _parse_row(row)
        except (TypeError, ValueError, AttributeError):
            dev = None
        if dev is not None:
            out.append(dev)
    return out


def _small_int(value: Any) -> int:
    v = int(value)
    if not 0 <= v < 2**32:
        raise ValueError("out of range")
    return v


def _parse_row(row: dict[str, Any]) -> TrustDevice | None:
    data = row.get("data") or {}
    if not isinstance(data, dict):
        return None
    for kind in ("module", "group", "scene"):
        body = data.get(kind)
        if isinstance(body, dict):
            break
    else:
        return None
    smd = body.get("smd_info")
    smd = smd if isinstance(smd, dict) else {}
    info = body.get("info")
    try:
        device_type: int | None = int(body["device"])
    except (KeyError, TypeError, ValueError):
        device_type = None
    dev = TrustDevice(
        id=_small_int(row.get("id", body.get("id", 0))),
        kind=kind,
        name=str(body.get("name") or f"{kind.title()} {row.get('id')}")[:200],
        device_type=device_type,
        info=[int(v) & 255 for v in info[:16]] if isinstance(info, list) else [0] * 16,
        hidden=bool(smd.get("hidden", False)),
    )
    if kind == "group" and isinstance(body.get("modules"), list):
        dev.members = [
            int(m) for m in body["modules"] if str(m).isdigit() and 0 <= int(m) < 2**32
        ]
    # The app only shows paired modules; virtual modules are always paired.
    if kind == "module" and not dev.is_paired and dev.device_type not in VIRTUAL_TYPES:
        return None
    if kind == "scene" and dev.hidden:
        return None  # internal scenes, e.g. the security walkout scene
    if dev.device_type in IGNORED_TYPES:
        return None
    dev.description = describe(dev)
    return dev


# ---------------------------------------------------------------------------
# describe(): device -> entities
# ---------------------------------------------------------------------------


def _zigbee_light(dev: TrustDevice) -> LightSpec:
    fns = dev.zigbee_functions
    if fns <= {0, 1, 2}:  # empty mask (e.g. a group) -> assume dimmable
        fns = {0, 1, 2, 3, 4}
    return LightSpec(
        on_index=3,
        dim_index=4 if 4 in fns else None,
        dim_min=1,
        dim_max=254,
        ct_index=9 if 9 in fns else None,
        hs_index=8 if 8 in fns else None,
        pointer_index=10 if 10 in fns else None,
    )


def _zigbee_sensor(dev: TrustDevice, desc: Description) -> None:
    fns = dev.zigbee_functions
    t = dev.device_type
    if fns <= {0, 1, 2}:  # no mask advertised: fall back to the type
        fns = {0, 1, 2, 3, 5, 7} if t != 46 else {0, 1, 2, 3, 4, 11}
    if t == 49:
        alarm_class = "smoke"  # FireAngel
    else:
        alarm_class = IAS_ZONE_CLASSES.get(dev.ias_zone_type) or _ZB_SENSOR_DEFAULT_CLASS.get(t)
    if 5 in fns and alarm_class:
        desc.binary_sensors.append(BinarySpec("alarm", 5, (1,), alarm_class))
    if 10 in fns:
        desc.binary_sensors.append(BinarySpec("occupancy", 10, (1,), "occupancy"))
    if 7 in fns:
        desc.binary_sensors.append(BinarySpec("tamper", 7, (1,), "tamper", diagnostic=True))
    if 4 in fns:
        desc.sensors.append(SensorSpec("temperature", 4, "temperature"))
    if 11 in fns:
        desc.sensors.append(SensorSpec("humidity", 11, "humidity"))
    if 3 in fns:
        desc.sensors.append(SensorSpec("battery", 3, "battery"))


def _remote(channels: int, desc: Description) -> None:
    """Per channel: last-pressed state, a press event and send-as-remote buttons."""
    many = channels > 5  # e.g. the 16-channel remote: buttons off by default
    for i in range(channels):
        n = i + 1
        desc.binary_sensors.append(BinarySpec(f"channel_{n}", i, (1,), None, channel=n))
        desc.buttons += [
            ButtonSpec(f"channel_{n}_on", i, 1, channel=n, enabled_default=not many),
            ButtonSpec(f"channel_{n}_off", i, 0, channel=n, enabled_default=not many),
        ]
    desc.events.append(EventSpec("buttons", tuple(range(channels)), ((1, "on"), (0, "off"))))


def describe(dev: TrustDevice) -> Description:
    d = Description()
    t = dev.device_type

    if dev.kind == "scene":
        d.scene = True
        return d

    if t in ZIGBEE_TYPES and dev.kind == "module":
        d.online_index = 0  # ZigbeeDevice.INDEX_NETWORK_CONNECTION

    if t in RF_SWITCH:
        d.switch = SwitchSpec(0)
    elif t in RF_DIMMER:
        d.light = LightSpec(on_index=0, dim_index=1, dim_min=1, dim_max=15)
    elif t in RF_ACTUATOR:
        d.cover = CoverSpec(open=(0, 1), close=(0, 0), stop=(1, 1))
    elif t in RF_ACTUATOR_SLIDER:
        d.cover = CoverSpec(open=(0, 1), close=(0, 0), position_index=1, position_max=15)
    elif t in SOMFY:
        d.cover = CoverSpec(open=(0, 1), close=(2, 1), stop=(1, 1))
    elif t in BREL:
        d.cover = CoverSpec(open=(0, BREL_PRESS), close=(2, BREL_PRESS), stop=(1, BREL_PRESS))
    elif t in RF_MOTION:
        d.binary_sensors.append(BinarySpec("motion", 0, (1,), "motion"))
    elif t in RF_CONTACT:
        d.binary_sensors.append(BinarySpec("opening", 0, (1,), "opening"))
    elif t in RF_DUSK:
        d.binary_sensors.append(BinarySpec("dusk", 0, (1,), None))
    elif t in RF_DOORBELL:
        d.binary_sensors.append(BinarySpec("pressed", 0, (1,), None))
        # Doorbell transmitter press(0): the hub sends the button's code, the chime rings
        d.buttons.append(ButtonSpec("ring", 0, 1))
    elif t in RF_CHIME:
        d.buttons.append(ButtonSpec("ring", 0, 1))
    elif t in ARC_CONTACT:
        d.binary_sensors += [
            BinarySpec("opening", 1, (22,), "opening"),
            BinarySpec("battery_low", 2, (0,), "battery", diagnostic=True),
            BinarySpec("connectivity", 3, (1,), "connectivity", diagnostic=True),
        ]
    elif t in ARC_MOTION:
        d.binary_sensors += [
            BinarySpec("motion", 1, (22,), "motion"),
            BinarySpec("tamper", 2, (25,), "tamper", diagnostic=True),
            BinarySpec("battery_low", 3, (0,), "battery", diagnostic=True),
            BinarySpec("connectivity", 4, (1,), "connectivity", diagnostic=True),
        ]
    elif t in ARC_SMOKE:
        d.binary_sensors += [
            BinarySpec("alarm", 1, (21,), "smoke"),
            BinarySpec("battery_low", 2, (0,), "battery", diagnostic=True),
            BinarySpec("connectivity", 3, (1,), "connectivity", diagnostic=True),
        ]
    elif t in ARC_SIREN:
        d.binary_sensors += [
            BinarySpec("siren_active", 3, (36,), "safety"),
            BinarySpec("tamper", 4, (25,), "tamper", diagnostic=True),
            BinarySpec("battery_low", 5, (0,), "battery", diagnostic=True),
            BinarySpec("connectivity", 6, (1,), "connectivity", diagnostic=True),
        ]
        d.buttons += [
            # Siren.armError(): index 2 = 35, the short "arm error" signal
            ButtonSpec("siren_test", 2, 35),
            # Siren.activateAlarm(): index 3 = 36, the real alarm; the app has no
            # stop action, so it is disabled by default
            ButtonSpec("siren_alarm", 3, 36, enabled_default=False),
        ]
    elif t in REMOTE_CHANNELS:
        _remote(REMOTE_CHANNELS[t], d)
    elif t in ARC_REMOTE:
        d.events.append(EventSpec("arm_disarm", (0,), ((38, "arm"), (39, "disarm"))))
        d.binary_sensors.append(BinarySpec("battery_low", 1, (0,), "battery", diagnostic=True))
    elif t in ZB_SWITCH:
        d.switch = SwitchSpec(3)
    elif t in ZB_DIMMER:
        d.light = LightSpec(on_index=3, dim_index=4, dim_min=1, dim_max=254)
    elif t in ZB_LIGHT:
        d.light = _zigbee_light(dev)
    elif t in ZB_SENSOR:
        _zigbee_sensor(dev, d)
    elif t == SECURITY_MODULE:
        d.alarm = True
    elif t == ENERGY_MODULE:
        d.sensors += [
            SensorSpec("power_consumed", 4, "power"),
            SensorSpec("power_produced", 5, "power"),
            SensorSpec("energy_consumed_low", 0, "energy"),
            SensorSpec("energy_consumed_high", 1, "energy"),
            SensorSpec("energy_produced_low", 2, "energy"),
            SensorSpec("energy_produced_high", 3, "energy"),
            SensorSpec("gas_consumed", 6, "gas"),
            SensorSpec("water_consumed", 7, "water"),
            SensorSpec("tariff", 11, "tariff"),
        ]
    else:
        d.supported = False
    if t in ZIGBEE_TYPES and dev.kind == "module" and dev.short_id:
        # The app identifies with a separate hub message (MessageFactory.identify:
        # type IDENTIFY_ZIGBEE, {"short id", "duration": 3}), not an entity action.
        d.buttons.append(ButtonSpec("identify", 0, 3, device_class="identify", action="identify"))
    return d


# ---------------------------------------------------------------------------
# Value conversions
# ---------------------------------------------------------------------------


def status_value(status: list[int] | None, index: int | None) -> int | None:
    if status is None or index is None:
        return None
    try:
        return int(status[index])
    except (IndexError, TypeError, ValueError):
        return None


def group_state_indexes(desc: Description) -> tuple[int | None, list[int]]:
    """(on/off index, other indexes) a group derives from its members."""
    if desc.light is not None:
        spec = desc.light
        others = [i for i in (spec.dim_index, spec.hs_index, spec.ct_index, spec.pointer_index) if i is not None]
        return spec.on_index, others
    if desc.switch is not None:
        return desc.switch.index, []
    return None, []


def derive_group_status(
    own: list[int] | None, members: list[list[int] | None], desc: Description
) -> list[int] | None:
    """Group state computed from its members, like the app does.

    The hub does not reliably update a group's own status, but it does update
    the members it switched. On = any member on; level = average of the members
    that are on; colour values from the first member that is on.
    """
    on_index, others = group_state_indexes(desc)
    statuses = [m for m in members if m]
    if on_index is None or not statuses:
        return own
    result = list(own or [])
    needed = max([on_index, *others]) + 1
    if len(result) < needed:
        result.extend([0] * (needed - len(result)))
    known = [v for v in (status_value(m, on_index) for m in statuses) if v is not None]
    if not known:
        return own
    result[on_index] = 1 if any(v == 1 for v in known) else 0
    lit = [m for m in statuses if status_value(m, on_index) == 1] or statuses
    light = desc.light
    for index in others:
        values = [v for v in (status_value(m, index) for m in lit) if v is not None]
        if not values:
            continue
        if light is not None and index == light.dim_index:
            positive = [v for v in values if v > 0] or values
            result[index] = round(sum(positive) / len(positive))
        else:
            result[index] = values[0]
    return result


def brightness_to_level(brightness: int, spec: LightSpec) -> int:
    """HA 0-255 -> device dim level (1-15 for 433 MHz, 1-254 for Zigbee)."""
    level = round(brightness / 255 * spec.dim_max)
    return max(spec.dim_min, min(spec.dim_max, level))


def level_to_brightness(level: int, spec: LightSpec) -> int:
    return max(0, min(255, round(level / spec.dim_max * 255)))


def kelvin_to_mired(kelvin: int) -> int:
    return max(0, min(600, round(1_000_000 / max(1, kelvin))))


def mired_to_kelvin(mired: int) -> int | None:
    return round(1_000_000 / mired) if mired and mired > 0 else None


def encode_hs(hue: float, saturation: float) -> int:
    """HueSaturationValue.toValue(); saturation 0-100 as in Home Assistant."""
    h = int((max(0.0, min(360.0, hue)) / 360.0) * 254.0)
    s = int((max(0.0, min(100.0, saturation)) / 100.0) * 254.0)
    return (h << 8) | s


def decode_hs(value: int) -> tuple[float, float]:
    h = ((value >> 8) & 255) * 360.0 / 254.0
    s = (value & 255) / 254.0 * 100.0
    return (round(min(360.0, h), 1), round(min(100.0, s), 1))


def position_to_level(position: int, spec: CoverSpec) -> int:
    return max(1, min(spec.position_max, round(position / 100 * spec.position_max)))


def level_to_position(level: int, spec: CoverSpec) -> int:
    return max(0, min(100, round(level / spec.position_max * 100)))


def sensor_value(kind: str, raw: int | None) -> float | int | str | None:
    """Scale a raw status value the way the app displays it."""
    if raw is None:
        return None
    if kind == "temperature":  # signed 16-bit, 1/100 °C
        v = raw & 0xFFFF
        if v >= 0x8000:
            v -= 0x10000
        return round(v / 100, 2)
    if kind == "humidity":  # 1/100 %
        return round(raw / 100, 1)
    if kind == "battery":  # 0..200 half-percent; 0 means full in the app
        return 100 if raw == 0 else max(0, min(100, round(raw / 2)))
    if kind == "power":  # W
        return raw
    if kind in ("energy", "gas", "water"):  # Wh, dm³, l -> kWh, m³
        return round(raw / 1000, 3)
    if kind == "tariff":
        return {1: "low", 2: "high"}.get(raw)
    return raw
