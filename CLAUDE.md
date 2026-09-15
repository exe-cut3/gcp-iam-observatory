# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A personal research toolkit (GitHub owner **exe-cut3**) built on one idea: **a new GCP IAM permission is an early signal of new, often unannounced Google Cloud functionality.** It records Google's IAM permission catalog daily, works out what changed, fetches the API specs behind it, and serves the result to people (a dashboard) and to AI agents (an MCP server), so agents can understand and maintain the user's GCP infrastructure.

This repo is the hub. Locally it sits next to three sibling repos in `..`:

| Repo | Role | Status |
|---|---|---|
| `gcp-permissions-checker` | **Collector**: GitHub Actions job (daily 23:00 UTC) writing `permissions.txt` and `permissions_metadata.jsonl`; also a recon CLI, `gcp_perm_checker.py --service-account key.json` or `--token T --project P`, that tests what a credential can do. The **git history of `permissions.txt` (since 2024-06-06) is the only durable data record**; everything else is derived and rebuildable. | active |
| `gcp-iam-observatory` (this repo) | **The system being built**: indexer, dashboard, MCP server, daily workflow, API spec history. Replaces both legacy dashboards. | live since 2026-09-15 |
| `gcp-permissions-watchdog` | Legacy dashboard over the collector. Its workflow is `disabled_inactivity` since 2026-07-18 because it never commits. | to retire |
| `gcp-iam-changelog` | Legacy dashboard over the third-party `iann0036/iam-dataset`; still runs daily. | to retire |

## Working rules from the user

- Build, run and test everything in **Docker** (Docker Desktop, compose v2), not on the Windows host.
- GitHub: act **only as `exe-cut3`**, the account that owns these repos. Do not use or add any other GitHub account on this machine.
- Commit locally; push only when the user asks.
- Build what was asked. Mention possible extras in a sentence rather than building them.
- Collection must never stop: the user may be away for months and expects to analyse that period on return, and every missed day permanently costs dating precision. A scheduled GitHub workflow that does not commit for 60 days is disabled, so every scheduled workflow commits daily (a heartbeat on quiet days).
- Report data honestly: surface gaps, inferred values and uncertainty instead of smoothing them over.

## Commands

From this repo. There is no linter or formatter configured; tests use `unittest`.

```bash
docker compose up -d --build                     # dashboard http://localhost:8080, MCP http://localhost:8081/mcp
docker compose run --rm --no-deps --entrypoint python observatory -m unittest discover -s tests -t .
docker compose run --rm --no-deps --entrypoint python observatory -m unittest tests.test_catalog.Search.test_service_filter
docker compose logs --no-log-prefix observatory  # index build / sync output
claude mcp add --transport http gcp-iam http://localhost:8081/mcp

# Build the index by hand, as the daily workflow does (writes to throwaway dirs)
docker compose run --rm --no-deps --entrypoint python observatory -m indexer.build_index \
  --collector-repo /data/collector --output-dir /tmp/dist --cache-dir /app/.cache --specs-dir /tmp/specs
```

- The `observatory` container has two modes. With `INDEX_URL` set (the default in `docker-compose.yml`) it downloads the published `index-latest` release every `SYNC_INTERVAL_SECONDS`. With `INDEX_URL: ""` it builds from `../gcp-permissions-checker`, mounted read-only.
- `indexer/`, `web/`, `mcp_server/` and `tests/` are bind-mounted, so `docker compose restart` picks up edits. Changes to `entrypoint.sh`, `Dockerfile` or `requirements.txt` need `--build`.
- Collector tests, from `../gcp-permissions-checker/`:
  `MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/src:ro" -w /src python:3.12-slim sh -c "pip install -q -r requirements.txt && python -m unittest discover -s tests -v"`
- Shell quirks on the user's Windows machine: Git Bash rewrites arguments that look like paths (`/app/...`, `//delete:`, `https://`). Prefix `docker` calls with `MSYS_NO_PATHCONV=1`, and run `cmdkey` from PowerShell.

## Architecture

```
collector git history ─► history   diff log per commit (cached), corrupt-snapshot and gap detection
                         events    novelty-tiered events: 0 new service, 1 new resource, 2 new verbs
                         cadence   weekday/monthly rhythm, only from the dense-snapshot era
iann0036/iam-dataset ──► enrich    predefined roles, permission→roles, method→permission map (24h cache)
Discovery documents ───► discovery API spec for every catalog service; method→permission "mapped" or "inferred"
                         specs     normalized daily snapshot in specs/, diffed into specs/changes.jsonl
                         build_index ─► dist/*.json ─► web/ (static dashboard) and mcp_server/ (MCP tools)
```

`indexer/build_index.py` orchestrates the modules above. `dist/` holds `events.json`, `lookup.json` (packed as `[firstSeen, predatesRecord, removedOn, stage]`), `cadence.json`, `services.json`, `apis.json`, `detail/<service>.json` (loaded lazily by the dashboard), `roles.json`, `methods.json` and `api_changes.json`, which the MCP server reads.

Invariants that span files:
- **`meta.json` is always written last**, by the build and by `indexer/sync.py`. The MCP server reloads the whole index when its mtime changes, so it must never see a half-written build. Other files are written atomically, and stale detail files are pruned only after new ones exist.
- **Never change the format of `permissions.txt`** in the collector. Two years of history and several consumers depend on it; new data goes in new files (as `permissions_metadata.jsonl` did).
- **An unchanged API must write a byte-identical spec file.** Volatile fields (revision, fetch attempts) stay out of `specs/`, otherwise the daily commit churns.
- **A failed fetch is never evidence of change.** Discovery errors are not cached, and `specs.update_specs` carries the previous spec and status forward.
- `mcp_server/catalog.py` holds all query logic with no MCP dependency; `server.py` only wires tools and holds `INSTRUCTIONS`, the agent-facing guide. `catalog.build_request` mirrors `buildRequest` in `web/app.js` (placeholders, raw HTTP with CRLF and exact `Content-Length`); change both together.

MCP server specifics:
- MCP Python SDK **2.2.0**: `MCPServer` from `mcp.server.mcpserver` (FastMCP was renamed), `ToolError` from `mcp.server.mcpserver.exceptions`. `ToolAnnotations` fields are snake_case with camelCase aliases, so build them with `model_validate({...camelCase...})`. `mcp.Client(server)` connects in-process for tests.
- Knowledge only, by the user's choice: no credentials, never calls Google. Agents act through their own gcloud under user approval. It listens on loopback only, with DNS-rebinding protection (Host must be localhost or 127.0.0.1).
- Search weights a method's id tokens (3) over its first permission (2) over everything else (1), and breaks ties by fewer id tokens. `SYNONYMS` maps everyday verbs (create↔insert, update↔patch); `EXPANSIONS` maps product names (gke→container cluster, vm→compute instance).
- Role advice excludes service-agent roles (they are for Google-managed accounts), ranks the permission's own service first, lists basic roles separately, and recommends a custom role when nothing narrower exists.

## Daily pipeline

| When (UTC) | Where | What |
|---|---|---|
| 23:00 | checker `update-permissions.yml` | `queryTestablePermissions` on a project using secret `GCP_SA_KEY`. A shrink guard refuses a list below 97% of the previous one (override: `allow_shrink` input). Commits metadata-only changes, a `last_run.txt` heartbeat on quiet days, and alerts via `TELEGRAM_TOKEN`/`TELEGRAM_TO`. |
| 01:00 | this repo, `daily-catalog.yml` | Runs tests, builds with `--specs-dir specs`, commits `specs/`, publishes the `dist` JSON as `index.tar.gz` on release `index-latest`, then fails if the collector workflow is not `active` or has not succeeded in 50 hours. |
| every 6 h | local containers | `indexer.sync` downloads and installs the release. |

## Data caveats

- Snapshot `56c1f22` (2026-02-01, the list briefly replaced by a 2-line fixture) is excluded; any snapshot losing over 50% of the catalog is treated as corrupt.
- Collection was sporadic before Feb 2026 (gaps of 263 and 308 days). Gaps over 14 days make first-seen dates imprecise; quiet stretches up to ~12 days are normal because the collector only commits on change. Permissions present on 2024-06-06 "predate the record".
- **The catalog is project-scoped.** Organization- and folder-level permissions are mostly absent. Five org services (accesscontextmanager, assuredworkloads, cloudcontrolspartner, policyremediatormanager, riskmanager) left it together on 2026-01-30 but still exist; they are not deprecations. `prodactuation` (first seen 2026-08-09, gone 2026-08-28) was genuinely withdrawn.
- Discovery status `restricted` (401/403) means an anonymous request was refused, not that the API is private. `not_found` means nothing answers at `<service>.googleapis.com` (the API may have another name). `error` is transient.
- About 49% of method→permission links come from iam-dataset's map, 26% are inferred from names (and labelled so), 25% are unknown. About 29% of catalog permissions have no role data upstream.
- A missing `stage` in the IAM response is recorded as ALPHA (the proto3 zero value). This is unverified until the collector's first real run with metadata. `apiDisabled` is deliberately not stored because it describes the collector's own project.

## Current state (2026-09-16)

- **The pipeline is live.** Both repos are pushed. The first `daily-catalog` run (triggered by hand on 2026-09-15) passed, recorded the specs baseline in `specs/` and published `index-latest`. The local containers run in sync mode and serve that index.
- The workflow commits `specs/` every day, so `git pull` before committing here.
- Still to confirm: the collector's first scheduled run with the new code (23:00 UTC after 2026-09-15) should create `permissions_metadata.jsonl`. If its stages come out almost entirely ALPHA, the ALPHA default for a missing `stage` is wrong. The following 01:00 UTC `daily-catalog` run is the first real day-over-day spec comparison; `specs/changes.jsonl` only appears once something changes.
- No Telegram secrets are set on this repo yet, so a failed `daily-catalog` run only triggers GitHub's email.
- `gcp-iam-changelog` has 2 unpushed fixes (the `roles/` prefix bug, orphaned page files); the user has not approved pushing them.
- Open, undecided: collecting org-level permissions (querying an organization), fixing how the dashboard labels the five org services, retiring the legacy repos, Telegram secrets for this repo, an API key to fetch restricted specs (the user chose anonymous for now), stage-promotion events, a privileged-verbs filter.
