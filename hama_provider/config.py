from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name, default)
    return value.strip() if isinstance(value, str) else default


def _bool(name: str, default: bool) -> bool:
    value = _env(name)
    if not value:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _proxy_url(value: str) -> str:
    value = value.strip()
    if not value or value.lower() in {"none", "n/a"}:
        return ""
    return value if "://" in value else f"http://{value}"


def _path_prefix(value: str) -> str:
    value = value.strip().rstrip("/")
    if not value:
        return ""
    return value if value.startswith("/") else f"/{value}"


def _base_url(value: str) -> str:
    return value.strip().rstrip("/")


def _languages(value: str) -> tuple[str, ...]:
    items = tuple(item.strip() for item in value.split(",") if item.strip())
    return items or ("main", "en", "ja")


@dataclass(frozen=True)
class Config:
    host: str
    port: int
    path_prefix: str
    base_url: str
    provider_identifier: str
    provider_title: str
    cache_dir: Path
    http_proxy: str
    https_proxy: str
    languages: tuple[str, ...]
    episode_languages: tuple[str, ...]
    min_genre_weight: int
    include_weighted_genres: bool
    include_adult: bool
    proxy_assets: bool
    request_timeout: int
    max_match_results: int

    @classmethod
    def from_env(cls) -> "Config":
        http_proxy = _env("HAMA_HTTP_PROXY") or _env("HTTP_PROXY") or _env("http_proxy")
        https_proxy = _env("HAMA_HTTPS_PROXY") or _env("HTTPS_PROXY") or _env("https_proxy")
        return cls(
            host=_env("HAMA_HOST", "0.0.0.0"),
            port=_int("HAMA_PORT", 34567),
            path_prefix=_path_prefix(_env("HAMA_PATH_PREFIX")),
            base_url=_base_url(_env("HAMA_BASE_URL")),
            provider_identifier=_env("HAMA_PROVIDER_IDENTIFIER", "tv.plex.agents.custom.zeroqi.hama"),
            provider_title=_env("HAMA_PROVIDER_TITLE", "HAMA Remote"),
            cache_dir=Path(_env("HAMA_CACHE_DIR", ".cache")).expanduser(),
            http_proxy=_proxy_url(http_proxy),
            https_proxy=_proxy_url(https_proxy),
            languages=_languages(_env("HAMA_LANGUAGES", "main,en,ja")),
            episode_languages=_languages(_env("HAMA_EPISODE_LANGUAGES", "main,en,ja")),
            min_genre_weight=_int("HAMA_MIN_GENRE_WEIGHT", 400),
            include_weighted_genres=_bool("HAMA_INCLUDE_WEIGHTED_GENRES", False),
            include_adult=_bool("HAMA_INCLUDE_ADULT", False),
            proxy_assets=_bool("HAMA_PROXY_ASSETS", True),
            request_timeout=_int("HAMA_REQUEST_TIMEOUT", 60),
            max_match_results=_int("HAMA_MAX_MATCH_RESULTS", 10),
        )

    @property
    def provider_root(self) -> str:
        return self.base_url or self.path_prefix or "/"

    def provider_path(self, path: str) -> str:
        if not path.startswith("/"):
            path = f"/{path}"
        return f"{self.path_prefix}{path}" if self.path_prefix else path

    def public_url(self, path: str) -> str:
        if not path.startswith("/"):
            path = f"/{path}"
        if self.base_url:
            prefixless = path
            if self.path_prefix and prefixless.startswith(self.path_prefix):
                prefixless = prefixless[len(self.path_prefix) :] or "/"
            return f"{self.base_url}{prefixless}"
        return self.provider_path(path)
