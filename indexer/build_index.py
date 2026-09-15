"""Build the static index the dashboard reads.

Outputs into --output-dir:
  events.json      ranked, clustered events (the feed)
  lookup.json      permission -> first seen / removed (the search index)
  cadence.json     release-rhythm statistics
  meta.json        coverage, gaps and collection anomalies
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from indexer import cadence as cadence_mod
    from indexer import enrich as enrich_mod
    from indexer import events as events_mod
    from indexer import history as history_mod
else:
    from . import cadence as cadence_mod
    from . import enrich as enrich_mod
    from . import events as events_mod
    from . import history as history_mod


def write(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    print(f"  wrote {path.name}  ({path.stat().st_size / 1024:.0f} KB)")


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
    write(args.output_dir / "cadence.json",
          cadence_mod.build_cadence(hist, args.collection_hour_utc))
    write(args.output_dir / "meta.json", {
        "generatedAt": datetime.datetime.now(tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "coverage": cadence_mod.build_coverage(hist),
        "summary30": events_mod.summarise(all_events, datetime.date.today() - datetime.timedelta(days=30)),
        "summary90": events_mod.summarise(all_events, datetime.date.today() - datetime.timedelta(days=90)),
    })
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
