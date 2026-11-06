"""Async client for the local HTTP and WebSocket API of inkVision devices."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import json
import logging
from typing import Any, Literal

import aiohttp

from .exceptions import (
    InkVisionConnectionError,
    InkVisionResponseError,
    InkVisionUnsupportedError,
)
from .websocket import InkVisionWebSocket

_LOGGER = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 10.0
# The device fetches the image header before it answers.
OTA_UPDATE_TIMEOUT = 15.0
# WebSocket ping interval; the device must answer within half of it.
WEBSOCKET_HEARTBEAT = 15.0

SpeakerAction = Literal["play", "pause", "stop", "set_volume", "mute", "play_media"]

SPEAKER_ENDPOINTS: dict[str, str] = {
    "play": "/speaker/play",
    "pause": "/speaker/pause",
    "stop": "/speaker/stop",
    "set_volume": "/speaker/volume",
    "mute": "/speaker/mute",
    "play_media": "/speaker/play",
}


class InkVisionClient:
    """One inkVision device on the local network.

    The caller owns the aiohttp session; the client never closes it.
    """

    def __init__(
        self,
        host: str,
        session: aiohttp.ClientSession,
        *,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        """Initialize the client for a host name or IP address."""
        self.host = host
        self._session = session
        self._timeout = timeout

    @property
    def base_url(self) -> str:
        """Return the device's HTTP base URL."""
        return f"http://{self.host}"

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        form: dict[str, str] | None = None,
        request_timeout: float | None = None,
        expect_json: bool = True,
    ) -> Any:
        """Send a request and return the decoded JSON reply.

        Without expect_json, a reply that is empty or not JSON returns None.
        """
        url = f"{self.base_url}{path}"
        try:
            async with asyncio.timeout(request_timeout or self._timeout):
                async with self._session.request(
                    method, url, json=json_body, data=form
                ) as response:
                    body = await response.text()
                    status = response.status
        except TimeoutError as err:
            raise InkVisionConnectionError(f"Timeout talking to {url}") from err
        except aiohttp.ClientError as err:
            raise InkVisionConnectionError(f"Error talking to {url}: {err}") from err

        if status in (404, 405):
            raise InkVisionUnsupportedError(
                f"{method} {path} is not supported by this firmware", status, body[:200]
            )
        if status != 200:
            raise InkVisionResponseError(
                f"{method} {path} failed with HTTP {status}", status, body[:200]
            )
        try:
            return json.loads(body)
        except ValueError as err:
            if not expect_json:
                return None
            raise InkVisionResponseError(
                f"{method} {path} returned invalid JSON", status, body[:200]
            ) from err

    async def _get_dict(self, path: str) -> dict[str, Any]:
        data = await self._request("GET", path)
        if not isinstance(data, dict):
            raise InkVisionResponseError(f"GET {path} did not return an object", 200)
        return data

    async def get_info(self) -> dict[str, Any]:
        """Return the device identity (GET /api/info)."""
        return await self._get_dict("/api/info")

    async def get_status(self) -> dict[str, Any]:
        """Return the device status: name, model, MAC, version, brightness, ... (GET /status)."""
        return await self._get_dict("/status")

    async def get_sensors(self) -> dict[str, Any]:
        """Return the sensor readings, grouped by sensor chip (GET /sensors)."""
        return await self._get_dict("/sensors")

    async def get_screens(self) -> list[str]:
        """Return the screens in the display rotation (GET /display/screens)."""
        screens = (await self._get_dict("/display/screens")).get("screens")
        if not isinstance(screens, list):
            raise InkVisionResponseError("GET /display/screens has no screen list", 200)
        return [screen for screen in screens if isinstance(screen, str)]

    async def set_screens(self, screens: list[str]) -> None:
        """Set the screens in the display rotation (POST /display/screens)."""
        await self._request(
            "POST", "/display/screens", json_body={"screens": screens}, expect_json=False
        )

    async def get_button_actions(self) -> dict[str, Any]:
        """Return what each button gesture does on the device (GET /buttons/actions).

        For example {"button1": {"singleTap": "nextScreen", "longPress": "none"}, ...}.
        """
        return await self._get_dict("/buttons/actions")

    async def set_button_action(self, button: str, gesture: str, action: str) -> None:
        """Set what one gesture does, e.g. ("button1", "longPress", "nextScreen")."""
        await self._request(
            "POST",
            "/buttons/actions",
            json_body={button: {gesture: action}},
            expect_json=False,
        )

    async def get_ota_status(self) -> dict[str, Any]:
        """Ask the device to check for a firmware update (GET /api/ota/check)."""
        return await self._get_dict("/api/ota/check")

    async def start_ota_update(self, download_url: str) -> None:
        """Start installing the firmware at download_url; the device reboots when done."""
        await self._request(
            "POST",
            "/api/ota/update",
            json_body={"url": download_url, "confirm": True},
            request_timeout=OTA_UPDATE_TIMEOUT,
            expect_json=False,
        )

    async def get_config(self) -> dict[str, Any]:
        """Return the device settings (GET /config.json)."""
        return await self._get_dict("/config.json")

    async def set_timezone(self, timezone: str) -> None:
        """Set the clock's IANA timezone, e.g. "Europe/Prague" (POST /config)."""
        await self._request(
            "POST",
            "/config",
            form={"data": json.dumps({"timeZone": timezone})},
            expect_json=False,
        )

    async def speaker_command(self, action: SpeakerAction, **payload: Any) -> None:
        """Send a speaker command over HTTP.

        Raises InkVisionUnsupportedError on firmware that only takes speaker
        commands over the WebSocket.
        """
        body = {key: value for key, value in payload.items() if value is not None}
        reply = await self._request(
            "POST", SPEAKER_ENDPOINTS[action], json_body=body, expect_json=False
        )
        if isinstance(reply, dict) and reply.get("success") is False:
            raise InkVisionResponseError(
                f"Speaker command {action} rejected: {reply}", 200, json.dumps(reply)[:200]
            )

    @asynccontextmanager
    async def websocket(self) -> AsyncIterator[InkVisionWebSocket]:
        """Open the device's WebSocket (ws://<host>/ws), which pushes sensor and status updates."""
        url = f"ws://{self.host}/ws"
        try:
            async with asyncio.timeout(self._timeout):
                connection = await self._session.ws_connect(url, heartbeat=WEBSOCKET_HEARTBEAT)
        except TimeoutError as err:
            raise InkVisionConnectionError(f"Timeout connecting to {url}") from err
        except aiohttp.ClientError as err:
            raise InkVisionConnectionError(f"Error connecting to {url}: {err}") from err

        websocket = InkVisionWebSocket(connection)
        try:
            yield websocket
        finally:
            await websocket.close()
