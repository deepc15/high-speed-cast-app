"""Lightweight HTTP server and REST API for HSCast GUI."""

from __future__ import annotations

import json
import mimetypes
import os
import socket
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlparse

if TYPE_CHECKING:
    from .app import HSCastManager

ASSETS_DIR = Path(__file__).resolve().parent / "assets"


class ApiHandler(BaseHTTPRequestHandler):
    manager: HSCastManager

    def log_message(self, format: str, *args) -> None:
        # Suppress standard HTTP request logging from cluttering stdout
        pass

    def _send_json(self, data: dict | list, status: int = 200) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, message: str, status: int = 400) -> None:
        self._send_json({"ok": False, "error": message}, status=status)

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0:
                return {}
            data = self.rfile.read(length).decode("utf-8")
            return json.loads(data)
        except Exception:
            return {}

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path.startswith("/api/"):
            self._handle_api_get(path, parse_qs(parsed.query))
        else:
            self._serve_static(path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path.startswith("/api/"):
            data = self._read_json()
            self._handle_api_post(path, data)
        else:
            self.send_error(HTTPStatus.METHOD_NOT_ALLOWED)

    def _handle_api_get(self, path: str, query: dict) -> None:
        if path == "/api/config":
            self._send_json({"ok": True, "config": self.manager.get_config()})
        elif path == "/api/status":
            self._send_json({"ok": True, "status": self.manager.get_system_status()})
        elif path == "/api/devices":
            self._send_json({"ok": True, "devices": self.manager.get_devices()})
        elif path == "/api/network":
            self._send_json({"ok": True, "ips": self.manager.get_local_ips()})
        elif path == "/api/monitors":
            self._send_json({"ok": True, "monitors": self.manager.get_monitors()})
        elif path == "/api/session/state":
            since = int(query.get("since", ["0"])[0])
            self._send_json({"ok": True, "session": self.manager.get_session_state(since=since)})
        else:
            self._send_error_json("Endpoint not found", status=404)

    def _handle_api_post(self, path: str, data: dict) -> None:
        if path == "/api/config":
            saved = self.manager.update_config(data)
            self._send_json({"ok": True, "config": saved})
        elif path == "/api/session/start":
            res = self.manager.start_session(data)
            self._send_json(res, status=200 if res.get("ok") else 400)
        elif path == "/api/session/stop":
            res = self.manager.stop_session()
            self._send_json(res)
        elif path == "/api/demo/start":
            res = self.manager.start_demo(data.get("role", "sender"))
            self._send_json(res)
        elif path == "/api/demo/stop":
            res = self.manager.stop_demo()
            self._send_json(res)
        elif path == "/api/action":
            action = data.get("action", "")
            res = self.manager.trigger_action(action)
            self._send_json(res)
        elif path == "/api/adb/browse":
            res = self.manager.browse_adb()
            self._send_json(res)
        elif path == "/api/record/browse":
            res = self.manager.browse_record_file()
            self._send_json(res)
        else:
            self._send_error_json("Endpoint not found", status=404)

    def _serve_static(self, path: str) -> None:
        if path in ("", "/"):
            path = "/index.html"
        rel_path = path.lstrip("/")
        file_path = (ASSETS_DIR / rel_path).resolve()

        # Prevent directory traversal
        try:
            file_path.relative_to(ASSETS_DIR)
        except ValueError:
            self.send_error(HTTPStatus.FORBIDDEN)
            return

        if not file_path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        mime_type, _ = mimetypes.guess_type(str(file_path))
        if not mime_type:
            mime_type = "application/octet-stream"

        try:
            with open(file_path, "rb") as f:
                content = f.read()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", f"{mime_type}; charset=utf-8" if "text" in mime_type else mime_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(content)
        except Exception as exc:
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))


def find_free_port(start: int = 8768, max_tries: int = 20) -> int:
    for port in range(start, start + max_tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return start


def start_server(manager: HSCastManager, port: int | None = None) -> tuple[ThreadingHTTPServer, int]:
    port = port or find_free_port()
    handler = type("ConfiguredApiHandler", (ApiHandler,), {"manager": manager})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    return server, port
