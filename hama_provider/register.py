from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request

from .config import default_identifier, default_title


def main() -> None:
    parser = argparse.ArgumentParser(description="Register the HAMA remote metadata provider with Plex Media Server.")
    parser.add_argument("--plex-url", required=True, help="PMS base URL, for example http://127.0.0.1:32400")
    parser.add_argument("--token", required=True, help="Plex token")
    parser.add_argument("--provider-url", required=True, help="Provider URL reachable by PMS")
    parser.add_argument("--kind", choices=("tv", "movie", "both"), default="tv", help="Provider type family")
    parser.add_argument("--identifier", help="Provider identifier")
    parser.add_argument("--group-title", help="Provider group title")
    parser.add_argument("--no-group", action="store_true", help="Only register the provider, do not create a provider group")
    args = parser.parse_args()

    identifier = args.identifier or default_identifier(args.kind)
    group_title = args.group_title or default_title(args.kind)
    plex_url = args.plex_url.rstrip("/")
    provider = request(
        "POST",
        f"{plex_url}/media/providers/metadata?{urllib.parse.urlencode({'uri': args.provider_url})}",
        args.token,
    )
    print(json.dumps(provider, indent=2, ensure_ascii=False))

    if not args.no_group:
        group = request(
            "POST",
            f"{plex_url}/media/providers/metadata/group?{urllib.parse.urlencode({'title': group_title, 'primaryIdentifier': identifier})}",
            args.token,
        )
        print(json.dumps(group, indent=2, ensure_ascii=False))


def request(method: str, url: str, token: str) -> object:
    request = urllib.request.Request(
        url,
        method=method,
        headers={
            "Accept": "application/json",
            "X-Plex-Token": token,
            "X-Plex-Product": "HAMA Remote Provider",
            "X-Plex-Client-Identifier": "hama-remote-provider-register",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read().decode("utf-8", "replace")
    except Exception as exc:
        print(f"Request failed: {method} {url}: {exc}", file=sys.stderr)
        raise
    return json.loads(body) if body else {}


if __name__ == "__main__":
    main()
