import asyncio
import json
import logging
from time import perf_counter
from uuid import uuid4

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import Settings

logger = logging.getLogger("vector.http")
MAX_BODY_BYTES = 96 * 1024
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
)


class HttpMiddleware:
    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = str(uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        started = perf_counter()
        status = 500

        async def send_headers(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = list(message.get("headers", []))
                headers.extend([
                    (b"x-request-id", request_id.encode()),
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"same-origin"),
                    (b"cache-control", b"no-store"),
                ])
                if scope["path"] not in {"/docs", "/docs/oauth2-redirect", "/redoc"}:
                    headers.append((b"content-security-policy", CSP.encode()))
                message["headers"] = headers
            await send(message)

        async def reject(code: str, message: str, code_status: int) -> None:
            await JSONResponse({"error": {"code": code, "message": message}, "request_id": request_id},
                               status_code=code_status)(scope, receive, send_headers)

        try:
            if scope["path"].startswith("/api/") and scope["method"] not in {"GET", "HEAD", "OPTIONS"}:
                headers = {key.lower(): value for key, value in scope["headers"]}
                if headers.get(b"origin", b"").decode("latin1") != self.settings.public_origin:
                    await reject("origin", "Источник запроса не разрешён.", 403)
                    return
                if scope["path"] in {"/api/auth/login", "/api/ask"}:
                    if headers.get(b"content-type", b"").split(b";")[0].strip() != b"application/json":
                        await reject("content_type", "Ожидается application/json.", 415)
                        return
                chunks = []
                size = 0
                try:
                    async with asyncio.timeout(15):
                        while True:
                            message = await receive()
                            if message["type"] == "http.disconnect":
                                return
                            chunk = message.get("body", b"")
                            size += len(chunk)
                            if size > MAX_BODY_BYTES:
                                await reject("body_too_large", "Запрос слишком большой.", 413)
                                return
                            chunks.append(chunk)
                            if not message.get("more_body", False):
                                break
                except TimeoutError:
                    await reject("body_timeout", "Время передачи запроса истекло.", 408)
                    return
                buffered = b"".join(chunks)
                consumed = False

                async def replay() -> Message:
                    nonlocal consumed
                    if not consumed:
                        consumed = True
                        return {"type": "http.request", "body": buffered, "more_body": False}
                    return await receive()

                await self.app(scope, replay, send_headers)
            else:
                await self.app(scope, receive, send_headers)
        finally:
            # Never log request bodies, cookies, Authorization headers or database credentials.
            logger.info(json.dumps({"request_id": request_id, "method": scope["method"],
                                    "path": scope["path"], "status": status,
                                    "duration_ms": round((perf_counter() - started) * 1000)}))
