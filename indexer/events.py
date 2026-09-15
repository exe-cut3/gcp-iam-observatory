"""Turn raw permission diffs into ranked events.

A single GCP launch shows up in the raw data as dozens of rows: one per
permission, plus a row per role that grants it, plus a row per API method. The
catalog alone produced 34 rows for serviceextensions going live. Grouping those
into one event per coherent change is what keeps the feed readable, and ranking
by novelty rather than by security severity matches what the catalog is actually
good at telling you: that something new exists.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field

from .history import History

# Novelty tiers. Volume rises steeply with each tier and interest falls, so the
# feed shows 0 and 1 by default and keeps 2 collapsed.
TIER_NEW_SERVICE = 0
TIER_NEW_RESOURCE = 1
TIER_NEW_CAPABILITY = 2

TIER_LABELS = {
    TIER_NEW_SERVICE: "new service",
    TIER_NEW_RESOURCE: "new resource",
    TIER_NEW_CAPABILITY: "new capability",
}


def split(permission: str) -> tuple[str, str, str]:
    """compute.instances.osLogin -> (compute, compute.instances, osLogin)"""
    parts = permission.split(".")
    if len(parts) < 2:
        return permission, permission, ""
    return parts[0], ".".join(parts[:-1]), parts[-1]


@dataclass
class Event:
    id: str
    date: datetime.date
    sha: str
    tier: int
    change: str          # "added" | "removed"
    service: str
    resource: str        # "" for whole-service events
    permissions: list[str] = field(default_factory=list)
    roles: list[dict] = field(default_factory=list)
    stages: list[str] = field(default_factory=list)

    @property
    def title(self) -> str:
        return self.service if self.tier == TIER_NEW_SERVICE else self.resource

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "date": self.date.isoformat(),
            "sha": self.sha[:8],
            "tier": self.tier,
            "tierLabel": TIER_LABELS[self.tier],
            "change": self.change,
            "service": self.service,
            "resource": self.resource,
            "title": self.title,
            "permissions": self.permissions,
            "count": len(self.permissions),
            "roles": self.roles,
            "stages": self.stages,
        }


def _event_id(date: datetime.date, change: str, key: str) -> str:
    # Stable across rebuilds so a permalink keeps working; positional ids do not.
    return f"{date.isoformat()}-{change}-{key}".replace("/", "_")


def build_events(history: History) -> list[Event]:
    """Walk the history forward, classifying each change against what existed before."""
    known_services: set[str] = set()
    known_resources: set[str] = set()
    events: list[Event] = []

    for change in history.changes:
        events.extend(
            _events_for(change.added, "added", change, known_services, known_resources)
        )
        # Removals are classified against the same known sets but never redefine
        # them: a resource that disappears stays "known" so its later return is
        # not reported as brand new.
        events.extend(
            _events_for(change.removed, "removed", change, known_services, known_resources, learn=False)
        )

        for permission in change.added:
            service, resource, _ = split(permission)
            known_services.add(service)
            known_resources.add(resource)

    events.sort(key=lambda e: (e.date, e.tier, e.service), reverse=True)
    return events


def _events_for(
    permissions: list[str],
    change_type: str,
    change,
    known_services: set[str],
    known_resources: set[str],
    learn: bool = True,
) -> list[Event]:
    if not permissions:
        return []

    by_service: dict[str, list[str]] = {}
    by_resource: dict[str, list[str]] = {}
    for permission in permissions:
        service, resource, _ = split(permission)
        by_service.setdefault(service, []).append(permission)
        by_resource.setdefault(resource, []).append(permission)

    # The first snapshot is the baseline, not a launch event: everything in it
    # predates the record and would otherwise appear as thousands of new services.
    baseline = learn and not known_services

    events: list[Event] = []
    claimed: set[str] = set()

    if not baseline:
        for service, perms in sorted(by_service.items()):
            is_new = (service not in known_services) if change_type == "added" else False
            if not is_new:
                continue
            events.append(
                Event(
                    id=_event_id(change.date, change_type, service),
                    date=change.date,
                    sha=change.sha,
                    tier=TIER_NEW_SERVICE,
                    change=change_type,
                    service=service,
                    resource="",
                    permissions=sorted(perms),
                )
            )
            claimed.update(perms)

        for resource, perms in sorted(by_resource.items()):
            remaining = [p for p in perms if p not in claimed]
            if not remaining:
                continue
            service = resource.split(".")[0]
            is_new_resource = resource not in known_resources
            tier = TIER_NEW_RESOURCE if is_new_resource else TIER_NEW_CAPABILITY
            events.append(
                Event(
                    id=_event_id(change.date, change_type, resource),
                    date=change.date,
                    sha=change.sha,
                    tier=tier,
                    change=change_type,
                    service=service,
                    resource=resource,
                    permissions=sorted(remaining),
                )
            )

    return events


def summarise(events: list[Event], since: datetime.date | None = None) -> dict:
    scoped = [e for e in events if since is None or e.date >= since]
    by_tier = {t: 0 for t in TIER_LABELS}
    permissions = 0
    for event in scoped:
        by_tier[event.tier] += 1
        permissions += len(event.permissions)
    return {
        "events": len(scoped),
        "permissions": permissions,
        "byTier": {str(k): v for k, v in by_tier.items()},
        "newServices": sorted(
            {e.service for e in scoped if e.tier == TIER_NEW_SERVICE and e.change == "added"}
        ),
    }
