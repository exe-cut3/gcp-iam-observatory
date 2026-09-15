"""Build the static index the dashboard reads.

Outputs into --output-dir:
  events.json      ranked, clustered events (the feed)
  lookup.json      permission -> first seen / removed / stage (the search index)
  services.json    roles per service
  cadence.json     release-rhythm statistics
  apis.json        Discovery status per explored service
  detail/*.json    per-service permission metadata, methods and schemas
  meta.json        coverage, gaps and collection anomalies
"""

from __future__ import annotations

import argparse
import collections
import datetime
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from indexer import cadence as cadence_mod
    from indexer import discovery as discovery_mod
    from indexer import enrich as enrich_mod
    from indexer import events as events_mod
    from indexer import history as history_mod
else:
    from . import cadence as cadence_mod
    from . import discovery as discovery_mod
    from . import enrich as enrich_mod
    from . import events as events_mod
    from . import history as history_mod


def write(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    print(f"  wrote {path.name}  ({path.stat().st_size / 1024:.0f} KB)")


def write_details(directory: Path, metadata: dict, explored: dict) -> None:
    """One file per service, fetched by the browser only when a card is opened."""
    directory.mkdir(parents=True, exist_ok=True)
    # The container's dist/ survives restarts, so a service that dropped out of
    # scope would otherwise keep serving last build's file.
    for stale in directory.glob("*.json"):
        stale.unlink()

    by_service: dict[str, dict] = {}
    for name, record in metadata.items():
        fields = {key: value for key, value in record.items() if key != "name"}
        by_service.setdefault(name.split(".")[0], {})[name] = fields

    services = sorted(s for s in set(by_service) | set(explored) if discovery_mod.SERVICE_NAME.match(s))
    total = 0
    for service in services:
        result = explored.get(service, {})
        payload = {
            "service": service,
            "discovery": result.get("discovery"),
            "methods": result.get("methods", []),
            "schemas": result.get("schemas", {}),
            "permissions": by_service.get(service, {}),
        }
        path = directory / f"{service}.json"
        path.write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
        total += path.stat().st_size
    print(f"  wrote detail/ for {len(services)} services  ({total / 1024:.0f} KB total)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--collector-repo", required=True, type=Path,
                        help="Path to the gcp-permissions-checker clone")
    parser.add_argument("--tracked-file", default="permissions.txt")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--cache-dir", type=Path, default=Path(".cache"))
    parser.add_argument("--collection-hour-utc", type=int, default=23,
                        help="Hour the collector runs, used to explain weekday attribution")
    parser.add_argument("--no-enrich", action="store_true",
                        help="Skip the iam-dataset fetch and build catalog-only")
    parser.add_argument("--refresh-enrich", action="store_true")
    parser.add_argument("--metadata-file", default="permissions_metadata.jsonl",
                        help="Per-permission metadata file in the collector repo")
    parser.add_argument("--no-discovery", action="store_true",
                        help="Skip fetching Discovery documents for the API explorer")
    parser.add_argument("--api-days", type=int, default=365,
                        help="Explore APIs of services with additions in this many days (0 = all)")
    parser.add_argument("--refresh-discovery", action="store_true")
    args = parser.parse_args()

    if not (args.collector_repo / ".git").exists():
        print(f"error: {args.collector_repo} is not a git repository", file=sys.stderr)
        return 1

    args.output_dir.mkdir(parents=True, exist_ok=True)

    print(f"reading history from {args.collector_repo}")
    hist = history_mod.load_history(
        args.collector_repo, args.tracked_file, args.cache_dir / "diff-log.json"
    )
    span = hist.span
    print(f"  {len(hist.changes)} snapshots, {span[0]} -> {span[1]}")
    print(f"  catalog now {len(hist.catalog)} permissions")
    if hist.anomalies:
        for a in hist.anomalies:
            print(f"  ! excluded corrupt snapshot {a.sha[:8]} {a.date}: {a.previous} -> {a.observed}")

    print("building events")
    all_events = events_mod.build_events(hist)
    by_tier = {t: sum(1 for e in all_events if e.tier == t) for t in events_mod.TIER_LABELS}
    print(f"  {len(all_events)} events  "
          + "  ".join(f"T{t}={n}" for t, n in by_tier.items()))

    services = {}
    context = {}
    if not args.no_enrich:
        print("fetching role context from iam-dataset")
        context = enrich_mod.load(args.cache_dir / "iam-dataset", args.refresh_enrich)
        if context["rolesByService"]:
            enrich_mod.apply(all_events, context)
            services = context["rolesByService"]
            print(f"  role context for {len(services)} services")

    seen = history_mod.first_seen(hist)
    gone = history_mod.last_seen(hist)
    baseline = span[0]

    # Packed as [firstSeen, predatesRecord, removedOn] rather than named fields:
    # service/resource/verb are all derivable from the key, and at 14k entries
    # the field names alone were most of the payload.
    lookup = {
        permission: [
            date.isoformat(),
            1 if date == baseline else 0,
            gone[permission].isoformat() if permission in gone else None,
        ]
        for permission, date in seen.items()
    }

    metadata = history_mod.load_metadata(args.collector_repo, args.metadata_file)
    if metadata is None:
        print(f"  no {args.metadata_file} in the collector yet; permissions show names only")
        metadata = {}
    else:
        print(f"  metadata for {len(metadata)} permissions")
        for permission, record in lookup.items():
            record.append(metadata.get(permission, {}).get("stage"))

    explored: dict[str, dict] = {}
    if not args.no_discovery:
        cutoff = datetime.date.today() - datetime.timedelta(days=args.api_days)
        targets = sorted({
            e.service for e in all_events
            if e.change == "added" and (args.api_days == 0 or e.date >= cutoff)
        })
        print(f"exploring API docs for {len(targets)} services")
        explored = discovery_mod.explore(
            targets,
            args.cache_dir / "discovery",
            hist.catalog,
            map_path=context.get("mapPath"),
            refresh=args.refresh_discovery,
        )
        counts = collections.Counter(r["summary"]["status"] for r in explored.values())
        print("  " + "  ".join(f"{status}={n}" for status, n in sorted(counts.items())))

    print("writing index")
    write(args.output_dir / "events.json", {
        "generatedAt": datetime.datetime.now(tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "events": [e.to_json() for e in all_events],
    })
    write(args.output_dir / "lookup.json", {
        "baseline": baseline.isoformat(),
        "catalogSize": len(hist.catalog),
        "permissions": lookup,
    })
    write(args.output_dir / "services.json", {"rolesByService": services})
    write_details(args.output_dir / "detail", metadata, explored)
    write(args.output_dir / "apis.json", {
        "exploredDays": None if args.no_discovery else args.api_days,
        "services": {service: result["summary"] for service, result in explored.items()},
    })
    write(args.output_dir / "cadence.json",
          cadence_mod.build_cadence(hist, args.collection_hour_utc))
    write(args.output_dir / "meta.json", {
        "generatedAt": datetime.datetime.now(tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "coverage": cadence_mod.build_coverage(hist),
        "summary30": events_mod.summarise(all_events, datetime.date.today() - datetime.timedelta(days=30)),
        "summary90": events_mod.summarise(all_events, datetime.date.today() - datetime.timedelta(days=90)),
        "metadata": {
            "available": bool(metadata),
            "permissions": len(metadata),
            "stages": dict(collections.Counter(r.get("stage") or "unknown" for r in metadata.values())),
        },
        "discovery": {
            "explored": len(explored),
            "days": None if args.no_discovery else args.api_days,
            "byStatus": dict(collections.Counter(r["summary"]["status"] for r in explored.values())),
        },
    })
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
