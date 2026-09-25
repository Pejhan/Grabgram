from __future__ import annotations

import asyncio
from dataclasses import dataclass
import time
from typing import Any, Callable

try:
    from .config import Config
    from .proxy import MtProtoProxy, ensure_telethon_compatible, telethon_client_options
except ImportError:
    from config import Config
    from proxy import MtProtoProxy, ensure_telethon_compatible, telethon_client_options


@dataclass(frozen=True)
class ProxyCheckResult:
    elapsed_seconds: float


async def check_mtproto_proxy(
    config: Config,
    proxy: MtProtoProxy,
    timeout_seconds: float = 15.0,
    *,
    client_factory: Callable[..., Any] | None = None,
    session_factory: Callable[[], Any] | None = None,
    connection_module: Any = None,
) -> ProxyCheckResult:
    """Open an isolated in-memory Telegram connection through an MTProto proxy."""
    proxy = proxy.validated()
    if not proxy.enabled:
        raise ValueError("Enable the MTProto proxy before checking it.")
    ensure_telethon_compatible(proxy)
    if client_factory is None or session_factory is None or connection_module is None:
        try:
            from telethon import TelegramClient, connection
            from telethon.sessions import MemorySession
        except ImportError as exc:
            raise RuntimeError("Telethon is not installed.") from exc
        client_factory = TelegramClient
        session_factory = MemorySession
        connection_module = connection

    client = client_factory(
        session_factory(), config.api_id, config.api_hash,
        connection_retries=1, retry_delay=0, auto_reconnect=False,
        **telethon_client_options(proxy, connection_module),
    )
    started = time.monotonic()
    try:
        await asyncio.wait_for(client.connect(), timeout=max(1.0, float(timeout_seconds)))
        if not client.is_connected():
            raise ConnectionError("The proxy did not establish a Telegram connection.")
        return ProxyCheckResult(time.monotonic() - started)
    except asyncio.TimeoutError as exc:
        raise TimeoutError(f"No Telegram connection after {timeout_seconds:g} seconds.") from exc
    finally:
        if client.is_connected():
            await client.disconnect()


def sanitized_proxy_error(error: BaseException, proxy: MtProtoProxy) -> str:
    message = str(error) or type(error).__name__
    for sensitive in {proxy.secret, proxy.effective_secret}:
        if sensitive:
            message = message.replace(sensitive, "[secret]")
    return f"{type(error).__name__}: {message}"
