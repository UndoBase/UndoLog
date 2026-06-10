"""Mock upstream MCP tool server for UndoLog demos.

Accepts tool call POST requests from the UndoLog proxy (``HTTPToolExecutor``)
and returns a canned ``ToolResult`` with the input echoed in ``output.args``.

Endpoints
---------
POST /tools
    Execute a tool call.  Request body is a ``ToolCall`` JSON object.
    Response is a ``ToolResult`` JSON object.

GET /health
    Returns ``{"status": "ok"}``.

Usage
-----
::

    python infra/mock-tool-server/server.py

Environment
-----------
MOCK_TOOL_SERVER_ADDR : str
    Listen address (default ``0.0.0.0``).
MOCK_TOOL_SERVER_PORT : int
    Listen port (default ``9091``).
"""

from __future__ import annotations

import json
import logging
import os
import time
from http import HTTPStatus
from http.server import HTTPServer, BaseHTTPRequestHandler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("mock-tool-server")

TOOL_CALLS: list[dict] = []


class Handler(BaseHTTPRequestHandler):
    """HTTP handler that echoes tool calls back as successful results."""

    def do_GET(self) -> None:
        if self.path == "/health":
            self._json_response({"status": "ok"})
            return
        self._json_response({"error": "not_found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if self.path != "/tools":
            self._json_response({"error": "not_found"}, HTTPStatus.NOT_FOUND)
            return

        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)

        try:
            call = json.loads(body)
        except json.JSONDecodeError:
            self._json_response(
                {"success": False, "error": "invalid JSON"},
                HTTPStatus.BAD_REQUEST,
            )
            return

        tool_name = call.get("tool_name", "unknown")
        args = call.get("args", {})
        log.info("TOOL CALL: %s args=%s", tool_name, json.dumps(args))

        TOOL_CALLS.append({"tool_name": tool_name, "args": args, "timestamp": time.time()})

        result = {
            "success": True,
            "output": json.dumps({
                "echo": True,
                "tool_name": tool_name,
                "args": args,
                "mock_result": "ok",
            }),
            "duration_ms": 0,
        }
        self._json_response(result)

    def _json_response(self, data: dict, status: int = HTTPStatus.OK) -> None:
        payload = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt: str, *args: object) -> None:
        log.debug(fmt, *args)


def run() -> None:
    addr = os.environ.get("MOCK_TOOL_SERVER_ADDR", "0.0.0.0")
    port = int(os.environ.get("MOCK_TOOL_SERVER_PORT", "9091"))
    server = HTTPServer((addr, port), Handler)
    log.info("Listening on %s:%s", addr, port)
    log.info("Endpoints: POST /tools, GET /health")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down")
        server.server_close()


if __name__ == "__main__":
    run()
