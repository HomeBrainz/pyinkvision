"""The device WebSocket: pushed updates in, commands out."""

from __future__ import annotations

from collections.abc import AsyncIterator
import json
import logging
from typing import Any

import aiohttp

from .exceptions import InkVisionConnectionError

_LOGGER = logging.getLogger(__name__)


class InkVisionWebSocket:
    """An open WebSocket to a device. Get one from InkVisionClient.websocket().

    Iterate over it to receive messages as dicts, for example
    {"type": "sensor_update", "data": {...}} or a reply to a command,
    {"command": "get_status", "success": true, "data": {...}}.
    The iteration raises InkVisionConnectionError when the connection ends.
    """

    def __init__(self, connection: aiohttp.ClientWebSocketResponse) -> None:
        """Wrap an open aiohttp WebSocket."""
        self._connection = connection

    @property
    def closed(self) -> bool:
        """Return whether the connection is closed."""
        return self._connection.closed

    async def send(self, message: dict[str, Any]) -> None:
        """Send a message, e.g. {"command": "set_brightness", "value": 8}."""
        if self._connection.closed:
            raise InkVisionConnectionError("WebSocket is closed")
        try:
            await self._connection.send_str(json.dumps(message))
        except (aiohttp.ClientError, ConnectionError) as err:
            raise InkVisionConnectionError(f"WebSocket send failed: {err}") from err

    async def close(self) -> None:
        """Close the connection."""
        await self._connection.close()

    async def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        """Yield each message the device sends."""
        async for message in self._connection:
            if message.type is aiohttp.WSMsgType.TEXT:
                try:
                    data = json.loads(message.data)
                except ValueError:
                    _LOGGER.warning("Ignoring invalid JSON from the device: %.200s", message.data)
                    continue
                if isinstance(data, dict):
                    yield data
                else:
                    _LOGGER.debug("Ignoring non-object message: %.200s", message.data)
            elif message.type is aiohttp.WSMsgType.ERROR:
                raise InkVisionConnectionError(f"WebSocket error: {self._connection.exception()}")
        raise InkVisionConnectionError(
            f"WebSocket closed by the device (code {self._connection.close_code})"
        )
