from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import traceback
import urllib.parse

from .config import Config
from .service import HamaProviderService

LOG = logging.getLogger("hama_provider.server")


class HamaRequestHandler(BaseHTTPRequestHandler):
    service: HamaProviderService
    config: Config

    server_version = "HamaProvider/0.1"

    def do_GET(self) -> None:
        try:
            path = self._route_path()
            if path is None:
                self._json_error(HTTPStatus.NOT_FOUND, "Not found")
                return
            if path in {"", "/"}:
                self._send_json(self.service.provider())
            elif path == "/health":
                self._send_json(self.service.health())
            elif path.startswith("/asset/"):
                token = path.rsplit("/", 1)[-1]
                body, content_type = self.service.asset(token)
                self._send_bytes(body, content_type)
            elif path.startswith("/library/metadata/"):
                self._metadata_route(path)
            else:
                self._json_error(HTTPStatus.NOT_FOUND, "Not found")
        except Exception as exc:
            LOG.error("GET %s failed: %s\n%s", self.path, exc, traceback.format_exc())
            self._json_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def do_POST(self) -> None:
        try:
            path = self._route_path()
            if path == "/library/metadata/matches":
                self._send_json(self.service.match(self._read_json_body()))
            else:
                self._json_error(HTTPStatus.NOT_FOUND, "Not found")
        except Exception as exc:
            LOG.error("POST %s failed: %s\n%s", self.path, exc, traceback.format_exc())
            self._json_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def log_message(self, format: str, *args: object) -> None:
        LOG.info("%s - %s", self.address_string(), format % args)

    def _metadata_route(self, path: str) -> None:
        relative = path[len("/library/metadata/") :]
        parts = [urllib.parse.unquote(part) for part in relative.split("/") if part]
        if not parts:
            self._json_error(HTTPStatus.BAD_REQUEST, "Missing ratingKey")
            return
        rating_key = parts[0]
        start, size = self._paging()
        if len(parts) == 1:
            self._send_json(self.service.metadata(rating_key))
        elif len(parts) == 2 and parts[1] == "children":
            self._send_json(self.service.children(rating_key, start=start, size=size))
        elif len(parts) == 2 and parts[1] == "grandchildren":
            self._send_json(self.service.grandchildren(rating_key, start=start, size=size))
        elif len(parts) == 2 and parts[1] == "images":
            self._send_json(self.service.images(rating_key))
        else:
            self._json_error(HTTPStatus.NOT_FOUND, "Not found")

    def _route_path(self) -> str | None:
        path = urllib.parse.urlparse(self.path).path.rstrip("/") or "/"
        prefix = self.config.path_prefix
        if not prefix:
            return path
        if path == prefix:
            return "/"
        if path.startswith(prefix + "/"):
            return path[len(prefix) :] or "/"
        return None

    def _paging(self) -> tuple[int, int]:
        try:
            start = int(self.headers.get("X-Plex-Container-Start", "0"))
        except ValueError:
            start = 0
        try:
            size = int(self.headers.get("X-Plex-Container-Size", "20"))
        except ValueError:
            size = 20
        return max(0, start), max(1, min(size, 200))

    def _read_json_body(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if not length:
            return {}
        body = self.rfile.read(length).decode("utf-8")
        return json.loads(body) if body else {}

    def _send_json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json_error(self, status: HTTPStatus, message: str) -> None:
        self._send_json({"error": message, "status": int(status)}, status)

    def _send_bytes(self, body: bytes, content_type: str) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "public, max-age=2592000")
        self.end_headers()
        self.wfile.write(body)


def run_server(config: Config) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    service = HamaProviderService(config)

    class Handler(HamaRequestHandler):
        pass

    Handler.service = service
    Handler.config = config
    server = ThreadingHTTPServer((config.host, config.port), Handler)
    LOG.info("HAMA remote provider listening on http://%s:%s%s", config.host, config.port, config.path_prefix or "/")
    LOG.info("Provider identifier: %s", config.provider_identifier)
    server.serve_forever()
