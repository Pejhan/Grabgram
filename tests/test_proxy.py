from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tg_downloader.database import Database
from tg_downloader.proxy import (
    DEFAULT_MTPROTO_SECRET, MtProtoProxy, canonical_proxy_secret, load_mtproto_proxy,
    parse_proxy_link, proxy_secret_kind, save_mtproto_proxy, telethon_client_options,
)


class MtProtoProxyTests(unittest.TestCase):
    def test_parses_telegram_and_web_links(self) -> None:
        secret = "dd" + "ab" * 16
        for link in (
            f"tg://proxy?server=proxy.example.com&port=443&secret={secret}",
            f"https://t.me/proxy?server=proxy.example.com&port=443&secret={secret}",
        ):
            proxy = parse_proxy_link(link)
            self.assertTrue(proxy.enabled)
            self.assertEqual("proxy.example.com", proxy.host)
            self.assertEqual(443, proxy.port)
            self.assertEqual(secret, proxy.secret)

    def test_rejects_invalid_link_and_secret(self) -> None:
        with self.assertRaises(ValueError):
            parse_proxy_link("https://example.com/proxy?server=x&port=443")
        with self.assertRaises(ValueError):
            MtProtoProxy(True, "proxy.example.com", 443, "not-hex").validated()

    def test_accepts_standard_and_urlsafe_base64_secrets(self) -> None:
        self.assertEqual("ab" * 16, canonical_proxy_secret("q6urq6urq6urq6urq6urqw=="))
        self.assertEqual("fb" * 16, canonical_proxy_secret("-_v7-_v7-_v7-_v7-_v7-w"))

    def test_proxy_link_preserves_base64_plus_character(self) -> None:
        proxy = parse_proxy_link(
            "tg://proxy?server=proxy.example.com&port=443&secret=+/v7+/v7+/v7+/v7+/v7+w=="
        )
        self.assertEqual("fb" * 16, proxy.secret)

    def test_base64_dd_secret_is_canonicalized_with_prefix(self) -> None:
        proxy = MtProtoProxy(True, "proxy.example.com", 443, "3aurq6urq6urq6urq6urq6s=").validated()
        self.assertEqual("dd" + "ab" * 16, proxy.secret)
        self.assertEqual("random_padding", proxy_secret_kind(proxy.secret))

    def test_fake_tls_secret_gets_explicit_compatibility_error(self) -> None:
        secret = "ee" + "ab" * 16 + "example.com".encode().hex()
        proxy = MtProtoProxy(True, "proxy.example.com", 443, secret)
        connection = SimpleNamespace(ConnectionTcpMTProxyRandomizedIntermediate=object())
        self.assertEqual("fake_tls", proxy_secret_kind(secret))
        with self.assertRaisesRegex(ValueError, "Fake-TLS"):
            telethon_client_options(proxy, connection)

    def test_empty_secret_uses_telethon_default(self) -> None:
        marker = object()
        connection = SimpleNamespace(ConnectionTcpMTProxyRandomizedIntermediate=marker)
        options = telethon_client_options(MtProtoProxy(True, "127.0.0.1", 443), connection)
        self.assertIs(marker, options["connection"])
        self.assertEqual(("127.0.0.1", 443, DEFAULT_MTPROTO_SECRET), options["proxy"])

    def test_settings_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "test.sqlite3")
            expected = MtProtoProxy(True, "proxy.example.com", 8443, "ab" * 16)
            save_mtproto_proxy(db, expected)
            self.assertEqual(expected, load_mtproto_proxy(Database(db.path)))


if __name__ == "__main__":
    unittest.main()
