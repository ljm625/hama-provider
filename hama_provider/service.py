from __future__ import annotations

from dataclasses import dataclass
import base64
import logging
import re
from typing import Any
import urllib.parse

from . import __version__
from .anime_lists import AnimeListMapping, AnimeListsRepository
from .anidb import AniDBRepository, AnimeMetadata, EpisodeMetadata
from .config import Config
from .http_client import HttpClient
from .models import TYPE_NAMES, guid_items, image_container, media_container, tag_items

LOG = logging.getLogger("hama_provider.service")
FORCED_ID_RE = re.compile(r"[\[\{](?P<source>anidb[0-9]*|tvdb[0-9]*|tmdb|imdb)-(?P<id>[^\]\}]+)[\]\}]", re.I)
CUSTOM_GUID_RE = re.compile(r"(?P<scheme>[^:]+)://(?P<type>movie|show|season|episode)/(?P<key>[^/?#]+)", re.I)
EXTERNAL_GUID_RE = re.compile(r"(?P<source>anidb|tvdb|tmdb|imdb)://(?P<id>[^/?#]+)", re.I)
RATING_KEY_RE = re.compile(r"^anidb-(?P<aid>[0-9]+)(?P<movie>-movie)?(?:-s(?P<season>[0-9]+)(?:e(?P<episode>[0-9]+))?)?$")


@dataclass(frozen=True)
class ParsedRatingKey:
    aid: str
    item_type: str
    season: int | None = None
    episode: int | None = None


class HamaProviderService:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.client = HttpClient(config)
        self.anime_lists = AnimeListsRepository(self.client)
        self.anidb = AniDBRepository(config, self.client)

    def provider(self) -> dict[str, Any]:
        scheme = self.config.provider_identifier
        return {
            "MediaProvider": {
                "identifier": scheme,
                "title": self.config.provider_title,
                "version": __version__,
                "Types": [
                    {"type": 1, "Scheme": [{"scheme": scheme}]},
                    {"type": 2, "Scheme": [{"scheme": scheme}]},
                    {"type": 3, "Scheme": [{"scheme": scheme}]},
                    {"type": 4, "Scheme": [{"scheme": scheme}]},
                ],
                "Feature": [
                    {"type": "metadata", "key": self.config.provider_path("/library/metadata")},
                    {"type": "match", "key": self.config.provider_path("/library/metadata/matches")},
                ],
            }
        }

    def health(self) -> dict[str, Any]:
        return {
            "ok": True,
            "version": __version__,
            "providerIdentifier": self.config.provider_identifier,
            "proxy": bool(self.client.proxies),
        }

    def match(self, payload: dict[str, Any]) -> dict[str, Any]:
        item_type = self._payload_type(payload)
        title = self._payload_title(payload)
        forced = self._forced_id(payload, title)
        candidates: list[dict[str, Any]] = []

        if forced:
            mapping = self._mapping_for_forced_id(*forced)
            aid = mapping.anidb_id if mapping else forced[1] if forced[0].startswith("anidb") else ""
            if aid:
                candidates.append(self._match_metadata(aid, item_type, 100, mapping=mapping))
        else:
            for candidate in self.anidb.search(title, limit=self.config.max_match_results):
                mapping = self.anime_lists.find_by_anidb(candidate.aid)
                candidates.append(self._match_metadata(candidate.aid, item_type, candidate.score, title=candidate.title, mapping=mapping))

        manual = str(payload.get("manual", "")).lower() in {"1", "true", "yes"}
        if not manual and candidates:
            candidates = candidates[:1]
        return media_container(self.config.provider_identifier, candidates)

    def metadata(self, rating_key: str) -> dict[str, Any]:
        parsed = self._parse_rating_key(rating_key)
        anime = self.anidb.fetch_metadata(parsed.aid)
        mapping = self.anime_lists.find_by_anidb(parsed.aid)
        if parsed.item_type in {"show", "movie"}:
            item = self._anime_metadata(anime, parsed.item_type, mapping=mapping)
        elif parsed.item_type == "season":
            item = self._season_metadata(anime, parsed.season or 0, mapping=mapping)
        else:
            episode = self._find_episode(anime, parsed.season or 0, parsed.episode or 0, mapping=mapping)
            item = self._episode_metadata(anime, episode, mapping=mapping)
        return media_container(self.config.provider_identifier, [item], total_size=1)

    def children(self, rating_key: str, *, start: int, size: int) -> dict[str, Any]:
        parsed = self._parse_rating_key(rating_key)
        anime = self.anidb.fetch_metadata(parsed.aid)
        mapping = self.anime_lists.find_by_anidb(parsed.aid)
        if parsed.item_type == "show":
            seasons = sorted({self._mapped_episode_number(episode, mapping)[0] for episode in anime.episodes})
            items = [self._season_metadata(anime, season, mapping=mapping) for season in seasons]
        elif parsed.item_type == "season":
            episodes = [
                episode
                for episode in anime.episodes
                if self._mapped_episode_number(episode, mapping)[0] == parsed.season
            ]
            items = [self._episode_metadata(anime, episode, mapping=mapping) for episode in episodes]
        else:
            items = []
        page = items[start : start + size]
        return media_container(self.config.provider_identifier, page, offset=start, total_size=len(items))

    def grandchildren(self, rating_key: str, *, start: int, size: int) -> dict[str, Any]:
        parsed = self._parse_rating_key(rating_key)
        anime = self.anidb.fetch_metadata(parsed.aid)
        mapping = self.anime_lists.find_by_anidb(parsed.aid)
        items = [self._episode_metadata(anime, episode, mapping=mapping) for episode in anime.episodes]
        page = items[start : start + size]
        return media_container(self.config.provider_identifier, page, offset=start, total_size=len(items))

    def images(self, rating_key: str) -> dict[str, Any]:
        parsed = self._parse_rating_key(rating_key)
        anime = self.anidb.fetch_metadata(parsed.aid)
        images: list[dict[str, Any]] = []
        if anime.picture:
            images.append({"type": "coverPoster", "url": self.asset_url(anime.picture), "alt": f"{anime.title} poster"})
        return image_container(self.config.provider_identifier, images)

    def asset(self, token: str) -> tuple[bytes, str]:
        url = self._decode_asset_url(token)
        body = self.client.fetch(url, ttl=30 * 24 * 60 * 60).body
        extension = urllib.parse.urlparse(url).path.rsplit(".", 1)[-1].lower()
        content_type = {
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "png": "image/png",
            "webp": "image/webp",
        }.get(extension, "application/octet-stream")
        return body, content_type

    def asset_url(self, url: str) -> str:
        if not url:
            return ""
        if not self.config.proxy_assets:
            return url
        token = base64.urlsafe_b64encode(url.encode("utf-8")).decode("ascii").rstrip("=")
        return self.config.public_url(f"/asset/{token}")

    def _match_metadata(
        self,
        aid: str,
        item_type: str,
        score: int,
        *,
        title: str = "",
        mapping: AnimeListMapping | None = None,
    ) -> dict[str, Any]:
        title = title or (mapping.name if mapping else "") or self.anidb.title_for_aid(aid)
        key_type = "movie" if item_type == "movie" else "show"
        key = self._rating_key(aid, key_type)
        return {
            "ratingKey": key,
            "key": self.config.provider_path(f"/library/metadata/{key}"),
            "guid": self._guid(key_type, key),
            "type": key_type,
            "title": title,
            "score": max(0, min(100, int(score))),
        }

    def _anime_metadata(self, anime: AnimeMetadata, item_type: str, *, mapping: AnimeListMapping | None) -> dict[str, Any]:
        key = self._rating_key(anime.aid, item_type)
        metadata: dict[str, Any] = {
            "ratingKey": key,
            "key": self.config.provider_path(f"/library/metadata/{key}"),
            "guid": self._guid(item_type, key),
            "type": item_type,
            "title": anime.title,
            "originalTitle": anime.original_title,
            "originallyAvailableAt": anime.originally_available_at or "1900-01-01",
            "summary": anime.summary,
            "Genre": tag_items(tuple(dict.fromkeys((*anime.genres, *(mapping.genres if mapping else ()))))),
            "Guid": guid_items(self._external_guids(anime, mapping)),
        }
        if anime.year:
            metadata["year"] = anime.year
        if anime.rating is not None:
            metadata["rating"] = anime.rating
        if anime.picture:
            metadata["thumb"] = self.asset_url(anime.picture)
        if anime.content_rating:
            metadata["contentRating"] = anime.content_rating
        studio = mapping.studio if mapping and mapping.studio else anime.studio
        if studio:
            metadata["studio"] = studio
        collection = self.anime_lists.collection_for_anidb(anime.aid)
        if collection:
            metadata["Collection"] = tag_items([collection])
        if anime.roles:
            metadata["Role"] = [
                {"tag": role.name, "role": role.role, **({"thumb": self.asset_url(role.photo)} if role.photo else {})}
                for role in anime.roles[:30]
            ]
        directors = list(anime.directors)
        if mapping and mapping.director:
            directors.append(mapping.director)
        if directors:
            metadata["Director"] = tag_items(tuple(dict.fromkeys(directors)))
        writers = list(anime.writers)
        if mapping and mapping.writer:
            writers.append(mapping.writer)
        if writers:
            metadata["Writer"] = tag_items(tuple(dict.fromkeys(writers)))
        if anime.producers:
            metadata["Producer"] = tag_items(anime.producers)
        if item_type == "show":
            metadata["childCount"] = len({episode.season for episode in anime.episodes})
            metadata["leafCount"] = len(anime.episodes)
        return metadata

    def _season_metadata(self, anime: AnimeMetadata, season: int, *, mapping: AnimeListMapping | None) -> dict[str, Any]:
        show_key = self._rating_key(anime.aid, "show")
        key = self._rating_key(anime.aid, "season", season=season)
        episodes = [
            episode
            for episode in anime.episodes
            if self._mapped_episode_number(episode, mapping)[0] == season
        ]
        metadata: dict[str, Any] = {
            "ratingKey": key,
            "key": self.config.provider_path(f"/library/metadata/{key}"),
            "guid": self._guid("season", key),
            "type": "season",
            "title": "Specials" if season == 0 else f"Season {season}",
            "index": season,
            "parentRatingKey": show_key,
            "parentKey": self.config.provider_path(f"/library/metadata/{show_key}"),
            "parentGuid": self._guid("show", show_key),
            "parentTitle": anime.title,
            "originallyAvailableAt": anime.originally_available_at or "1900-01-01",
            "leafCount": len(episodes),
            "Guid": guid_items(self._external_guids(anime, mapping)),
        }
        if anime.picture:
            metadata["thumb"] = self.asset_url(anime.picture)
        return metadata

    def _episode_metadata(self, anime: AnimeMetadata, episode: EpisodeMetadata, *, mapping: AnimeListMapping | None) -> dict[str, Any]:
        season, index = self._mapped_episode_number(episode, mapping)
        show_key = self._rating_key(anime.aid, "show")
        season_key = self._rating_key(anime.aid, "season", season=season)
        key = self._rating_key(anime.aid, "episode", season=season, episode=index)
        title = episode.title or f"Episode {index}"
        metadata: dict[str, Any] = {
            "ratingKey": key,
            "key": self.config.provider_path(f"/library/metadata/{key}"),
            "guid": self._guid("episode", key),
            "type": "episode",
            "title": title,
            "index": index,
            "parentIndex": season,
            "parentRatingKey": season_key,
            "parentKey": self.config.provider_path(f"/library/metadata/{season_key}"),
            "parentGuid": self._guid("season", season_key),
            "parentTitle": "Specials" if season == 0 else f"Season {season}",
            "grandparentRatingKey": show_key,
            "grandparentKey": self.config.provider_path(f"/library/metadata/{show_key}"),
            "grandparentGuid": self._guid("show", show_key),
            "grandparentTitle": anime.title,
            "originallyAvailableAt": episode.originally_available_at or anime.originally_available_at or "1900-01-01",
            "summary": episode.summary,
            "Guid": guid_items(self._external_guids(anime, mapping)),
        }
        if episode.rating is not None:
            metadata["rating"] = episode.rating
        if episode.duration is not None:
            metadata["duration"] = episode.duration
        if episode.directors:
            metadata["Director"] = tag_items(episode.directors)
        if episode.writers:
            metadata["Writer"] = tag_items(episode.writers)
        if episode.producers:
            metadata["Producer"] = tag_items(episode.producers)
        if anime.picture:
            metadata["thumb"] = self.asset_url(anime.picture)
        return metadata

    def _payload_type(self, payload: dict[str, Any]) -> str:
        value = payload.get("type")
        if isinstance(value, int):
            return TYPE_NAMES.get(value, "show")
        if isinstance(value, str):
            if value.isdigit():
                return TYPE_NAMES.get(int(value), "show")
            return value.lower() if value.lower() in TYPE_NAMES.values() else "show"
        return "show"

    @staticmethod
    def _payload_title(payload: dict[str, Any]) -> str:
        for key in ("title", "grandparentTitle", "parentTitle", "name"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    def _forced_id(self, payload: dict[str, Any], title: str) -> tuple[str, str] | None:
        guid = str(payload.get("guid") or "")
        custom = CUSTOM_GUID_RE.search(guid)
        if custom and custom.group("scheme") == self.config.provider_identifier:
            parsed = self._parse_rating_key(custom.group("key"))
            return "anidb", parsed.aid
        external = EXTERNAL_GUID_RE.search(guid)
        if external:
            return external.group("source").lower(), external.group("id")
        match = FORCED_ID_RE.search(title)
        if match:
            return match.group("source").lower(), match.group("id").strip()
        return None

    def _mapping_for_forced_id(self, source: str, value: str) -> AnimeListMapping | None:
        return self.anime_lists.find_by_external(source, value)

    def _parse_rating_key(self, rating_key: str) -> ParsedRatingKey:
        match = RATING_KEY_RE.match(rating_key)
        if not match:
            raise ValueError(f"Unsupported ratingKey: {rating_key}")
        aid = match.group("aid")
        season = int(match.group("season")) if match.group("season") else None
        episode = int(match.group("episode")) if match.group("episode") else None
        if episode is not None:
            return ParsedRatingKey(aid, "episode", season or 0, episode)
        if season is not None:
            return ParsedRatingKey(aid, "season", season)
        if match.group("movie"):
            return ParsedRatingKey(aid, "movie")
        return ParsedRatingKey(aid, "show")

    def _find_episode(
        self,
        anime: AnimeMetadata,
        season: int,
        episode_index: int,
        *,
        mapping: AnimeListMapping | None,
    ) -> EpisodeMetadata:
        for episode in anime.episodes:
            mapped_season, mapped_episode = self._mapped_episode_number(episode, mapping)
            if mapped_season == season and mapped_episode == episode_index:
                return episode
        raise ValueError(f"Episode not found: s{season}e{episode_index}")

    @staticmethod
    def _mapped_episode_number(episode: EpisodeMetadata, mapping: AnimeListMapping | None) -> tuple[int, int]:
        if not mapping:
            return episode.season, episode.index
        for anidb_season, anidb_episode, tvdb_season, tvdb_episode in mapping.episode_map:
            if episode.season == anidb_season and episode.index == anidb_episode:
                return tvdb_season, tvdb_episode
        if episode.season == 1 and mapping.default_tvdb_season.isdigit():
            try:
                offset = int(mapping.episode_offset or "0")
            except ValueError:
                offset = 0
            return int(mapping.default_tvdb_season), episode.index + offset
        return episode.season, episode.index

    def _external_guids(self, anime: AnimeMetadata, mapping: AnimeListMapping | None) -> list[str]:
        guids = self.anime_lists.external_guids(mapping)
        if f"anidb://{anime.aid}" not in guids:
            guids.insert(0, f"anidb://{anime.aid}")
        for mal_id in anime.resources.get("mal", ()):
            guids.append(f"myanimelist://{mal_id}")
        return list(dict.fromkeys(guids))

    def _rating_key(self, aid: str, item_type: str, *, season: int | None = None, episode: int | None = None) -> str:
        if item_type == "movie":
            return f"anidb-{aid}-movie"
        if item_type == "season":
            return f"anidb-{aid}-s{season or 0}"
        if item_type == "episode":
            return f"anidb-{aid}-s{season or 0}e{episode or 0}"
        return f"anidb-{aid}"

    def _guid(self, item_type: str, key: str) -> str:
        return f"{self.config.provider_identifier}://{item_type}/{key}"

    @staticmethod
    def _decode_asset_url(token: str) -> str:
        padding = "=" * (-len(token) % 4)
        return base64.urlsafe_b64decode((token + padding).encode("ascii")).decode("utf-8")
