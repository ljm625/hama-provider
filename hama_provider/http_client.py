from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import gzip
import hashlib
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

from .config import Config

LOG = logging.getLogger("hama_provider.http")


@dataclass(frozen=True)
class CachedResponse:
    body: bytes
    url: str
    cache_hit: bool


class HttpClient:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.cache_dir = config.cache_dir / "http"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        proxies: dict[str, str] = {}
        if config.http_proxy:
            proxies["http"] = config.http_proxy
        if config.https_proxy:
            proxies["https"] = config.https_proxy
        elif config.http_proxy:
            proxies["https"] = config.http_proxy
        self.proxies = proxies
        proxy_handler = urllib.request.ProxyHandler(proxies)
        self.opener = urllib.request.build_opener(proxy_handler)
        if proxies:
            safe = {key: self._redact(value) for key, value in proxies.items()}
            LOG.info("Using upstream proxies: %s", safe)

    def fetch(
        self,
        url: str,
        *,
        data: bytes | None = None,
        method: str | None = None,
        headers: dict[str, str] | None = None,
        ttl: int = 3600,
        timeout: int | None = None,
    ) -> CachedResponse:
        cache_key = self._cache_key(url, data, method)
        cache_file = self.cache_dir / cache_key
        if ttl > 0 and cache_file.exists() and time.time() - cache_file.stat().st_mtime < ttl:
            return CachedResponse(cache_file.read_bytes(), url, True)

        request_headers = {
            "User-Agent": "HAMA-Remote/0.1",
            "Accept": "*/*",
        }
        if headers:
            request_headers.update(headers)
        request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
        try:
            with self.opener.open(request, timeout=timeout or self.config.request_timeout) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            body = exc.read()
            message = body.decode("utf-8", "replace")[:500]
            raise RuntimeError(f"HTTP {exc.code} while fetching {url}: {message}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Network error while fetching {url}: {exc}") from exc

        cache_file.write_bytes(body)
        return CachedResponse(body, url, False)

    def fetch_text(self, url: str, *, ttl: int = 3600) -> str:
        body = self.fetch(url, ttl=ttl).body
        return body.decode("utf-8", "replace")

    def fetch_json(self, url: str, *, ttl: int = 3600) -> object:
        return json.loads(self.fetch_text(url, ttl=ttl))

    def fetch_xml_bytes(self, url: str, *, ttl: int = 3600) -> bytes:
        body = self.fetch(url, ttl=ttl).body
        if url.endswith(".gz") or body[:2] == b"\x1f\x8b":
            return gzip.decompress(body)
        return body

    @staticmethod
    def _cache_key(url: str, data: bytes | None, method: str | None) -> str:
        digest = hashlib.sha256()
        digest.update((method or "GET").encode("ascii"))
        digest.update(b"\0")
        digest.update(url.encode("utf-8"))
        digest.update(b"\0")
        if data:
            digest.update(data)
        suffix = Path(urllib.parse.urlparse(url).path).suffix
        return digest.hexdigest() + suffix

    @staticmethod
    def _redact(value: str) -> str:
        parts = urllib.parse.urlsplit(value)
        netloc = f"***@{parts.netloc.rsplit('@', 1)[1]}" if "@" in parts.netloc else parts.netloc
        return parts._replace(netloc=netloc).geturl()
