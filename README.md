# GCP IAM Observatory

New IAM permissions as an early signal for unreleased Google Cloud functionality.

A permission usually exists in the IAM catalog before the product it belongs to is
documented or announced, so watching the catalog is a way to see what Google is
building. This dashboard turns the daily catalog snapshots collected by
[gcp-permissions-checker](https://github.com/exe-cut3/gcp-permissions-checker) into
something you can actually read.

## Why this exists

The raw data is unusable directly. One real launch — `serviceextensions` going live
on 2026-09-04 — produces 34 rows: 3 permissions, 3 predefined roles, and 28
role↔permission mappings that follow mechanically from those roles. Reported as rows,
a month of GCP activity is a couple of thousand lines of mostly nothing.

So the index groups changes into **events** and ranks them by **novelty** rather than
by security severity, because the catalog is good at telling you something new exists,
not that something is dangerous:

| Tier | Meaning | Roughly per month |
|---|---|---|
| 0 | A service appears for the first time | ~2 |
| 1 | A known service exposes a new resource type | ~24 |
| 2 | A known resource gains new verbs | ~35 events |

Tiers 0 and 1 are shown by default — about 25 cards a month. Tier 2 is one click away.
Role-mapping churn is never a headline; it lives inside a card as evidence.

## Three questions, three views

- **Feed** — what is new in a window, grouped by day, collapsed into events
- **Lookup** — when did a given permission first appear, and what landed alongside it
- **Cadence** — Google's release rhythm by weekday and by month

Opening a card adds two layers of detail, neither of which needs credentials:

- **Permission metadata** — title, launch stage and custom-role support for each
  permission, read from `permissions_metadata.jsonl` in the collector once it records
  one. Non-GA stages are flagged on the card.
- **API explorer** — the service's Discovery document (Google's equivalent of an
  OpenAPI spec) shown as endpoints, parameters and schemas, each method joined to the
  permission it needs, with a ready-to-copy `curl` that fetches its own token from
  `gcloud`, or the same request as raw HTTP for a proxy such as Caido or Burp.

## Running it

Everything runs in Docker. The only input is a local clone of the collector repo,
mounted read-only.

```bash
docker compose up --build
```

Then open <http://localhost:8080>.

`docker-compose.yml` expects `../gcp-permissions-checker` next to this directory.
The first run reads the whole history and fetches role data; later runs reuse a cached
diff log and start in seconds. `indexer/` and `web/` are mounted, so after editing
either, `docker compose restart` is enough — no rebuild.

To build without touching the network, set `INDEXER_ARGS: "--no-enrich"`. The index
still builds; cards just lose their role context.

## What the indexer writes

| File | Contents |
|---|---|
| `events.json` | Ranked, clustered events — the feed |
| `lookup.json` | Permission → first seen / removed, loaded on demand |
| `services.json` | Roles per service, referenced by events rather than repeated |
| `cadence.json` | Weekday and monthly statistics |
| `apis.json` | Discovery status per explored service |
| `detail/<service>.json` | Metadata, methods and schemas for one service, loaded on demand |
| `meta.json` | Coverage, collection gaps, excluded snapshots |

## Honesty about the data

The record is only as good as the collection behind it, and the dashboard says so
rather than papering over it:

- **Permissions that predate the record** cannot be dated. The catalog starts
  2024-06-06; anything present then is reported as "already existed", not as having
  appeared that day.
- **Collection gaps** are surfaced. The collector was sporadic before Feb 2026, with
  gaps of 263 and 308 days. A permission first seen at the end of a gap may have
  appeared any time inside it, and Lookup says so.
- **Weekday attribution** is computed only where snapshots are dense, since one
  snapshot closing a long gap would otherwise dump months of change onto a single
  weekday. The collector only commits when something changed, so quiet stretches are
  indistinguishable from outages in git history; observed quiet periods run to ~12
  days and real outages to 18+, so 14 days is the dividing line.
- **Corrupt snapshots are excluded.** A snapshot that loses more than half the catalog
  is treated as a collection failure. This has happened once for real, when the list
  was briefly replaced by a two-line test fixture.

- **API status is what an anonymous caller sees.** A 403 means the service's host
  exists but will not hand its Discovery document to a caller without an API key; on
  its own that does not prove the API is private. "No API host" means nothing answers at
  `<service>.googleapis.com`, which also happens when permissions guard an API
  published under another name.
- **Method-to-permission links are labelled by source.** Most come from iam-dataset's
  method map. That map lags new services, so for those the link is inferred by matching
  the method name against the catalog, and marked as inferred.

Every gap permanently costs resolution in the historical record, which is the real
reason the collector must not stop.
