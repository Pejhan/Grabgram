from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

from tg_downloader.config import Config
from tg_downloader.proxy import MtProtoProxy
from tg_downloader.proxy_check import check_mtproto_proxy, sanitized_proxy_error


class FakeClient:
    def __init__(self, *_args, **kwargs):
        self.kwargs = kwargs
        self.connected = False
        self.disconnected = False

    async def connect(self) -> None:
        self.connected = True

    def is_connected(self) -> bool:
        return self.connected and not self.disconnected

    async def disconnect(self) -> None:
        self.disconnected = True


class ProxyCheckTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.config = Config(1, "hash", Path("session"), Path("db"), Path("downloads"))
        self.proxy = MtProtoProxy(True, "proxy.example.com", 443, "ab" * 16)

    async def test_check_connects_with_isolated_client_and_disconnects(self) -> None:
        clients: list[FakeClient] = []

        def factory(*args, **kwargs):
            client = FakeClient(*args, **kwargs)
            clients.append(client)
            return client

        marker = object()
        connection = SimpleNamespace(ConnectionTcpMTProxyRandomizedIntermediate=marker)
        result = await check_mtproto_proxy(
            self.config, self.proxy, client_factory=factory,
            session_factory=object, connection_module=connection,
        )
        self.assertGreaterEqual(result.elapsed_seconds, 0)
        self.assertEqual(("proxy.example.com", 443, "ab" * 16), clients[0].kwargs["proxy"])
        self.assertIs(marker, clients[0].kwargs["connection"])
        self.assertTrue(clients[0].disconnected)

    async def test_disabled_proxy_is_not_checked(self) -> None:
        with self.assertRaisesRegex(ValueError, "Enable"):
            await check_mtproto_proxy(self.config, MtProtoProxy(False))

    def test_error_message_redacts_secret(self) -> None:
        message = sanitized_proxy_error(RuntimeError(f"bad {self.proxy.secret}"), self.proxy)
        self.assertNotIn(self.proxy.secret, message)
        self.assertIn("[secret]", message)


if __name__ == "__main__":
    unittest.main()
