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


def expand_language_code(value: str) -> tuple[str, ...]:
    value = (value or "").strip()
    if not value:
        return ()
    lower = value.lower().replace("_", "-")
    if lower in {"main", "x-jat"}:
        return ("main",)
    if lower in {"zh", "zh-cn", "zh-sg", "zh-hans"}:
        return ("zh-Hans", "zh-Hant", "zh")
    if lower in {"zh-tw", "zh-hk", "zh-mo", "zh-hant"}:
        return ("zh-Hant", "zh-Hans", "zh")
    if "-" in lower:
        return (value, lower.split("-", 1)[0])
    return (value,)


def _title_aliases(value: str) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for item in value.replace("\n", ";").split(";"):
        if not item.strip() or "=" not in item:
            continue
        title, aid = item.split("=", 1)
        title = title.strip()
        aid = aid.strip()
        if title and aid:
            aliases[title] = aid
    return aliases


def _provider_kind(value: str) -> str:
    value = (value or "tv").strip().lower()
    return value if value in {"tv", "movie", "both"} else "tv"


def default_identifier(provider_kind: str) -> str:
    if provider_kind == "movie":
        return "tv.plex.agents.custom.zeroqi.hama.movie"
    if provider_kind == "both":
        return "tv.plex.agents.custom.zeroqi.hama.all"
    return "tv.plex.agents.custom.zeroqi.hama"


def default_title(provider_kind: str) -> str:
    if provider_kind == "movie":
        return "HAMA Remote Movies"
    if provider_kind == "both":
        return "HAMA Remote All"
    return "HAMA Remote TV"


@dataclass(frozen=True)
class Config:
    host: str
    port: int
    path_prefix: str
    base_url: str
    provider_kind: str
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
    use_plex_language: bool
    request_timeout: int
    max_match_results: int
    title_aliases: dict[str, str]

    @classmethod
    def from_env(cls) -> "Config":
        http_proxy = _env("HAMA_HTTP_PROXY") or _env("HTTP_PROXY") or _env("http_proxy")
        https_proxy = _env("HAMA_HTTPS_PROXY") or _env("HTTPS_PROXY") or _env("https_proxy")
        provider_kind = _provider_kind(_env("HAMA_PROVIDER_KIND", "tv"))
        title_languages = _env("HAMA_TITLE_LANGUAGES") or _env("HAMA_SERIES_LANGUAGES") or _env("HAMA_LANGUAGES", "main,en,ja")
        episode_languages = _env("HAMA_EPISODE_LANGUAGES") or _env("HAMA_LANGUAGES", "main,en,ja")
        return cls(
            host=_env("HAMA_HOST", "0.0.0.0"),
            port=_int("HAMA_PORT", 34567),
            path_prefix=_path_prefix(_env("HAMA_PATH_PREFIX")),
            base_url=_base_url(_env("HAMA_BASE_URL")),
            provider_kind=provider_kind,
            provider_identifier=_env("HAMA_PROVIDER_IDENTIFIER", default_identifier(provider_kind)),
            provider_title=_env("HAMA_PROVIDER_TITLE", default_title(provider_kind)),
            cache_dir=Path(_env("HAMA_CACHE_DIR", ".cache")).expanduser(),
            http_proxy=_proxy_url(http_proxy),
            https_proxy=_proxy_url(https_proxy),
            languages=_languages(title_languages),
            episode_languages=_languages(episode_languages),
            min_genre_weight=_int("HAMA_MIN_GENRE_WEIGHT", 400),
            include_weighted_genres=_bool("HAMA_INCLUDE_WEIGHTED_GENRES", False),
            include_adult=_bool("HAMA_INCLUDE_ADULT", False),
            proxy_assets=_bool("HAMA_PROXY_ASSETS", True),
            use_plex_language=_bool("HAMA_USE_PLEX_LANGUAGE", True),
            request_timeout=_int("HAMA_REQUEST_TIMEOUT", 60),
            max_match_results=_int("HAMA_MAX_MATCH_RESULTS", 10),
            title_aliases=_title_aliases(_env("HAMA_TITLE_ALIASES")),
        )

    def title_language_priority(self, plex_language: str = "") -> tuple[str, ...]:
        return self._language_priority(self.languages, plex_language)

    def episode_language_priority(self, plex_language: str = "") -> tuple[str, ...]:
        return self._language_priority(self.episode_languages, plex_language)

    def _language_priority(self, configured: tuple[str, ...], plex_language: str) -> tuple[str, ...]:
        priority: list[str] = []
        if self.use_plex_language:
            priority.extend(expand_language_code(plex_language))
        for language in configured:
            priority.extend(expand_language_code(language))
        if "main" not in priority:
            priority.append("main")
        return tuple(dict.fromkeys(priority))

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
