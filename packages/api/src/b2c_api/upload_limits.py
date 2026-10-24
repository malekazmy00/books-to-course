"""ASGI guard that refuses oversized upload bodies before they are spooled."""
from __future__ import annotations

import json
from typing import Any

# Room for the metadata_json form field, part headers and boundaries. The
# handler still enforces the exact PDF byte limit while hashing the file.
UPLOAD_MULTIPART_ALLOWANCE = 1024 * 1024


class _BodyTooLarge(Exception):
    pass


class UploadSizeLimitMiddleware:
    """Cap the request body of ``POST <path>``.

    A declared ``Content-Length`` above the cap is refused without reading the
    body; a chunked (or under-declared) body is cut off as soon as the received
    byte count passes the cap. Either way the client gets 413.
    """

    def __init__(self, app: Any, *, path: str, max_body_bytes: int) -> None:
        self.app = app
        self.path = path
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or scope.get("method") != "POST" or scope.get("path") != self.path:
            await self.app(scope, receive, send)
            return
        declared = dict(scope.get("headers") or []).get(b"content-length")
        if declared is not None:
            if not declared.isdigit() or int(declared) > self.max_body_bytes:
                await self._reject(send)
                return
        received = 0
        exceeded = False
        response_started = False

        async def limited_receive() -> dict:
            nonlocal received, exceeded
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_bytes:
                    exceeded = True
                    raise _BodyTooLarge
            return message

        async def guarded_send(message: dict) -> None:
            nonlocal response_started
            if exceeded and not response_started:
                return  # the framework's body-parse error is replaced by our 413
            response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except _BodyTooLarge:
            pass
        if exceeded and not response_started:
            await self._reject(send)

    @staticmethod
    async def _reject(send: Any) -> None:
        body = json.dumps({"detail": "PDF exceeds the configured upload limit"}).encode()
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode()),
                                (b"connection", b"close")]})
        await send({"type": "http.response.body", "body": body})
