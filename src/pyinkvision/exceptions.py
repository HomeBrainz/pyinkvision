"""Exceptions raised by pyinkvision."""

from __future__ import annotations


class InkVisionError(Exception):
    """Base class for all pyinkvision errors."""


class InkVisionConnectionError(InkVisionError):
    """The device could not be reached, timed out, or closed the connection."""


class InkVisionResponseError(InkVisionError):
    """The device answered, but rejected the request or sent an invalid reply."""

    def __init__(self, message: str, status: int | None = None, body: str = "") -> None:
        """Initialize with the HTTP status and the start of the response body."""
        super().__init__(message)
        self.status = status
        self.body = body


class InkVisionUnsupportedError(InkVisionResponseError):
    """The firmware does not offer this endpoint (HTTP 404 or 405)."""
