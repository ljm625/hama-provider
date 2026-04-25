# HAMA Remote Metadata Provider

This is a first-pass Python 3 port of the HAMA.bundle metadata path to Plex's
new HTTP Metadata Provider API.

The legacy bundle runs inside Plex's plugin runtime. This service runs outside
Plex, can be hosted on another machine, and controls all upstream network
traffic itself. Set `HAMA_HTTP_PROXY` and `HAMA_HTTPS_PROXY` to force AniDB,
anime-list, and asset fetches through a proxy.

## What Works

- Plex MediaProvider root response.
- Match endpoint backed by the AniDB title database.
- Forced IDs in titles such as `Show Name [anidb-123]`, `[tvdb-123]`,
  `[tmdb-123]`, and `[imdb-tt123]`.
- anime-list lookup from TVDB/TMDB/IMDb IDs back to AniDB IDs.
- Metadata endpoint for movie, show, season, and episode items.
- Children and grandchildren endpoints with Plex paging headers.
- Poster/image proxy endpoint, so Plex can fetch artwork through this service.
- Registration helper for PMS metadata provider and provider group endpoints.

## Known Gaps

This is intentionally an MVP port, not a byte-for-byte clone of HAMA.bundle.
The following legacy sources are not fully merged yet:

- TheTVDB v2/v4
- TMDB
- FanartTV
- MAL and AniList enrichment
- OMDb
- TVTunes
- Local file metadata and Plex-local metadata

AniDB and anime-list are the core pieces needed to prove the remote provider
shape and proxy behavior. The other sources can be added behind the same
service interface.

## Run Locally

```sh
cd hama-provider
python3 -m hama_provider
```

Open:

```text
http://127.0.0.1:34567/
```

## Proxy

Use explicit service-level proxy settings:

```sh
export HAMA_HTTP_PROXY=http://127.0.0.1:7890
export HAMA_HTTPS_PROXY=http://127.0.0.1:7890
python3 -m hama_provider
```

If those are not set, the service also honors standard `HTTP_PROXY`,
`HTTPS_PROXY`, `http_proxy`, and `https_proxy` environment variables. PAC files
are not evaluated by this service.

## Public URL

If the provider is behind a reverse proxy, set both the local path prefix and
the public base URL:

```sh
export HAMA_PATH_PREFIX=/hama
export HAMA_BASE_URL=https://metadata.example.com/hama
python3 -m hama_provider
```

Register the URL that Plex can reach:

```sh
python3 -m hama_provider.register \
  --plex-url http://127.0.0.1:32400 \
  --token YOUR_PLEX_TOKEN \
  --provider-url https://metadata.example.com/hama
```

The helper calls:

- `POST /media/providers/metadata?uri=...`
- `POST /media/providers/metadata/group?title=...&primaryIdentifier=...`

Use the returned `MetadataAgentProviderGroup` id as
`metadataAgentProviderGroupId` when creating or editing a Plex library.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `HAMA_HOST` | `0.0.0.0` | Listen address |
| `HAMA_PORT` | `34567` | Listen port |
| `HAMA_PATH_PREFIX` | empty | Reverse proxy path prefix |
| `HAMA_BASE_URL` | empty | Public URL for generated links |
| `HAMA_PROVIDER_IDENTIFIER` | `tv.plex.agents.custom.zeroqi.hama` | Provider identifier and GUID scheme |
| `HAMA_PROVIDER_TITLE` | `HAMA Remote` | Plex-visible provider title |
| `HAMA_CACHE_DIR` | `.cache` | HTTP cache directory |
| `HAMA_HTTP_PROXY` | env proxy | HTTP upstream proxy |
| `HAMA_HTTPS_PROXY` | env proxy | HTTPS upstream proxy |
| `HAMA_LANGUAGES` | `main,en,ja` | Series title priority |
| `HAMA_EPISODE_LANGUAGES` | `main,en,ja` | Episode title priority |
| `HAMA_MIN_GENRE_WEIGHT` | `400` | Minimum AniDB tag weight |
| `HAMA_INCLUDE_WEIGHTED_GENRES` | `false` | Include weighted non-infobox AniDB tags |
| `HAMA_PROXY_ASSETS` | `true` | Serve artwork through `/asset/...` |
