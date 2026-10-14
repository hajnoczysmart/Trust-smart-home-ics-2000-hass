# Trust Smart Home (ICS-2000) – Home Assistant integration

Control the devices, groups and scenes you added in the Trust Smart Home app (via the
ICS-2000 hub) from Home Assistant. Supports 433 MHz KlikAanKlikUit/Trust, Zigbee,
Somfy RTS and Brel devices, the ICS-2000 alarm system and the P1 energy meter. No
external Python packages are required.

The device types, status positions and command values come from the official
Trust Smart Home Android app (`com.trust.smarthome.commons.models.devices`).
The cloud communication and the command frame are based on
[ics_2000_python](https://github.com/zefanjajobse/ics_2000_python) (Apache-2.0) and are
byte-identical to it.

## Installation

1. HACS → ⋮ (top right) → Custom repositories →
   `https://github.com/hajnoczysmart/Trust-smart-home-ics-2000-hass`, type: *Integration*.
2. Find *Trust Smart Home (ICS-2000)* in HACS → Download → restart Home Assistant.
3. Settings → Devices & services → Add integration → *Trust Smart Home*.
4. Enter the e-mail address and password of your Trust Smart Home app account. If you also
   enter the hub's IP address, commands are sent over your local network (UDP 2012), falling
   back to the cloud automatically.

**Upgrading from v0.x:** the settings are compatible, but the entity IDs changed. The
cleanest way is to delete the old integration entry and add it again.

## Supported devices

| Device (type ID) | Home Assistant entity |
|---|---|
| 433 MHz switch, plug (1) | `switch` |
| 433 MHz dimmer, bulb, rotary dimmer (2, 24, 57) | `light`, brightness in 15 steps |
| Zigbee switch, plug, built-in switch (33, 41, 53) | `switch` |
| Zigbee dimmer, built-in dimmer, plug dimmer (34, 54, 59) | `light`, brightness |
| Zigbee bulb: RGB, tunable white, generic (35, 36, 40) | `light`: brightness, colour (HS), colour temperature – whatever the bulb advertises |
| Zigbee light group (48) | `light` for the group; its state is derived from the members (on if any member is on) |
| 433 MHz motor/actuator (3) | `cover`: open / close / stop |
| 433 MHz actuator with slider (56, 58) | `cover`: open / close / position |
| Somfy RTS (23), Brel (27) | `cover`: open / close / stop ("my") |
| 433 MHz motion and contact sensor (4, 5, 28), dusk sensor (14) | `binary_sensor` |
| 433 MHz doorbell button (6, 21, 50) | `binary_sensor` (pressed), `button` to ring the paired chime |
| 433 MHz chime (13) | `button` (ring) |
| 433 MHz remotes and wall switches (7–12, 15, 22, 25, 26, 29–31, 55) | per channel: `binary_sensor` (last button pressed), `event` on button press, `button` to transmit as the remote |
| ARC alarm remote (16) | `event` (arm / disarm), low battery |
| ARC alarm devices: contact, motion, smoke, siren (17–20) | `binary_sensor` + tamper, low battery, connectivity; siren: *Test signal* button and *Sound alarm* button (disabled by default) |
| Zigbee sensors: multi-purpose, leakage, smoke, CO, FireAngel, temperature/humidity (37, 42, 43, 46, 47, 49) | `binary_sensor` (alarm according to the zone type, tamper, occupancy) and `sensor` (temperature, humidity, battery) |
| ICS-2000 alarm system (240) | `alarm_control_panel`: arm, disarm, state (arming / armed / triggered) |
| P1 energy module (238) | `sensor`: current consumption/production (W), energy consumed/produced (kWh, low/high tariff), gas and water (m³), tariff |
| Scenes from the app | `scene` (activate) |

Zigbee devices that report themselves offline are shown as unavailable. Every Zigbee device
has an *Identify* button: the device blinks for about 3 seconds, which shows the hub can
reach it.

## Testing alarms and the doorbell

- **Doorbell button:** *Ring* makes the hub send the button's code, so the paired chime rings.
- **ARC siren:** *Test signal* sends the short "arm error" signal. *Sound alarm* triggers
  the real alarm; the app has no command to stop it, so the siren sounds for its configured
  duration. It is disabled by default (enable it in the entity settings if you need it).
- **Smoke detectors:** a real alarm test is only possible with the button on the detector
  itself; the app cannot trigger it either. Zigbee smoke detectors can be checked with
  *Identify*.

The cloud often keeps returning the old value for a while after a command (especially
for groups and their members). The integration therefore only takes over a value from the
cloud when it actually changes: a stale, unchanged value never overwrites what you just
set, while changes made elsewhere (for example in the app) still show up.

Every device also has a *Raw status* diagnostic sensor showing its full status list. It is
enabled by default only for devices without dedicated support (lock, Zigbee Light Link
remote, Forest, Motion Blinds).

## Remotes and wall switches

For each channel the hub stores which button was pressed last (on = 1, off = 0).
This gives three things:

- **"Channel N" binary sensor:** on if the "on" button was pressed last.
- **"Button press" event:** event type `channel_<N>_on` or `channel_<N>_off`. In automations,
  use an *Event entity* trigger, or watch the state change of its `event_type` attribute.
- **"Channel N on/off" buttons:** the hub transmits as the remote, so 433 MHz devices that
  were paired directly with the remote can be switched too. For the 16-channel remote these
  are disabled by default (enable them in the entity settings).

**Limitation:** the hub does not push updates; the integration polls every 5 seconds. Pressing
the same button twice, or on and off quickly on the same channel, cannot be seen, and an
event may arrive up to 5 seconds late. Changes caused by the integration's own buttons are
not reported as button presses.

## Extra sensor for any device

Configure → pick a device → expose one position of its raw status list as a binary or
numeric sensor. Trigger the device, reopen the dialog, and you will see which number changed.

## Not supported

- **Lock, Zigbee Light Link remote, Forest and Motion Blinds:** how to control them cannot
  be determined reliably from the app, so only their raw status is shown (they can be
  exposed with the extra sensor feature).
- **XY colour mode:** the colour of Zigbee bulbs that only advertise XY colour cannot be set
  (brightness and white can). The app's own bulbs also advertise HS.
- **Alarm "home" mode:** the ICS-2000 alarm has a single armed state, shown in Home Assistant
  as "armed away".
- **Wi-Fi cameras and Wi-Fi devices:** they do not use the ICS-2000 protocol.
- **New devices:** the device list is read at startup. After adding a device in the app,
  reload the integration.

## Testing

Tested with Home Assistant on a real ICS-2000 hub. Not every device type listed above has
been tried on hardware yet; reports for other devices are welcome.

In addition, automated checks verify that:

- the command frame and encryption are byte-identical to the working `ics_2000_python`
  library in 200 random cases;
- the device catalogue returns the app's values for every device family;
- every Home Assistant name and method used exists in the Home Assistant 2026.11 source;
- the platform logic (which commands are sent, how state is read, when button presses and
  group states are reported) runs against stubbed Home Assistant classes.

If something does not work, enable debug logging and include the relevant lines in an issue:

```yaml
logger:
  logs:
    custom_components.trust_smart_home: debug
```

The user interface is in English.

## License and credits

The communication part of `api.py` is based on
[ics_2000_python](https://github.com/zefanjajobse/ics_2000_python) (Apache License 2.0);
the rest is original code.
