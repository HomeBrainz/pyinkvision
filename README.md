# pyinkvision

Async Python client for the local API of [inkVision](https://inkvision.eu) devices: the Atmos
Smart Clock, Frame and Bento Bar. The devices are reached over your network with plain HTTP and a
WebSocket; no cloud account is needed.

It is the library behind the inkVision integration for Home Assistant.

## Install

```sh
pip install pyinkvision
```

Requires Python 3.12 or newer and aiohttp.

## Usage

```python
import asyncio

import aiohttp

from pyinkvision import InkVisionClient, InkVisionConnectionError


async def main() -> None:
    async with aiohttp.ClientSession() as session:
        clock = InkVisionClient("192.168.1.50", session)

        status = await clock.get_status()
        print(status["device"], status["version"])
        print(await clock.get_sensors())

        await clock.set_screens(["clock", "temp", "humidity"])

        # Pushed updates, and commands back to the device.
        try:
            async with clock.websocket() as ws:
                await ws.send({"command": "get_sensors"})
                async for message in ws:
                    print(message)
        except InkVisionConnectionError as err:
            print("Connection ended:", err)


asyncio.run(main())
```

You pass in the `aiohttp.ClientSession`; the client never closes it.

## API

`InkVisionClient(host, session, *, timeout=10.0)`

| Method | Device endpoint |
| -- | -- |
| `get_info()` | `GET /api/info` |
| `get_status()` | `GET /status` |
| `get_sensors()` | `GET /sensors` |
| `get_screens()`, `set_screens(screens)` | `GET`/`POST /display/screens` |
| `get_button_actions()`, `set_button_action(button, gesture, action)` | `GET`/`POST /buttons/actions` |
| `get_ota_status()`, `start_ota_update(download_url)` | `GET /api/ota/check`, `POST /api/ota/update` |
| `get_config()`, `set_timezone(timezone)` | `GET /config.json`, `POST /config` |
| `speaker_command(action, **payload)` | `POST /speaker/...` |
| `websocket()` | `ws://<host>/ws` |

Errors:

- `InkVisionConnectionError`: the device is unreachable, timed out, or closed the WebSocket.
- `InkVisionResponseError`: the device answered with an error or an invalid reply. It has
  `status` and `body`.
- `InkVisionUnsupportedError`: a subclass of `InkVisionResponseError` for endpoints the
  firmware doesn't have (HTTP 404/405).
- `InkVisionError`: the base class of all of the above.

## Development

```sh
uv sync
uv run pytest
uv run mypy
uv run ruff check
```

Releases are published to PyPI by GitHub Actions when a GitHub release `vX.Y.Z` is published.
The tag must match `version` in `pyproject.toml`.

## License

MIT. inkVision is a product of HomeBrainz.
