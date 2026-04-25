from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
import logging
import re
import string
import unicodedata
import xml.etree.ElementTree as ET

from .config import Config
from .http_client import HttpClient

LOG = logging.getLogger("hama_provider.anidb")

ANIDB_TITLES = "https://anidb.net/api/anime-titles.xml.gz"
ANIDB_HTTP_API = "http://api.anidb.net:9001/httpapi?request=anime&client=hama&clientver=1&protover=1&aid={aid}"
ANIDB_IMAGE_BASE = "https://cdn.anidb.net/images/main/"
TWO_WEEKS = 14 * 24 * 60 * 60
SIX_DAYS = 6 * 24 * 60 * 60
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"

RESTRICTED_GENRE = {
    "18 restricted": "X",
    "pornography": "X",
    "tv censoring": "TV-MA",
    "borderline porn": "TV-MA",
}


@dataclass(frozen=True)
class TitleEntry:
    aid: str
    title: str
    title_type: str
    language: str
    normalized: str
    folded: str


@dataclass(frozen=True)
class MatchCandidate:
    aid: str
    title: str
    score: int
    language: str = ""
    title_type: str = ""


@dataclass(frozen=True)
class PersonRole:
    name: str
    role: str = ""
    photo: str = ""


@dataclass(frozen=True)
class EpisodeMetadata:
    season: int
    index: int
    title: str
    originally_available_at: str = ""
    summary: str = ""
    rating: float | None = None
    duration: int | None = None
    directors: tuple[str, ...] = ()
    writers: tuple[str, ...] = ()
    producers: tuple[str, ...] = ()


@dataclass(frozen=True)
class AnimeMetadata:
    aid: str
    title: str
    original_title: str
    anime_type: str = ""
    originally_available_at: str = ""
    summary: str = ""
    rating: float | None = None
    picture: str = ""
    genres: tuple[str, ...] = ()
    content_rating: str = ""
    studio: str = ""
    directors: tuple[str, ...] = ()
    writers: tuple[str, ...] = ()
    producers: tuple[str, ...] = ()
    roles: tuple[PersonRole, ...] = ()
    episodes: tuple[EpisodeMetadata, ...] = ()
    resources: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def year(self) -> int | None:
        if self.originally_available_at[:4].isdigit():
            return int(self.originally_available_at[:4])
        return None

    @property
    def is_movie(self) -> bool:
        return self.anime_type.lower() == "movie" or len([ep for ep in self.episodes if ep.season == 1]) == 1


class AniDBRepository:
    def __init__(self, config: Config, client: HttpClient) -> None:
        self.config = config
        self.client = client
        self._titles_loaded = False
        self._title_entries: list[TitleEntry] = []
        self._titles_by_aid: dict[str, list[TitleEntry]] = {}

    def ensure_titles(self) -> None:
        if self._titles_loaded:
            return
        LOG.info("Loading AniDB title database")
        root = ET.fromstring(self.client.fetch_xml_bytes(ANIDB_TITLES, ttl=TWO_WEEKS))
        entries: list[TitleEntry] = []
        by_aid: dict[str, list[TitleEntry]] = {}
        for anime in root.findall("anime"):
            aid = anime.get("aid", "")
            for title in anime.findall("title"):
                text = (title.text or "").strip()
                if not aid or not text:
                    continue
                entry = TitleEntry(
                    aid=aid,
                    title=text.replace("`", "'"),
                    title_type=title.get("type", ""),
                    language=title.get(XML_LANG, ""),
                    normalized=normalize_title(text),
                    folded=fold_title(text),
                )
                entries.append(entry)
                by_aid.setdefault(aid, []).append(entry)
        self._title_entries = entries
        self._titles_by_aid = by_aid
        self._titles_loaded = True
        LOG.info("Loaded %d AniDB titles for %d anime", len(entries), len(by_aid))

    def title_for_aid(self, aid: str) -> str:
        self.ensure_titles()
        return self._choose_title_entries(self._titles_by_aid.get(str(aid), []))[0] or f"AniDB {aid}"

    def search(self, query: str, *, limit: int) -> list[MatchCandidate]:
        self.ensure_titles()
        normalized = normalize_title(query)
        folded = fold_title(query)
        if not normalized and not folded:
            return []
        words = [word for word in normalized.split() if len(word) > 2]
        best: dict[str, MatchCandidate] = {}
        for entry in self._title_entries:
            score = self._score(normalized, folded, words, entry)
            if score < 35:
                continue
            current = best.get(entry.aid)
            if current is None or score > current.score:
                best[entry.aid] = MatchCandidate(
                    aid=entry.aid,
                    title=entry.title,
                    score=score,
                    language=entry.language,
                    title_type=entry.title_type,
                )
        return sorted(best.values(), key=lambda item: item.score, reverse=True)[:limit]

    def fetch_metadata(self, aid: str) -> AnimeMetadata:
        root = ET.fromstring(self.client.fetch_xml_bytes(ANIDB_HTTP_API.format(aid=aid), ttl=SIX_DAYS))
        if root.tag.lower() == "error":
            raise RuntimeError(f"AniDB returned error for aid {aid}: {''.join(root.itertext()).strip()}")
        title, original_title = self._choose_title_elements(root.findall("titles/title"))
        if not title:
            title = self.title_for_aid(aid)
        creators = self._creators(root)
        genres, content_rating = self._genres(root)
        roles = self._roles(root)
        picture_name = child_text(root, "picture")
        return AnimeMetadata(
            aid=str(aid),
            title=title,
            original_title=original_title or title,
            anime_type=child_text(root, "type"),
            originally_available_at=child_text(root, "startdate"),
            summary=summary_sanitizer(child_text(root, "description")),
            rating=parse_float(child_text(root, "ratings/permanent")),
            picture=f"{ANIDB_IMAGE_BASE}{picture_name}" if picture_name else "",
            genres=tuple(sorted(genres)),
            content_rating=content_rating,
            studio=creators.get("studio", ("",))[0] if creators.get("studio") else "",
            directors=creators.get("directors", ()),
            writers=creators.get("writers", ()),
            producers=creators.get("producers", ()),
            roles=tuple(roles),
            episodes=tuple(self._episodes(root, creators)),
            resources=self._resources(root),
        )

    @staticmethod
    def _score(normalized_query: str, folded_query: str, words: list[str], entry: TitleEntry) -> int:
        if folded_query and entry.folded == folded_query:
            score = 100
        elif folded_query and (entry.folded.startswith(folded_query) or folded_query.startswith(entry.folded)):
            score = 92
        elif folded_query and folded_query in entry.folded:
            score = 88
        elif normalized_query and entry.normalized:
            title = entry.normalized
            if title == normalized_query:
                score = 100
            elif title.startswith(normalized_query) or normalized_query.startswith(title):
                score = 92
            elif normalized_query in title:
                score = 88
            else:
                score = int(SequenceMatcher(None, normalized_query, title).ratio() * 100)
                if words:
                    matched = sum(1 for word in words if word in title)
                    score = max(score, int(100 * matched / len(words)) - 5)
        else:
            score = int(SequenceMatcher(None, folded_query, entry.folded).ratio() * 100) if folded_query and entry.folded else 0
        type_penalty = {"main": 0, "official": 1, "syn": 3, "synonym": 3, "short": 8, "card": 8}
        score -= type_penalty.get(entry.title_type, 4)
        return max(0, score)

    def _choose_title_elements(self, titles: list[ET.Element]) -> tuple[str, str]:
        entries = [
            TitleEntry(
                aid="",
                title=(title.text or "").strip().replace("`", "'"),
                title_type=title.get("type", ""),
                language=title.get(XML_LANG, ""),
                normalized=normalize_title(title.text or ""),
                folded=fold_title(title.text or ""),
            )
            for title in titles
            if (title.text or "").strip()
        ]
        return self._choose_title_entries(entries)

    def _choose_title_entries(self, entries: list[TitleEntry]) -> tuple[str, str]:
        if not entries:
            return "", ""
        languages = self.config.languages
        type_priority = {"main": 0, "official": 1, "syn": 3, "synonym": 3, "short": 5, "card": 5, "": 6}
        main = next((entry.title for entry in entries if entry.title_type == "main"), "")

        def key(entry: TitleEntry) -> tuple[int, int]:
            if entry.title_type == "main" and "main" in languages:
                lang_rank = languages.index("main")
            elif entry.language in languages:
                lang_rank = languages.index(entry.language)
            else:
                lang_rank = len(languages) + 1
            return (lang_rank, type_priority.get(entry.title_type, 6))

        chosen = sorted(entries, key=key)[0].title
        return chosen, main or chosen

    def _episode_title(self, titles: list[ET.Element]) -> str:
        languages = self.config.episode_languages
        entries = [
            (
                languages.index(title.get(XML_LANG, "")) if title.get(XML_LANG, "") in languages else len(languages),
                (title.text or "").strip().replace("`", "'"),
            )
            for title in titles
            if (title.text or "").strip()
        ]
        if not entries:
            return ""
        return sorted(entries, key=lambda item: item[0])[0][1]

    def _episodes(self, root: ET.Element, creators: dict[str, tuple[str, ...]]) -> list[EpisodeMetadata]:
        episodes: list[EpisodeMetadata] = []
        for episode in root.findall("episodes/episode"):
            epno = episode.find("epno")
            if epno is None or not (epno.text or "").strip():
                continue
            season, index = episode_number(epno.get("type", ""), epno.text or "")
            if index < 0:
                continue
            length = child_text(episode, "length")
            duration = int(length) * 60 * 1000 if length.isdigit() else None
            episodes.append(
                EpisodeMetadata(
                    season=season,
                    index=index,
                    title=self._episode_title(episode.findall("title")),
                    originally_available_at=child_text(episode, "airdate"),
                    summary=summary_sanitizer(child_text(episode, "summary")),
                    rating=parse_float(child_text(episode, "rating")),
                    duration=duration,
                    directors=creators.get("directors", ()),
                    writers=creators.get("writers", ()),
                    producers=creators.get("producers", ()),
                )
            )
        return sorted(episodes, key=lambda item: (item.season, item.index))

    def _genres(self, root: ET.Element) -> tuple[set[str], str]:
        genres: set[str] = set()
        content_rating = ""
        for tag in root.findall("tags/tag"):
            name = child_text(tag, "name")
            if not name:
                continue
            weight = int(tag.get("weight", "0") or "0")
            include = bool(tag.get("infobox"))
            if self.config.include_weighted_genres and weight >= self.config.min_genre_weight:
                include = True
            lower = name.lower()
            if lower in RESTRICTED_GENRE:
                content_rating = RESTRICTED_GENRE[lower]
                if not self.config.include_adult:
                    include = False
            if include:
                genres.add(string.capwords(name, "-"))
        return genres, content_rating

    @staticmethod
    def _creators(root: ET.Element) -> dict[str, tuple[str, ...]]:
        creator_tags = {
            "Animation Work": "studio",
            "Work": "studio",
            "Direction": "directors",
            "Series Composition": "producers",
            "Original Work": "writers",
            "Script": "writers",
            "Screenplay": "writers",
        }
        values: dict[str, list[str]] = {}
        for creator in root.findall("creators/name"):
            name = (creator.text or "").strip()
            if not name:
                continue
            key = creator_tags.get(creator.get("type", ""))
            if key:
                values.setdefault(key, []).append(name)
        return {key: tuple(dict.fromkeys(items)) for key, items in values.items()}

    @staticmethod
    def _roles(root: ET.Element) -> list[PersonRole]:
        roles: list[PersonRole] = []
        for character in root.findall("characters/character"):
            if character.get("type") not in {"secondary cast in", "main character in"}:
                continue
            role = child_text(character, "name")
            seiyuu = character.find("seiyuu")
            name = (seiyuu.text or "").strip() if seiyuu is not None and seiyuu.text else ""
            picture = seiyuu.get("picture", "") if seiyuu is not None else ""
            if name and role:
                roles.append(PersonRole(name=name, role=role, photo=f"{ANIDB_IMAGE_BASE}{picture}" if picture else ""))
        return roles

    @staticmethod
    def _resources(root: ET.Element) -> dict[str, tuple[str, ...]]:
        type_names = {"1": "ann", "2": "mal", "3": "animeNfo"}
        values: dict[str, list[str]] = {}
        for resource in root.findall("resources/resource"):
            key = type_names.get(resource.get("type", ""))
            if not key:
                continue
            for entity in resource.findall("externalentity"):
                identifier = child_text(entity, "identifier")
                if identifier:
                    values.setdefault(key, []).append(identifier)
        return {key: tuple(dict.fromkeys(items)) for key, items in values.items()}


def normalize_title(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def fold_title(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").replace("`", "'")
    return re.sub(r"\s+", " ", value.casefold()).strip()


def child_text(element: ET.Element, path: str) -> str:
    child = element.find(path)
    return (child.text or "").strip() if child is not None and child.text else ""


def parse_float(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def episode_number(ep_type: str, raw_value: str) -> tuple[int, int]:
    value = raw_value.strip()
    if ep_type == "1" and value.isdigit():
        return 1, int(value)
    specials = {"S": 0, "C": 100, "T": 200, "P": 300, "O": 400}
    prefix = value[:1]
    suffix = value[1:]
    if prefix in specials and suffix.isdigit():
        return 0, specials[prefix] + int(suffix)
    if value.isdigit():
        return 0, int(value)
    return 0, -1


def summary_sanitizer(summary: str) -> str:
    summary = (summary or "").replace("`", "'")
    summary = re.sub(r"https?://anidb\.net/[a-z]{1,2}[0-9]+ \[(?P<text>.+?)\]", r"\g<text>", summary)
    summary = re.sub(r"https?://anidb\.net/[a-z]+/[0-9]+ \[(?P<text>.+?)\]", r"\g<text>", summary)
    summary = re.sub(r"^(\*|--|~) .*", "", summary, flags=re.MULTILINE)
    summary = re.sub(r"\n(Source|Note|Summary):.*", "", summary, flags=re.DOTALL)
    summary = re.sub(r"\n\n+", "\n\n", summary, flags=re.DOTALL)
    return summary.strip(" \n")
