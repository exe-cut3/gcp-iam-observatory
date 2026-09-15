"""Attach role context from the upstream iam-dataset.

The catalog tells you a permission exists; it does not tell you which roles grant
it or how far along a service is. Two files from iam-dataset cover that. The repo
itself is ~900MB, so they are fetched individually and cached. Enrichment is
optional: without network the index still builds, just without role context.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

RAW_BASE = "https://raw.githubusercontent.com/iann0036/iam-dataset/main/gcp"
SOURCES = {
    "predefined_roles.json": f"{RAW_BASE}/predefined_roles.json",
    "permissions.json": f"{RAW_BASE}/permissions.json",
    "map.json": f"{RAW_BASE}/map.json",
}

# Upstream republishes daily; the cache lives in a long-lived Docker volume, so
# without an expiry it would serve the first download forever.
CACHE_TTL_SECONDS = 24 * 3600

# Roles broad enough that granting a new permission through them widens blast
# radius for every principal already holding the role.
BROAD_ROLES = {"roles/owner", "roles/editor", "roles/viewer"}


def fetch(cache_dir: Path, refresh: bool = False) -> dict[str, Path]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, url in SOURCES.items():
        target = cache_dir / name
        expired = not target.exists() or time.time() - target.stat().st_mtime > CACHE_TTL_SECONDS
        if refresh or expired:
            try:
                with urllib.request.urlopen(url, timeout=120) as response:
                    target.write_bytes(response.read())
            except (urllib.error.URLError, TimeoutError) as exc:
                fallback = " (using cached copy)" if target.exists() else ""
                print(f"  ! could not fetch {name}: {exc}{fallback}")
        if target.exists():
            paths[name] = target
    return paths


def load(cache_dir: Path, refresh: bool = False) -> dict:
    paths = fetch(cache_dir, refresh)

    roles_by_service: dict[str, list[dict]] = {}
    if "predefined_roles.json" in paths:
        roles = json.loads(paths["predefined_roles.json"].read_text(encoding="utf-8"))
        for role in roles:
            name = role.get("name", "")
            if not name.startswith("roles/"):
                continue
            service = name.removeprefix("roles/").split(".")[0]
            roles_by_service.setdefault(service, []).append(
                {
                    "name": name,
                    "title": role.get("title", ""),
                    "stage": role.get("stage", ""),
                    "deleted": bool(role.get("deleted")),
                }
            )

    granting_roles: dict[str, list[str]] = {}
    if "permissions.json" in paths:
        mapping = json.loads(paths["permissions.json"].read_text(encoding="utf-8"))
        if isinstance(mapping, dict):
            for permission, roles in mapping.items():
                names = [r.get("id", "") for r in roles if isinstance(r, dict)]
                granting_roles[permission] = [n for n in names if n]

    return {
        "rolesByService": roles_by_service,
        "grantingRoles": granting_roles,
        "mapPath": paths.get("map.json"),
    }


def apply(events, context: dict) -> None:
    """Attach only what is specific to the event.

    The full role list for a service is identical for every event that service
    ever produces, so it is published once in services.json and looked up by
    name in the browser rather than repeated on ~1500 events.
    """
    roles_by_service = context.get("rolesByService", {})
    granting_roles = context.get("grantingRoles", {})

    for event in events:
        event.stages = sorted({r["stage"] for r in roles_by_service.get(event.service, []) if r["stage"]})
        broad = set()
        for permission in event.permissions:
            broad.update(BROAD_ROLES.intersection(granting_roles.get(permission, [])))
        event.roles = [{"name": r, "broad": True} for r in sorted(broad)]
