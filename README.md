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

## How it stays current

| When (UTC) | Where | What |
|---|---|---|
| 23:00 | gcp-permissions-checker workflow | Records every permission Google lists, with title and launch stage. On quiet days it commits a heartbeat so GitHub never disables the schedule. |
| 01:00 | Daily catalog workflow (this repo) | Rebuilds the index from the collector's full history, fetches every service's API spec, commits the normalized specs to `specs/`, records what changed and publishes the index as the `index-latest` release. |
| every 6 h | Your Docker containers | Download the latest published index; the MCP server reloads it. |

`specs/` is the durable history of API definitions. `specs/apis/<service>.jsonl` holds
each service's spec as last seen, and `specs/changes.jsonl` lists new and removed methods,
new API versions and specs that became public, dated by the day they were first seen. A
Discovery document can only be fetched as it is today, so this history cannot be
recreated later.

The workflow fails loudly (GitHub's failure email, plus Telegram when `TELEGRAM_TOKEN`
and `TELEGRAM_TO` secrets are set) when the build breaks, or when the collector's
workflow has stopped or not succeeded for two days.

## Running it

```bash
docker compose up --build
```

Dashboard at <http://localhost:8080>, MCP server at `http://localhost:8081/mcp`.

By default the containers download the published index, so nothing else is needed
locally. To work on the indexer, set `INDEX_URL: ""` in `docker-compose.yml`: the
container then builds from `../gcp-permissions-checker` next to this directory.
`indexer/`, `web/` and `mcp_server/` are mounted, so `docker compose restart` picks up
edits without a rebuild. In build mode, `INDEXER_ARGS: "--no-enrich"` skips fetching role
data.

## For AI agents (MCP)

The same index is served to AI agents as a read-only MCP server, so an agent can learn
how Google Cloud access fits together and look up any endpoint before it touches your
projects. `docker compose up` starts it next to the dashboard at
`http://localhost:8081/mcp`. To add it to Claude Code:

```bash
claude mcp add --transport http gcp-iam http://localhost:8081/mcp
```

| Tool | Answers |
|---|---|
| `catalog_overview` | What the catalog holds, how fresh it is, Google's release rhythm |
| `search` | Task wording to method ids, permissions, roles or services |
| `get_service` | A service's API status, resources, roles and recent changes |
| `get_method` | One endpoint: URL, parameters, body and response fields, required permissions with the narrowest roles that grant them, raw HTTP and curl |
| `get_schema` | Deeper request and response types |
| `get_permission` | When a permission appeared, which methods need it, which roles grant it |
| `get_role` | A role's permissions, by service |
| `whats_new` | Recent catalog changes by novelty tier |

The server holds no credentials and never calls Google. To change infrastructure an agent
uses its own tools (gcloud, or the request templates) with your credentials, so each
change goes through your approval. It listens on loopback only and rejects requests
whose Host header is not local.

## What the indexer writes

| File | Contents |
|---|---|
| `events.json` | Ranked, clustered events — the feed |
| `lookup.json` | Permission → first seen / removed, loaded on demand |
| `services.json` | Roles per service, referenced by events rather than repeated |
| `cadence.json` | Weekday and monthly statistics |
| `apis.json` | Discovery status per explored service |
| `detail/<service>.json` | Metadata, methods and schemas for one service, loaded on demand |
| `roles.json` | Every predefined role with its permissions, for least-privilege lookups |
| `methods.json` | Every API method in one compact table, for search |
| `api_changes.json` | Every recorded API spec change, from `specs/changes.jsonl` |
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

- **The catalog is project-scoped.** It lists what Google reports as testable on a
  project, so organization- and folder-level permissions are mostly absent. On 2026-01-30
  five organization-level services (accesscontextmanager, assuredworkloads,
  cloudcontrolspartner, policyremediatormanager, riskmanager) dropped out at once. They
  still exist; the feed shows them as removed only because they stopped being testable on
  a project. The MCP server still answers role questions for such permissions from the
  upstream role data.
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
