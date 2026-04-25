from __future__ import annotations

from typing import Any


TYPE_NUMBERS = {
    "movie": 1,
    "show": 2,
    "season": 3,
    "episode": 4,
}

TYPE_NAMES = {value: key for key, value in TYPE_NUMBERS.items()}


def media_container(identifier: str, metadata: list[dict[str, Any]], *, offset: int = 0, total_size: int | None = None) -> dict[str, Any]:
    total = len(metadata) if total_size is None else total_size
    return {
        "MediaContainer": {
            "offset": offset,
            "totalSize": total,
            "identifier": identifier,
            "size": len(metadata),
            "Metadata": metadata,
        }
    }


def image_container(identifier: str, images: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "MediaContainer": {
            "offset": 0,
            "totalSize": len(images),
            "identifier": identifier,
            "size": len(images),
            "Image": images,
        }
    }


def tag_items(values: tuple[str, ...] | list[str]) -> list[dict[str, str]]:
    return [{"tag": value} for value in values if value]


def guid_items(values: list[str]) -> list[dict[str, str]]:
    return [{"id": value} for value in values if value]
