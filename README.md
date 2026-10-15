# Trust Smart Home (ICS-2000) – Home Assistant integration

Control the devices, groups and scenes you added in the Trust Smart Home app (via the
ICS-2000 hub) from Home Assistant. Supports 433 MHz KlikAanKlikUit/Trust, Zigbee,
Somfy RTS and Brel devices, the 868 MHz security system devices, the ICS-2000 alarm system and the P1 energy meter. No
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

| Device | Home Assistant entity |
|---|---|
| 433 MHz switch, plug | `switch` |
| 433 MHz dimmer, bulb, rotary dimmer | `light`, brightness in 15 steps |
| Zigbee switch, plug, built-in switch | `switch` |
| Zigbee dimmer, built-in dimmer, plug dimmer | `light`, brightness |
| Zigbee bulb: RGB, tunable white, generic | `light`: brightness, colour (HS), colour temperature – whatever the bulb advertises |
| Zigbee light group | `light` for the group; its state is derived from the members (on if any member is on) |
| 433 MHz motor/actuator | `cover`: open / close / stop |
| 433 MHz actuator with slider | `cover`: open / close / position |
| Somfy RTS, Brel | `cover`: open / close / stop ("my") |
| 433 MHz motion and contact sensor, dusk sensor | `binary_sensor` |
| 433 MHz doorbell button | `binary_sensor` (pressed), `button` to ring the paired chime |
| 433 MHz chime | `button` (ring) |
| 433 MHz remotes and wall switches | per channel: `binary_sensor` (last button pressed), `event` on button press, `button` to transmit as the remote |
| Security system remote, 868 MHz | `event` (arm / disarm), low battery |
| Security system devices, 868 MHz: contact, motion, smoke, siren | `binary_sensor` + tamper, low battery, connectivity; siren: *Test signal* button and *Sound alarm* button (disabled by default) |
| Zigbee sensors: multi-purpose, leakage, smoke, CO, FireAngel, temperature/humidity | `binary_sensor` (alarm according to the zone type, tamper, occupancy) and `sensor` (temperature, humidity, battery) |
| ICS-2000 alarm system | `alarm_control_panel`: arm, disarm, state (arming / armed / triggered) |
| P1 energy module | `sensor`: current consumption/production (W), energy consumed/produced (kWh, low/high tariff), gas and water (m³), tariff |
| Scenes from the app | `scene` (activate) |
| Rules (automations) from the app | `switch` to enable/disable, `button` to run now; on a shared *Rules* device |
| ICS-2000 hub LED (experimental) | `select`: on / off / disco, on an *ICS-2000* device |

Zigbee devices that report themselves offline are shown as unavailable. Mains-powered Zigbee devices (bulbs, plugs, built-in
switches and dimmers) have an *Identify* button: the device blinks for about 3 seconds,
which shows the hub can reach it. Battery-powered Zigbee devices (sensors, lock, remote)
sleep most of the time and ignore it, so they have no such button.

## Testing alarms and the doorbell

- **Doorbell button:** *Ring* makes the hub send the button's code, so the paired chime rings.
- **Security siren (868 MHz):** *Test signal* sends the short "arm error" signal. *Sound alarm* triggers
  the real alarm; the app has no command to stop it, so the siren sounds for its configured
  duration. It is disabled by default (enable it in the entity settings if you need it).
- **Smoke detectors:** use the test button on the detector itself (the app cannot trigger a
  test either). The detector sounds, and within a few seconds its *Smoke* sensor in Home
  Assistant turns on, which tests the whole chain.

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

**Limitation:** the hub does not push updates; the integration polls (every 3 seconds by default). Pressing
the same button twice, or on and off quickly on the same channel, cannot be seen, and an
event may arrive up to one polling interval late. Changes caused by the integration's own buttons are
not reported as button presses.

## Rules from the app

Rules (automations) created in the Trust Smart Home app appear on a *Rules* device:

- a **switch** per rule enables or disables it, like the toggle in the app;
- a **Run** button executes the rule's actions immediately.

Internal rules of the security system are hidden, as in the app.

## Hub LED (experimental)

The *ICS-2000 → Hub LED* select changes the hub's LED setting (on, off or disco), the
same setting as in the app's settings. Unlike device commands, this is a settings message
that has to carry the hub's current data versions, so it works best with the hub's IP
address configured: the integration then reads the current setting and the versions from
the hub first. Without an IP address it is sent through the cloud and the last value set
from Home Assistant is shown. If the hub refuses the change, an error is shown; with
debug logging enabled, the hub's replies are logged.

## Settings

Configure → *Settings*: the status polling interval (1–60 seconds, default 3). Lower values
make state changes and button-press events show up faster, at the cost of more requests to
the Trust cloud.

## Diagnostics

Settings → Devices & services → Trust Smart Home → ⋮ → *Download diagnostics* gives a file
with the device list, device types and current status values, useful for reporting
problems or new device types. E-mail, password, hub key, MAC, home ID and hub address are
removed.

## Extra sensor for any device

Configure → *Extra sensor for a device* → pick a device → expose one position of its raw status list as a binary or
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

## Security

- **Credentials:** the Trust cloud API needs your e-mail and password with every request, so
  they are stored in Home Assistant's config entry (like other cloud integrations). They are
  only sent to `https://trustsmartcloud2.com` over HTTPS with certificate verification.
  Error messages and logs are scrubbed, so the password, e-mail, hub MAC and commands never
  appear in them. Consider using a strong, unique password for your Trust account.
- **Local control:** commands to the hub on your LAN (UDP 2012) are encrypted with your
  home's AES key, exactly like the app does. The hub address is validated (IP address or
  host name only).
- **Data from the cloud** (hub key and MAC, device lists, status values) is validated before
  use; malformed entries are skipped instead of breaking the integration.
- No other services are contacted, and no external Python packages are installed.

## License and credits

The communication part of `api.py` is based on
[ics_2000_python](https://github.com/zefanjajobse/ics_2000_python) (Apache License 2.0);
the rest is original code.
