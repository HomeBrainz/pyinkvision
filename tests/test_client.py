"""Tests for InkVisionClient against a fake device served by aiohttp."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
import json
from typing import Any

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer
import pytest

from pyinkvision import (
    InkVisionClient,
    InkVisionConnectionError,
    InkVisionResponseError,
    InkVisionUnsupportedError,
)

STATUS = {"device": "Atmos Smart Clock", "mac_address": "AA:BB:CC:DD:EE:FF", "version": "1.0.11"}

ServerFactory = Callable[[web.Application], Awaitable[TestServer]]


class FakeDevice:
    """Records requests and answers like the firmware does."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str, Any]] = []
        self.replies: dict[tuple[str, str], web.StreamResponse] = {}

    async def handle(self, request: web.Request) -> web.StreamResponse:
        if request.content_type == "application/json":
            body: Any = await request.json()
        else:
            body = dict(await request.post()) or None
        self.requests.append((request.method, request.path, body))
        key = (request.method, request.path)
        if key in self.replies:
            return self.replies[key]
        if request.method == "GET":
            return web.json_response(
                {
                    "/api/info": {"name": "Atmos Smart Clock"},
                    "/status": STATUS,
                    "/sensors": {"bme680": {"temperature": 21.5}},
                    "/display/screens": {"screens": ["clock", 3, "temp"]},
                    "/buttons/actions": {"button1": {"singleTap": "nextScreen"}},
                    "/api/ota/check": {"updateAvailable": False},
                    "/config.json": {"timeZone": "Europe/Prague"},
                }[request.path]
            )
        return web.json_response({"success": True})


@pytest.fixture
async def device(
    aiohttp_server: ServerFactory,
) -> AsyncIterator[tuple[FakeDevice, InkVisionClient]]:
    fake = FakeDevice()
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", fake.handle)
    server = await aiohttp_server(app)
    async with aiohttp.ClientSession() as session:
        yield fake, InkVisionClient(f"{server.host}:{server.port}", session)


async def test_reads(device: tuple[FakeDevice, InkVisionClient]) -> None:
    _, client = device
    assert client.base_url.startswith("http://127.0.0.1:")
    assert await client.get_info() == {"name": "Atmos Smart Clock"}
    assert await client.get_status() == STATUS
    assert await client.get_sensors() == {"bme680": {"temperature": 21.5}}
    assert await client.get_screens() == ["clock", "temp"]
    assert await client.get_button_actions() == {"button1": {"singleTap": "nextScreen"}}
    assert await client.get_ota_status() == {"updateAvailable": False}
    assert await client.get_config() == {"timeZone": "Europe/Prague"}


async def test_writes(device: tuple[FakeDevice, InkVisionClient]) -> None:
    fake, client = device
    await client.set_screens(["clock"])
    await client.set_button_action("button2", "longPress", "none")
    await client.start_ota_update("http://fw/bin")
    await client.set_timezone("Etc/UTC")
    await client.speaker_command("set_volume", volume=30, ignored=None)

    assert fake.requests == [
        ("POST", "/display/screens", {"screens": ["clock"]}),
        ("POST", "/buttons/actions", {"button2": {"longPress": "none"}}),
        ("POST", "/api/ota/update", {"url": "http://fw/bin", "confirm": True}),
        ("POST", "/config", {"data": '{"timeZone": "Etc/UTC"}'}),
        ("POST", "/speaker/volume", {"volume": 30}),
    ]


async def test_write_with_non_json_reply(device: tuple[FakeDevice, InkVisionClient]) -> None:
    fake, client = device
    fake.replies[("POST", "/speaker/play")] = web.Response(text="OK")
    await client.speaker_command("play_media", url="http://x/a.mp3")


@pytest.mark.parametrize(
    ("reply", "error", "status"),
    [
        (web.Response(status=404), InkVisionUnsupportedError, 404),
        (web.Response(status=405), InkVisionUnsupportedError, 405),
        (web.Response(status=500, text="boom"), InkVisionResponseError, 500),
        (web.Response(text="not json"), InkVisionResponseError, 200),
        (web.json_response([1, 2]), InkVisionResponseError, 200),
    ],
)
async def test_read_errors(
    device: tuple[FakeDevice, InkVisionClient],
    reply: web.Response,
    error: type[Exception],
    status: int,
) -> None:
    fake, client = device
    fake.replies[("GET", "/status")] = reply
    with pytest.raises(error) as err:
        await client.get_status()
    assert isinstance(err.value, InkVisionResponseError)
    assert err.value.status == status


async def test_screens_without_list(device: tuple[FakeDevice, InkVisionClient]) -> None:
    fake, client = device
    fake.replies[("GET", "/display/screens")] = web.json_response({})
    with pytest.raises(InkVisionResponseError):
        await client.get_screens()


async def test_rejected_speaker_command(device: tuple[FakeDevice, InkVisionClient]) -> None:
    fake, client = device
    fake.replies[("POST", "/speaker/pause")] = web.json_response({"success": False})
    with pytest.raises(InkVisionResponseError, match="rejected"):
        await client.speaker_command("pause")


async def test_unreachable() -> None:
    async with aiohttp.ClientSession() as session:
        # Nothing listens on port 1.
        client = InkVisionClient("127.0.0.1:1", session)
        with pytest.raises(InkVisionConnectionError):
            await client.get_status()
        with pytest.raises(InkVisionConnectionError):
            async with client.websocket():
                pass  # pragma: no cover


async def test_timeout(aiohttp_server: ServerFactory) -> None:
    async def slow(request: web.Request) -> web.Response:
        await asyncio.sleep(1)
        return web.json_response({})  # pragma: no cover

    app = web.Application()
    app.router.add_get("/status", slow)
    app.router.add_get("/ws", slow)
    server = await aiohttp_server(app)
    async with aiohttp.ClientSession() as session:
        client = InkVisionClient(f"{server.host}:{server.port}", session, timeout=0.05)
        with pytest.raises(InkVisionConnectionError, match="Timeout"):
            await client.get_status()
        with pytest.raises(InkVisionConnectionError, match="Timeout"):
            async with client.websocket():
                pass  # pragma: no cover


async def test_websocket(aiohttp_server: ServerFactory) -> None:
    received: list[Any] = []

    async def ws_handler(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_str("not json")
        await ws.send_str("[1]")
        await ws.send_json({"type": "sensor_update", "data": {"temp": 1}})
        received.append(json.loads((await ws.receive()).data))
        await ws.send_json({"command": "get_status", "success": True})
        await ws.close()
        return ws

    app = web.Application()
    app.router.add_get("/ws", ws_handler)
    server = await aiohttp_server(app)
    async with aiohttp.ClientSession() as session:
        client = InkVisionClient(f"{server.host}:{server.port}", session)
        messages = []
        with pytest.raises(InkVisionConnectionError, match="closed by the device"):
            async with client.websocket() as ws:
                assert not ws.closed
                async for message in ws:
                    messages.append(message)
                    if message.get("type") == "sensor_update":
                        await ws.send({"command": "get_status"})
        assert ws.closed
        with pytest.raises(InkVisionConnectionError, match="closed"):
            await ws.send({"command": "get_status"})

    assert messages == [
        {"type": "sensor_update", "data": {"temp": 1}},
        {"command": "get_status", "success": True},
    ]
    assert received == [{"command": "get_status"}]


async def test_websocket_send_failure() -> None:
    class BrokenConnection:
        closed = False

        async def send_str(self, data: str) -> None:
            raise ConnectionResetError("gone")

    from pyinkvision import InkVisionWebSocket

    ws = InkVisionWebSocket(BrokenConnection())  # type: ignore[arg-type]
    with pytest.raises(InkVisionConnectionError, match="send failed"):
        await ws.send({"command": "restart"})


async def test_websocket_error_message() -> None:
    class ErrorConnection:
        closed = False

        def exception(self) -> Exception:
            return RuntimeError("bad frame")

        async def __aiter__(self) -> AsyncIterator[aiohttp.WSMessage]:
            yield aiohttp.WSMessage(aiohttp.WSMsgType.ERROR, None, None)

    from pyinkvision import InkVisionWebSocket

    ws = InkVisionWebSocket(ErrorConnection())  # type: ignore[arg-type]
    with pytest.raises(InkVisionConnectionError, match="bad frame"):
        async for _ in ws:
            pass  # pragma: no cover
