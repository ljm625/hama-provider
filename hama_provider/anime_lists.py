from __future__ import annotations

from dataclasses import dataclass
import logging
import xml.etree.ElementTree as ET

from .http_client import HttpClient

LOG = logging.getLogger("hama_provider.anime_lists")

SCUDLEE_MASTER = "https://raw.githubusercontent.com/Anime-Lists/anime-lists/master/anime-list-master.xml"
SCUDLEE_MOVIESET = "https://raw.githubusercontent.com/Anime-Lists/anime-lists/master/anime-movieset-list.xml"
MONTH = 30 * 24 * 60 * 60


@dataclass(frozen=True)
class AnimeListMapping:
    anidb_id: str
    tvdb_id: str = ""
    tmdb_id: str = ""
    imdb_id: str = ""
    name: str = ""
    default_tvdb_season: str = "1"
    episode_offset: str = "0"
    studio: str = ""
    director: str = ""
    writer: str = ""
    genres: tuple[str, ...] = ()
    episode_map: tuple[tuple[int, int, int, int], ...] = ()


class AnimeListsRepository:
    def __init__(self, client: HttpClient) -> None:
        self.client = client
        self._loaded = False
        self._by_anidb: dict[str, AnimeListMapping] = {}
        self._by_tvdb: dict[str, list[AnimeListMapping]] = {}
        self._by_tmdb: dict[str, AnimeListMapping] = {}
        self._by_imdb: dict[str, AnimeListMapping] = {}
        self._collections: dict[str, str] = {}

    def ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._load_master()
        self._load_movie_sets()
        self._loaded = True

    def find_by_anidb(self, anidb_id: str) -> AnimeListMapping | None:
        self.ensure_loaded()
        return self._by_anidb.get(str(anidb_id))

    def find_by_external(self, source: str, value: str) -> AnimeListMapping | None:
        self.ensure_loaded()
        source = source.lower()
        value = value.strip()
        if source.startswith("anidb"):
            return self._by_anidb.get(value)
        if source.startswith("tvdb"):
            mappings = self._by_tvdb.get(value) or []
            return self._primary_mapping(mappings)
        if source == "tmdb":
            return self._by_tmdb.get(value)
        if source == "imdb":
            return self._by_imdb.get(value)
        return None

    def related_for_tvdb(self, tvdb_id: str) -> list[AnimeListMapping]:
        self.ensure_loaded()
        return list(self._by_tvdb.get(str(tvdb_id), []))

    def collection_for_anidb(self, anidb_id: str) -> str:
        self.ensure_loaded()
        return self._collections.get(str(anidb_id), "")

    @staticmethod
    def external_guids(mapping: AnimeListMapping | None) -> list[str]:
        if not mapping:
            return []
        guids = [f"anidb://{mapping.anidb_id}"]
        if mapping.tvdb_id and mapping.tvdb_id.isdigit():
            guids.append(f"tvdb://{mapping.tvdb_id}")
        if mapping.tmdb_id:
            guids.append(f"tmdb://{mapping.tmdb_id}")
        if mapping.imdb_id:
            guids.append(f"imdb://{mapping.imdb_id}")
        return guids

    def _load_master(self) -> None:
        LOG.info("Loading anime-list master")
        root = ET.fromstring(self.client.fetch_xml_bytes(SCUDLEE_MASTER, ttl=MONTH))
        for anime in root.findall(".//anime"):
            mapping = self._mapping_from_element(anime)
            if not mapping.anidb_id:
                continue
            self._by_anidb[mapping.anidb_id] = mapping
            if mapping.tvdb_id:
                self._by_tvdb.setdefault(mapping.tvdb_id, []).append(mapping)
            if mapping.tmdb_id:
                self._by_tmdb[mapping.tmdb_id] = mapping
            if mapping.imdb_id:
                self._by_imdb[mapping.imdb_id] = mapping
        LOG.info("Loaded %d anime-list mappings", len(self._by_anidb))

    def _load_movie_sets(self) -> None:
        try:
            root = ET.fromstring(self.client.fetch_xml_bytes(SCUDLEE_MOVIESET, ttl=MONTH))
        except Exception as exc:
            LOG.warning("Could not load anime movie sets: %s", exc)
            return
        for set_node in root.findall(".//set"):
            title = self._first_text(set_node, "titles/title") or self._first_text(set_node, "title")
            for anime in set_node.findall(".//anime"):
                anidb_id = anime.get("anidbid", "")
                if anidb_id and title:
                    self._collections[anidb_id] = title

    def _mapping_from_element(self, anime: ET.Element) -> AnimeListMapping:
        default_season = anime.get("defaulttvdbseason") or "1"
        if default_season == "a":
            default_season = "1"
        genres = tuple(
            sorted(
                {
                    (genre.text or "").strip()
                    for genre in anime.findall("supplemental-info/genre")
                    if (genre.text or "").strip()
                }
            )
        )
        return AnimeListMapping(
            anidb_id=anime.get("anidbid", "").strip(),
            tvdb_id=anime.get("tvdbid", "").strip(),
            tmdb_id=anime.get("tmdbid", "").strip(),
            imdb_id=anime.get("imdbid", "").strip(),
            name=self._first_text(anime, "name"),
            default_tvdb_season=default_season,
            episode_offset=anime.get("episodeoffset") or "0",
            studio=self._first_text(anime, "supplemental-info/studio"),
            director=self._first_text(anime, "supplemental-info/director"),
            writer=self._first_text(anime, "supplemental-info/credits"),
            genres=genres,
            episode_map=tuple(self._episode_map(anime)),
        )

    @staticmethod
    def _episode_map(anime: ET.Element) -> list[tuple[int, int, int, int]]:
        mappings: list[tuple[int, int, int, int]] = []
        for node in anime.findall("mapping-list/mapping"):
            anidb_season = _to_int(node.get("anidbseason"), 1)
            tvdb_season = _to_int(node.get("tvdbseason"), anidb_season)
            offset = _to_int(node.get("offset"), 0)
            start = _to_int(node.get("start"), -1)
            end = _to_int(node.get("end"), -1)
            if start >= 0 and end >= start:
                for anidb_episode in range(start, end + 1):
                    mappings.append((anidb_season, anidb_episode, tvdb_season, anidb_episode + offset))
            for item in filter(None, (node.text or "").strip(";").split(";")):
                if "-" not in item:
                    continue
                anidb_episode, tvdb_episode = item.split("-", 1)
                if anidb_episode.isdigit() and tvdb_episode.isdigit():
                    mappings.append((anidb_season, int(anidb_episode), tvdb_season, int(tvdb_episode)))
        return mappings

    @staticmethod
    def _primary_mapping(mappings: list[AnimeListMapping]) -> AnimeListMapping | None:
        if not mappings:
            return None
        for mapping in mappings:
            if mapping.default_tvdb_season == "1" and mapping.episode_offset in {"", "0"}:
                return mapping
        return mappings[0]

    @staticmethod
    def _first_text(element: ET.Element, path: str) -> str:
        child = element.find(path)
        return (child.text or "").strip() if child is not None and child.text else ""


def _to_int(value: str | None, default: int) -> int:
    try:
        return int(value) if value not in (None, "") else default
    except ValueError:
        return default
