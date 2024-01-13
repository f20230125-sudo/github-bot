"""Keep the local API from being driven by other web pages open in your browser.

Three checks: the Host header must be this machine, and any request that changes
something must carry our custom header and come from our own site's Origin.
"""

from __future__ import annotations

import json

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
GUARD_HEADER = "x-desk-request"


class GuardMiddleware:
    def __init__(self, app, allowed_hosts: set[str], allowed_origin: str):
        self.app = app
        self.allowed_hosts = allowed_hosts
        self.allowed_origin = allowed_origin

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}

        if headers.get("host", "") not in self.allowed_hosts:
            await self._deny(send, "Host not allowed.")
            return

        if scope["method"] not in SAFE_METHODS:
            if headers.get(GUARD_HEADER) != "1" or headers.get("origin") != self.allowed_origin:
                await self._deny(send, "Missing or invalid request headers.")
                return

        await self.app(scope, receive, send)

    @staticmethod
    async def _deny(send, detail: str) -> None:
        body = json.dumps({"detail": detail}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
