"""Minimal client for the Trust Smart Home / KlikAanKlikUit ICS-2000 cloud.

The request formats, AES scheme and the 43-byte command frame are adapted
from ics_2000_python (Apache-2.0, https://github.com/zefanjajobse/ics_2000_python)
and match what the official Trust Smart Home app sends:
an action is {"module"|"group"|"scene": {"id", "function", "value"}}, and
group actions also carry "update_group_members": true.

Everything here is blocking; call it from an executor.
"""
from __future__ import annotations

import base64
import json
import logging
import random
import re
import socket
import time
from typing import Any

import requests
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

_LOGGER = logging.getLogger(__name__)

API_URL = "https://trustsmartcloud2.com/ics2000_api"
LOCAL_PORT = 2012
FRAME_MAGIC = 653213
# MessageType values from the app
MSG_CONTROL_ENTITY = 128
MSG_ANNOUNCEMENT = 3  # the app's LAN discovery broadcast
MSG_GET_DEVICE_INFO = 16
MSG_REMOTE_REBOOT = 180
MSG_UPDATE_USER_SETTINGS = 17
MSG_GET_USER_SETTINGS = 18
MSG_GET_ENTITY_STATUS = 132  # not used by the app itself (it reads statuses from the cloud)
MSG_ACKNOWLEDGE = 252
MSG_NOT_ACKNOWLEDGE = 253
BUSY_ERRORS = (17, 18, 19, 20)  # MessageError BUSY, BUSY_SYNCING, BUSY_PAIRING, BUSY_SCANNING
BUSY_RETRIES = 20  # as the app's local transport
LOCAL_STATUS_BUDGET = 2.0  # seconds per poll for statuses asked from the hub
VERSIONS_LEN = 16  # header bytes 13-28: global, settings, ... scene data versions
LED_STATES = ("on", "off", "disco")  # Settings "led" (LedsState)
# Hub settings (Settings / pj4 JSON names)
SETTING_FW_UPDATE_DISABLED = "disable_fw_update_check"  # 1 = no automatic firmware updates
SETTING_ZIGBEE_CHANNEL = "zigbee_channel"
SETTING_LOCAL_ONLY = "local_communication_only"  # app: "force local communication"
ENTITY_KINDS = ("module", "group", "scene", "scenario")  # scenario = app rule


class TrustApiError(Exception):
    """Generic communication error."""


class TrustAuthError(TrustApiError):
    """Wrong e-mail or password."""


# The cloud API takes the credentials as request parameters, so a failed
# request's error text (which contains the URL) can include them. Never let
# that reach the log or the UI.
_SECRET_PARAMS = re.compile(
    r"((?:password_hash|password|email|mac|command|aes_key)=)[^&\s'\")]*", re.IGNORECASE
)


def redact(text: str) -> str:
    """Remove credentials and other request parameters from a message."""
    return _SECRET_PARAMS.sub(r"\1***", text)


def _request_error(endpoint: str, err: Exception) -> TrustApiError:
    return TrustApiError(f"{endpoint}: {type(err).__name__}: {redact(str(err))}")


def _as_int(value: Any) -> int:
    """Status values from the cloud are integers; tolerate odd encodings."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _is_hex(value: Any, lengths: tuple[int, ...]) -> bool:
    return isinstance(value, str) and len(value) in lengths and re.fullmatch(r"[0-9a-fA-F]+", value) is not None


# --------------------------------------------------------------------------
# Crypto and framing (pure functions, unit tested)
# --------------------------------------------------------------------------


def _cipher(aes_key_hex: str, iv: bytes) -> Cipher:
    return Cipher(algorithms.AES(bytes.fromhex(aes_key_hex)), modes.CBC(iv))


def encrypt(plaintext: str, aes_key_hex: str) -> bytes:
    """AES-CBC with a zero IV and PKCS#7 padding; returns IV + ciphertext."""
    iv = bytes(16)
    padder = padding.PKCS7(128).padder()
    data = padder.update(plaintext.encode("utf-8")) + padder.finalize()
    enc = _cipher(aes_key_hex, iv).encryptor()
    return iv + enc.update(data) + enc.finalize()


def decrypt(b64: str, aes_key_hex: str) -> str:
    """Inverse of the cloud's encryption: base64(IV + ciphertext)."""
    raw = base64.b64decode(b64)
    dec = _cipher(aes_key_hex, raw[:16]).decryptor()
    data = dec.update(raw[16:]) + dec.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    return (unpadder.update(data) + unpadder.finalize()).decode("utf-8")


def command_payload(kind: str, entity_id: int, function: int, value: int) -> dict:
    """JSON body of a single action, as the app serialises it."""
    if kind not in ENTITY_KINDS:
        raise ValueError(f"Unknown entity kind {kind!r}")
    body: dict[str, Any] = {"id": entity_id, "function": function, "value": value}
    if kind == "group":
        body["update_group_members"] = True
    return {kind: body}


def build_frame(
    mac_hex: str,
    entity_id: int,
    encrypted: bytes,
    msg_type: int = MSG_CONTROL_ENTITY,
    versions: bytes | None = None,
) -> bytes:
    """43-byte header followed by the encrypted payload.

    versions: the hub's data versions (header bytes 13-28), only needed for
    settings messages; the hub returns them in every reply.
    """
    header = bytearray(43)
    header[0] = 1  # frame
    header[2] = msg_type
    mac = bytes.fromhex(mac_hex.replace(":", "").replace("-", ""))[:6]
    header[3 : 3 + len(mac)] = mac
    header[9:13] = FRAME_MAGIC.to_bytes(4, "little")
    if versions is not None and len(versions) == VERSIONS_LEN:
        header[13 : 13 + VERSIONS_LEN] = versions
    header[29:33] = int(entity_id).to_bytes(4, "little")
    header[41:43] = len(encrypted).to_bytes(2, "little")
    return bytes(header) + encrypted


def build_command(
    mac_hex: str, aes_key_hex: str, kind: str, entity_id: int, function: int, value: int
) -> bytes:
    payload = json.dumps(command_payload(kind, entity_id, function, value))
    return build_frame(mac_hex, entity_id, encrypt(payload, aes_key_hex))


def build_settings_message(
    mac_hex: str, aes_key_hex: str, msg_type: int, data: dict, versions: bytes | None = None
) -> bytes:
    """GET_USER_SETTINGS / UPDATE_USER_SETTINGS frame (no entity)."""
    payload = json.dumps(data, separators=(",", ":"))
    return build_frame(mac_hex, 0, encrypt(payload, aes_key_hex), msg_type, versions)


def parse_reply(raw: bytes, aes_key_hex: str) -> tuple[int, bytes, str | None, int | None]:
    """(message type, data versions, decrypted data or None, NAK error code)."""
    if len(raw) < 43:
        raise ValueError("short reply")
    msg_type = raw[2]
    versions = bytes(raw[13 : 13 + VERSIONS_LEN])
    length = int.from_bytes(raw[41:43], "little")
    data = raw[43 : 43 + length]
    if msg_type == MSG_NOT_ACKNOWLEDGE:
        return msg_type, versions, None, data[0] if data else None
    text = None
    if len(data) > 16:
        dec = _cipher(aes_key_hex, bytes(data[:16])).decryptor()
        plain = dec.update(bytes(data[16:])) + dec.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        try:
            text = (unpadder.update(plain) + unpadder.finalize()).decode("utf-8", "replace")
        except ValueError:
            text = plain.decode("utf-8", "replace")
    return msg_type, versions, text, None


def _loads(text: str | None) -> Any:
    if not text:
        return None
    try:
        return json.loads(text.strip("\x00 \r\n"))
    except ValueError:
        return None


def _find_key(data: Any, key: str, depth: int = 0) -> Any:
    """First value of `key` in nested JSON (the reply layout is not documented)."""
    if depth > 5:
        return None
    if isinstance(data, dict):
        if key in data:
            return data[key]
        values = data.values()
    elif isinstance(data, list):
        values = data
    else:
        return None
    for value in values:
        found = _find_key(value, key, depth + 1)
        if found is not None:
            return found
    return None



# --------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------


class TrustClient:
    """Stateless HTTP client; credentials are sent with every request."""

    def __init__(self, email: str, password: str, timeout: float = 15) -> None:
        self.email = email
        self.password = password
        self.timeout = timeout
        self.session = requests.Session()
        self.homes: list[dict[str, Any]] = []
        self.aes_key: str | None = None
        self.mac: str | None = None
        self.home_id: str | None = None
        self.home_name: str | None = None
        self._versions: bytes | None = None  # hub data versions from the last reply

    def close(self) -> None:
        """Release the HTTP connection pool."""
        self.session.close()

    # ---- helpers ---------------------------------------------------------

    def _post(self, endpoint: str, data: dict[str, Any]) -> Any:
        try:
            resp = self.session.post(
                f"{API_URL}/{endpoint}", data=data, timeout=self.timeout
            )
        except requests.RequestException as err:
            raise _request_error(endpoint, err) from err
        if resp.status_code == 401:
            raise TrustAuthError("Incorrect e-mail or password")
        if resp.status_code != 200:
            raise TrustApiError(f"{endpoint}: HTTP {resp.status_code}")
        try:
            return resp.json()
        except ValueError as err:
            raise TrustApiError(f"{endpoint}: invalid JSON") from err

    def _auth(self) -> dict[str, str]:
        if self.aes_key is None or self.mac is None:
            raise TrustApiError("No home selected")
        return {
            "email": self.email,
            "mac": self.mac,
            "password_hash": self.password,
            "home_id": "",
        }

    # ---- account ---------------------------------------------------------

    def login(self) -> dict[str, str]:
        """Authenticate; returns {home_id: home_name}."""
        data = self._post(
            "account.php",
            {
                "action": "login",
                "email": self.email,
                "password_hash": self.password,
                "device_unique_id": "android",
                "platform": "",
                "mac": "",
            },
        )
        if not isinstance(data, dict):
            raise TrustAuthError("Unexpected login response")
        self.homes = list(data.get("homes") or [])
        return {str(h.get("home_id")): str(h.get("home_name")) for h in self.homes}

    def select_home(self, home_id: str) -> None:
        for home in self.homes:
            if str(home.get("home_id")) == str(home_id):
                aes_key = str(home.get("aes_key") or "")
                mac = str(home.get("mac") or "").replace(":", "").replace("-", "")
                if not _is_hex(aes_key, (32, 48, 64)) or not _is_hex(mac, (12,)):
                    raise TrustApiError("The cloud returned invalid hub data for this home")
                self.aes_key = aes_key
                self.mac = mac
                self.home_id = str(home.get("home_id"))
                self.home_name = home.get("home_name")
                return
        raise TrustApiError(f"Home {home_id} not found on this account")

    # ---- data ------------------------------------------------------------

    def sync(self) -> list[dict[str, Any]]:
        """All entities of the home with their decrypted 'data' object."""
        raw = self._post("gateway.php", {"action": "sync", **self._auth()})
        if not isinstance(raw, list):
            raise TrustApiError("Unexpected sync response")
        out: list[dict[str, Any]] = []
        for item in raw:
            try:
                data = json.loads(decrypt(item.get("data", ""), self.aes_key or ""))
            except Exception as err:  # noqa: BLE001 - skip undecodable rows
                _LOGGER.debug("Skipping entity %s: %s", item.get("id"), err)
                continue
            try:
                entity_id = int(item.get("id"))
            except (TypeError, ValueError):
                continue
            if not isinstance(data, dict) or not 0 <= entity_id < 2**32:
                continue
            out.append({"id": entity_id, "data": data})
        return out

    def get_statuses(self, entity_ids: list[int]) -> dict[int, list[int]]:
        """Current function values per entity id."""
        if not entity_ids:
            return {}
        raw = self._post(
            "entity.php",
            {
                "action": "get-multiple",
                **self._auth(),
                "entity_id": json.dumps([int(i) for i in entity_ids]),
            },
        )
        if not isinstance(raw, list):
            raise TrustApiError("Unexpected status response")
        out: dict[int, list[int]] = {}
        for item in raw:
            status = item.get("status")
            if not status:
                continue
            try:
                decoded = json.loads(decrypt(status, self.aes_key or ""))
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Undecodable status for %s: %s", item.get("id"), err)
                continue
            if not isinstance(decoded, dict):
                continue
            try:
                entity_id = int(item["id"])
            except (KeyError, TypeError, ValueError):
                continue
            for kind in ENTITY_KINDS:
                if isinstance(decoded.get(kind), dict):
                    functions = decoded[kind].get("functions") or []
                    if isinstance(functions, list):
                        out[entity_id] = [_as_int(v) for v in functions[:256]]
                    break
        return out

    # ---- commands --------------------------------------------------------

    def send(
        self,
        kind: str,
        entity_id: int,
        function: int,
        value: int,
        local_address: str | None = None,
    ) -> None:
        """Send one action; tries the hub on the LAN first when configured."""
        _LOGGER.debug("Command: %s %s function %s = %s", kind, entity_id, function, value)
        frame = build_command(
            self.mac or "", self.aes_key or "", kind, entity_id, function, int(value)
        )
        self._deliver(frame, local_address)

    def _deliver(self, frame: bytes, local_address: str | None) -> None:
        if local_address:
            try:
                # Like the app: when the hub answers "busy" (e.g. it is still
                # handling the previous command), wait a moment and send again.
                reply = self._exchange(frame, local_address)
            except OSError as err:
                _LOGGER.debug("Local send to %s failed (%s), using cloud", local_address, err)
            else:
                if len(reply) > 43 and reply[2] == MSG_NOT_ACKNOWLEDGE:
                    if reply[43] in BUSY_ERRORS:
                        _LOGGER.debug("Hub stayed busy, sending through the cloud")
                        self._send_cloud(frame)
                        return
                    _LOGGER.debug("Hub refused the command (error %s)", reply[43])
                else:
                    _LOGGER.debug("Sent on the LAN, reply type %s", reply[2] if len(reply) > 2 else None)
                return
        self._send_cloud(frame)

    def _exchange(self, frame: bytes, host: str, expect_data: bool = False) -> bytes:
        """Send a message on the LAN the way the app does and return the reply.

        - "busy" (NAK 17-20): wait 40-60 ms and send again, up to 20 times;
        - a request is first acknowledged, then answered in a second packet.
        """
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(2)
        try:
            reply = b""
            for _attempt in range(BUSY_RETRIES + 1):
                sock.sendto(frame, (host, LOCAL_PORT))
                reply, _addr = sock.recvfrom(8192)
                if len(reply) > 43 and reply[2] == MSG_NOT_ACKNOWLEDGE and reply[43] in BUSY_ERRORS:
                    _LOGGER.debug("Hub busy (error %s), retrying", reply[43])
                    time.sleep(random.uniform(0.04, 0.06))
                    continue
                break
            if expect_data and len(reply) >= 3 and reply[2] == MSG_ACKNOWLEDGE:
                try:
                    reply, _addr = sock.recvfrom(8192)  # the actual answer
                except socket.timeout:
                    pass  # only an acknowledgement came
            return reply
        finally:
            sock.close()

    # ---- finding the hub on the LAN ------------------------------------------

    def discover(self, timeout: float = 3.0) -> str | None:
        """The hub's IP address, found like the app does it.

        The app broadcasts an ANNOUNCEMENT (frame 254, {"SID": random}) for its
        hub's MAC to port 2012; the hub answers, and the answer's source is
        its address. The MAC comes from the Trust account, so nothing has to be
        entered. Returns None when no hub answers (e.g. Home Assistant runs in
        a container without host networking).
        """
        if not self.mac or not self.aes_key:
            return None
        mac = bytes.fromhex(self.mac.replace(":", "").replace("-", ""))[:6]
        payload = encrypt(json.dumps({"SID": format(random.getrandbits(31), "x")}), self.aes_key)
        frame = bytearray(build_frame(self.mac, 0, payload, MSG_ANNOUNCEMENT))
        frame[0] = 254  # frame number used by the app for announcements
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.settimeout(0.5)
            deadline = time.monotonic() + timeout
            sock.sendto(bytes(frame), ("255.255.255.255", LOCAL_PORT))
            while time.monotonic() < deadline:
                try:
                    reply, addr = sock.recvfrom(8192)
                except socket.timeout:
                    continue
                if len(reply) >= 9 and reply[3:9] == mac and addr[1] == LOCAL_PORT:
                    return str(addr[0])
        except OSError as err:
            _LOGGER.debug("Hub discovery failed: %s", err)
        finally:
            sock.close()
        return None

    # ---- statuses from the hub on the LAN (experimental) -------------------

    def get_status_local(self, kind: str, entity_id: int, local_address: str) -> list[int] | None:
        """One entity's function values asked from the hub (None if not answered).

        The app never does this, so the request layout is a guess modelled on
        the control message; the answer is expected in the cloud's status
        layout ({"module": {"functions": [...]}}).
        """
        frame = build_frame(
            self.mac or "",
            entity_id,
            encrypt(json.dumps({kind: {"id": entity_id}}), self.aes_key or ""),
            MSG_GET_ENTITY_STATUS,
        )
        try:
            raw = self._exchange(frame, local_address, expect_data=True)
            msg_type, _versions, text, _code = parse_reply(raw, self.aes_key or "")
        except (OSError, ValueError):
            return None
        if msg_type == MSG_NOT_ACKNOWLEDGE:
            return None
        functions = _find_key(_loads(text), "functions")
        if not isinstance(functions, list):
            return None
        return [_as_int(v) for v in functions[:256]]

    def get_statuses_local(
        self, entities: list[tuple[str, int]], local_address: str
    ) -> dict[int, list[int]]:
        """Statuses from the hub; empty when the hub does not answer the first one."""
        out: dict[int, list[int]] = {}
        deadline = time.monotonic() + LOCAL_STATUS_BUDGET
        for n, (kind, entity_id) in enumerate(entities):
            if time.monotonic() > deadline:
                break  # the rest comes from the cloud this time
            status = self.get_status_local(kind, entity_id, local_address)
            if status is None:
                if n == 0:
                    return {}
                continue
            out[entity_id] = status
        return out

    def probe_local_status(self, kind: str, entity_id: int, local_address: str) -> list[dict]:
        """Raw replies to a few status-request layouts, for the diagnostics file."""
        variants = [
            ("kind_id", {kind: {"id": entity_id}}),
            ("id", {"id": entity_id}),
            ("empty", {}),
        ]
        results: list[dict] = []
        for name, data in variants:
            frame = build_frame(
                self.mac or "", entity_id, encrypt(json.dumps(data), self.aes_key or ""),
                MSG_GET_ENTITY_STATUS,
            )
            try:
                raw = self._exchange(frame, local_address, expect_data=True)
                msg_type, _v, text, code = parse_reply(raw, self.aes_key or "")
                results.append(
                    {
                        "request": name,
                        "reply_type": msg_type,
                        "error": code,
                        "segment": raw[1] if len(raw) > 1 else None,
                        "data": (text or "")[:1500],
                    }
                )
            except (OSError, ValueError) as err:
                results.append({"request": name, "failed": f"{type(err).__name__}: {err}"})
        return results

    # ---- hub settings, information and restart --------------------------------

    def get_setting(self, key: str, local_address: str | None) -> Any:
        """One hub setting, asked on the LAN like the app (None if unknown).

        The reply also carries the hub's data versions, which a settings
        update must repeat.
        """
        if not local_address:
            return None
        frame = build_settings_message(
            self.mac or "", self.aes_key or "", MSG_GET_USER_SETTINGS, {"user_setting": key}
        )
        try:
            raw = self._exchange(frame, local_address, expect_data=True)
            msg_type, versions, text, code = parse_reply(raw, self.aes_key or "")
        except (OSError, ValueError) as err:
            _LOGGER.debug("Reading the hub setting %s failed: %s", key, err)
            return None
        _LOGGER.debug("Hub setting %s: reply type %s, error %s", key, msg_type, code)
        if msg_type == MSG_NOT_ACKNOWLEDGE:
            return None
        self._versions = versions
        return _find_key(_loads(text), key)

    def set_setting(self, data: dict[str, Any], local_address: str | None) -> None:
        """Change hub settings (UPDATE_USER_SETTINGS with only these keys, like the app)."""
        if local_address:
            self.get_setting(next(iter(data)), local_address)  # refreshes the data versions
            frame = build_settings_message(
                self.mac or "", self.aes_key or "", MSG_UPDATE_USER_SETTINGS, data, self._versions
            )
            try:
                raw = self._exchange(frame, local_address)
            except OSError as err:
                _LOGGER.debug("Local settings update failed (%s), using cloud", err)
            else:
                try:
                    msg_type, versions, _text, code = parse_reply(raw, self.aes_key or "")
                except ValueError:
                    return
                _LOGGER.debug("Settings update reply: type %s, error %s", msg_type, code)
                if msg_type == MSG_NOT_ACKNOWLEDGE:
                    if code in BUSY_ERRORS:
                        raise TrustApiError(f"The hub stayed busy, try again later (error {code})")
                    raise TrustApiError(f"The hub refused the setting (error {code})")
                self._versions = versions
                return
        frame = build_settings_message(
            self.mac or "", self.aes_key or "", MSG_UPDATE_USER_SETTINGS, data, self._versions
        )
        self._send_cloud(frame)

    def get_led(self, local_address: str | None) -> str | None:
        value = self.get_setting("led", local_address)
        return value if value in LED_STATES else None

    def set_led(self, state: str, local_address: str | None) -> None:
        if state not in LED_STATES:
            raise ValueError(f"Unknown LED state {state!r}")
        self.set_setting({"led": state}, local_address)

    def get_device_info(self, local_address: str | None) -> dict[str, Any] | None:
        """GET_DEVICE_INFO: firmware/hardware/Zigbee versions (LAN only)."""
        if not local_address:
            return None
        frame = build_frame(self.mac or "", 0, b"", MSG_GET_DEVICE_INFO)
        try:
            raw = self._exchange(frame, local_address, expect_data=True)
            msg_type, _versions, text, code = parse_reply(raw, self.aes_key or "")
        except (OSError, ValueError) as err:
            _LOGGER.debug("Reading the hub information failed: %s", err)
            return None
        _LOGGER.debug("Hub information: reply type %s, error %s", msg_type, code)
        data = _loads(text)
        return data if isinstance(data, dict) else None

    def reboot(self, local_address: str | None) -> None:
        """Restart the hub (REMOTE_REBOOT, as in the app's settings)."""
        data = {"reboot": "requested", "magic": random.randint(0, 2**31 - 1)}
        frame = build_settings_message(self.mac or "", self.aes_key or "", MSG_REMOTE_REBOOT, data)
        if local_address:
            try:
                raw = self._exchange(frame, local_address)
            except OSError as err:
                _LOGGER.debug("Local restart request failed (%s), using cloud", err)
            else:
                if len(raw) > 43 and raw[2] == MSG_NOT_ACKNOWLEDGE:
                    raise TrustApiError(f"The hub refused the restart (error {raw[43]})")
                return
        self._send_cloud(frame)

    def _send_cloud(self, frame: bytes) -> None:
        if self.mac is None:
            raise TrustApiError("No home selected")
        try:
            resp = self.session.get(
                f"{API_URL}/command.php",
                params={
                    "action": "add",
                    "email": self.email,
                    "mac": self.mac,
                    "password_hash": self.password,
                    "device_unique_id": "",
                    "command": frame.hex(),
                },
                timeout=self.timeout,
            )
        except requests.RequestException as err:
            raise _request_error("command.php", err) from err
        if resp.status_code == 401:
            raise TrustAuthError("Incorrect e-mail or password")
        if resp.status_code != 200:
            raise TrustApiError(f"command.php: HTTP {resp.status_code}")
