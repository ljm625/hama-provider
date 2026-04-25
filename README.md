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
- TV/movie provider split with `HAMA_PROVIDER_KIND`.

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

The default provider kind is `tv`, which advertises only TV Show, Season, and
Episode metadata types. This is the compatible shape for combining with Plex
Series.

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

### TV and Movie Providers

Plex provider groups require compatible metadata types. A TV provider should
support show/season/episode only; a movie provider should support movie only.
Running one provider that advertises movie + TV can trigger this Plex error:
`auxiliary provider metadata is not compatible with the primary provider in the group`.

For a TV library:

```sh
export HAMA_PROVIDER_KIND=tv
export HAMA_PROVIDER_IDENTIFIER=tv.plex.agents.custom.zeroqi.hama
export HAMA_PROVIDER_TITLE="HAMA Remote TV"
python3 -m hama_provider
```

For a movie library, run a second instance on another port or path:

```sh
export HAMA_PROVIDER_KIND=movie
export HAMA_PROVIDER_IDENTIFIER=tv.plex.agents.custom.zeroqi.hama.movie
export HAMA_PROVIDER_TITLE="HAMA Remote Movies"
export HAMA_PORT=34568
python3 -m hama_provider
```

The old `both` mode is still available for direct testing, but it is not
recommended for Plex provider groups:

```sh
export HAMA_PROVIDER_KIND=both
```

## Matching Troubleshooting

If the service log only shows `GET /` requests, Plex has only probed the
provider root. That is expected during registration, but it means Plex has not
asked this provider to match anything yet.

A real match call should show:

```text
POST /library/metadata/matches
Match request: ...
```

Then, on the first uncached match, the service should also log an upstream
`HTTP fetch` for the AniDB title database. Later matches may show
`HTTP cache hit` or no upstream fetch if the title database is already loaded
in memory.

Quick direct test:

```sh
curl -s http://127.0.0.1:34567/library/metadata/matches \
  -H 'Content-Type: application/json' \
  -d '{"type":2,"title":"Cowboy Bebop","manual":true}' | jq .
```

Plex will not use a registered provider for a library until the library is
created or edited with `metadataAgentProviderGroupId`. On current PMS builds,
check these endpoints:

```sh
curl -s -H "Accept: application/json" -H "X-Plex-Token: $PLEX_TOKEN" \
  "$PLEX_URL/media/providers/metadata" | jq .

curl -s -H "Accept: application/json" -H "X-Plex-Token: $PLEX_TOKEN" \
  "$PLEX_URL/media/providers/metadata/group" | jq .

curl -s -H "Accept: application/json" -H "X-Plex-Token: $PLEX_TOKEN" \
  "$PLEX_URL/library/sections/$SECTION_ID?includeDetails=1" | jq .
```

When editing an existing library, preserve the existing library `agent`,
`scanner`, `name`, and `language` values and add the returned provider group id
as `metadataAgentProviderGroupId`.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `HAMA_HOST` | `0.0.0.0` | Listen address |
| `HAMA_PORT` | `34567` | Listen port |
| `HAMA_PATH_PREFIX` | empty | Reverse proxy path prefix |
| `HAMA_BASE_URL` | empty | Public URL for generated links |
| `HAMA_PROVIDER_KIND` | `tv` | `tv`, `movie`, or `both` |
| `HAMA_PROVIDER_IDENTIFIER` | `tv.plex.agents.custom.zeroqi.hama` | Provider identifier and GUID scheme |
| `HAMA_PROVIDER_TITLE` | `HAMA Remote TV` | Plex-visible provider title |
| `HAMA_CACHE_DIR` | `.cache` | HTTP cache directory |
| `HAMA_HTTP_PROXY` | env proxy | HTTP upstream proxy |
| `HAMA_HTTPS_PROXY` | env proxy | HTTPS upstream proxy |
| `HAMA_LANGUAGES` | `main,en,ja` | Series title priority |
| `HAMA_EPISODE_LANGUAGES` | `main,en,ja` | Episode title priority |
| `HAMA_MIN_GENRE_WEIGHT` | `400` | Minimum AniDB tag weight |
| `HAMA_INCLUDE_WEIGHTED_GENRES` | `false` | Include weighted non-infobox AniDB tags |
| `HAMA_PROXY_ASSETS` | `true` | Serve artwork through `/asset/...` |
