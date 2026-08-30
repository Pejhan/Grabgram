from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import string
from typing import Any
from urllib.parse import unquote, urlparse


DEFAULT_MTPROTO_SECRET = "0" * 32


def decode_proxy_secret(value: str) -> bytes:
    """Decode Telegram's hexadecimal, Base64, or URL-safe Base64 secret forms."""
    text = value.strip()
    if not text:
        return bytes(16)
    decoded: bytes | None = None
    if len(text) % 2 == 0 and all(character in string.hexdigits for character in text):
        decoded = bytes.fromhex(text)
    else:
        try:
            encoded = text.encode("ascii")
            encoded += b"=" * (-len(encoded) % 4)
            decoded = base64.b64decode(encoded, altchars=b"-_", validate=True)
        except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
            raise ValueError("The MTProto proxy secret must be hexadecimal or Base64 encoded.") from exc
    if len(decoded) == 16:
        return decoded
    if len(decoded) == 17 and decoded[0] == 0xDD:
        return decoded
    if len(decoded) > 17 and decoded[0] == 0xEE:
        return decoded
    raise ValueError(
        "The decoded proxy secret must be 16 bytes, a 17-byte dd secret, "
        "or an ee Fake-TLS secret containing a domain."
    )


def canonical_proxy_secret(value: str) -> str:
    """Return a representation Telethon parses consistently without losing Base64 prefixes."""
    return decode_proxy_secret(value).hex()


def proxy_secret_kind(value: str) -> str:
    decoded = decode_proxy_secret(value)
    if decoded[0] == 0xEE and len(decoded) > 17:
        return "fake_tls"
    if decoded[0] == 0xDD and len(decoded) == 17:
        return "random_padding"
    return "standard"


@dataclass(frozen=True)
class MtProtoProxy:
    enabled: bool = False
    host: str = ""
    port: int = 443
    secret: str = ""

    def validated(self) -> "MtProtoProxy":
        host = self.host.strip()
        raw_secret = self.secret.strip()
        secret = canonical_proxy_secret(raw_secret) if raw_secret else ""
        if not self.enabled:
            return MtProtoProxy(False, host, int(self.port), secret)
        if not host:
            raise ValueError("Enter the MTProto proxy server.")
        if not 1 <= int(self.port) <= 65535:
            raise ValueError("The proxy port must be between 1 and 65535.")
        return MtProtoProxy(True, host, int(self.port), secret)

    @property
    def effective_secret(self) -> str:
        return self.secret or DEFAULT_MTPROTO_SECRET


def parse_proxy_link(value: str) -> MtProtoProxy:
    text = value.strip()
    parsed = urlparse(text)
    is_tg = parsed.scheme.lower() == "tg" and parsed.netloc.lower() == "proxy"
    is_web = parsed.scheme.lower() in {"http", "https"} and parsed.netloc.lower() in {
        "t.me", "www.t.me", "telegram.me", "www.telegram.me",
    } and parsed.path.rstrip("/").lower() == "/proxy"
    if not (is_tg or is_web):
        raise ValueError("Paste a tg://proxy or https://t.me/proxy link.")
    # Telegram proxy links may contain unescaped '+' characters in Base64
    # secrets. Unlike form-query parsing, '+' must remain a literal plus.
    values: dict[str, list[str]] = {}
    for field in parsed.query.split("&"):
        key, separator, item = field.partition("=")
        if separator:
            values.setdefault(unquote(key), []).append(unquote(item))
    try:
        host = values["server"][0]
        port = int(values["port"][0])
    except (KeyError, IndexError, ValueError) as exc:
        raise ValueError("The proxy link must include a server and numeric port.") from exc
    return MtProtoProxy(True, host, port, values.get("secret", [""])[0]).validated()


def load_mtproto_proxy(db: Any) -> MtProtoProxy:
    return MtProtoProxy(
        enabled=bool(db.setting_int("mtproto_proxy_enabled", 0)),
        host=db.setting_text("mtproto_proxy_host", ""),
        port=db.setting_int("mtproto_proxy_port", 443),
        secret=db.setting_text("mtproto_proxy_secret", ""),
    ).validated()


def save_mtproto_proxy(db: Any, proxy: MtProtoProxy) -> None:
    proxy = proxy.validated()
    db.set_setting_int("mtproto_proxy_enabled", int(proxy.enabled))
    db.set_setting_text("mtproto_proxy_host", proxy.host)
    db.set_setting_int("mtproto_proxy_port", proxy.port)
    db.set_setting_text("mtproto_proxy_secret", proxy.secret)


def ensure_telethon_compatible(proxy: MtProtoProxy) -> None:
    proxy = proxy.validated()
    if proxy.enabled and proxy_secret_kind(proxy.effective_secret) == "fake_tls":
        raise ValueError(
            "This proxy uses an ee/Fake-TLS secret. Telegram's official clients support that "
            "transport, but Telethon 1.x does not implement its TLS handshake. Use a standard "
            "16-byte or dd MTProto proxy secret with this application."
        )


def telethon_client_options(proxy: MtProtoProxy, connection_module: Any) -> dict[str, Any]:
    proxy = proxy.validated()
    if not proxy.enabled:
        return {}
    ensure_telethon_compatible(proxy)
    return {
        "connection": connection_module.ConnectionTcpMTProxyRandomizedIntermediate,
        "proxy": (proxy.host, proxy.port, proxy.effective_secret),
    }
