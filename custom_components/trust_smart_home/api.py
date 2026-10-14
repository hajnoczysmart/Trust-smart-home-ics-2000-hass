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
import re
import socket
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
MSG_IDENTIFY_ZIGBEE = 137
ENTITY_KINDS = ("module", "group", "scene")


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
    mac_hex: str, entity_id: int, encrypted: bytes, msg_type: int = MSG_CONTROL_ENTITY
) -> bytes:
    """43-byte header followed by the encrypted payload."""
    header = bytearray(43)
    header[0] = 1  # frame
    header[2] = msg_type
    mac = bytes.fromhex(mac_hex.replace(":", "").replace("-", ""))[:6]
    header[3 : 3 + len(mac)] = mac
    header[9:13] = FRAME_MAGIC.to_bytes(4, "little")
    header[29:33] = int(entity_id).to_bytes(4, "little")
    header[41:43] = len(encrypted).to_bytes(2, "little")
    return bytes(header) + encrypted


def build_command(
    mac_hex: str, aes_key_hex: str, kind: str, entity_id: int, function: int, value: int
) -> bytes:
    payload = json.dumps(command_payload(kind, entity_id, function, value))
    return build_frame(mac_hex, entity_id, encrypt(payload, aes_key_hex))


def build_identify(mac_hex: str, aes_key_hex: str, short_id: int, duration: int = 3) -> bytes:
    """MessageFactory.identify(): the hub tells a Zigbee device to identify itself."""
    payload = json.dumps({"short id": int(short_id), "duration": int(duration)}, separators=(",", ":"))
    return build_frame(mac_hex, 0, encrypt(payload, aes_key_hex), MSG_IDENTIFY_ZIGBEE)


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
        frame = build_command(
            self.mac or "", self.aes_key or "", kind, entity_id, function, int(value)
        )
        self._deliver(frame, local_address)

    def identify(self, short_id: int, local_address: str | None = None) -> None:
        """Make a Zigbee device blink (as the app's 'identify' does)."""
        self._deliver(build_identify(self.mac or "", self.aes_key or "", short_id), local_address)

    def _deliver(self, frame: bytes, local_address: str | None) -> None:
        if local_address:
            try:
                self._send_local(frame, local_address)
                return
            except OSError as err:
                _LOGGER.debug("Local send to %s failed (%s), using cloud", local_address, err)
        self._send_cloud(frame)

    def _send_local(self, frame: bytes, host: str) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(3)
        try:
            sock.sendto(frame, (host, LOCAL_PORT))
            sock.recvfrom(1024)  # acknowledgement; its content is not used
        finally:
            sock.close()

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
