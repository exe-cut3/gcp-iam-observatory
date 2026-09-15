"""Daily API spec snapshots and the changes between them.

A Discovery document can only be fetched as it is today, so the only way to know
when an endpoint appeared is to keep each day's spec and compare it with the
previous one. Specs are stored normalized, one JSON object per line (the API
header, then methods, then schemas, each sorted) with volatile fields such as the
revision and fetch attempts left out, so an unchanged API writes an identical
file and git records nothing for it.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

from .discovery import ERROR, LISTED, UNLISTED

PUBLISHED = (LISTED, UNLISTED)


def _dumps(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def spec_text(result: dict) -> str:
    discovery = result["discovery"]
    header = {
        "kind": "api",
        "api": discovery["api"],
        "version": discovery["version"],
        "title": discovery["title"],
        "rootUrl": discovery["rootUrl"],
        "servicePath": discovery["servicePath"],
        "documentationLink": discovery["documentationLink"],
    }
    lines = [_dumps(header)]
    lines += [_dumps({"kind": "method", **m}) for m in sorted(result["methods"], key=lambda m: m["id"])]
    lines += [_dumps({"kind": "schema", "name": name, **result["schemas"][name]}) for name in sorted(result["schemas"])]
    return "\n".join(lines) + "\n"


def _method_ids(path: Path) -> set[str] | None:
    if not path.exists():
        return None
    ids = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if record.get("kind") == "method":
            ids.add(record["id"])
    return ids


def _write_if_changed(path: Path, text: str) -> None:
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def update_specs(specs_dir: Path, explored: dict, today: datetime.date) -> list[dict]:
    """Store today's specs and return what changed since the last stored snapshot.

    The first run only records a baseline. A service that failed to fetch keeps its
    previous spec and status, so a timeout never reads as an API vanishing and then
    reappearing. Services not explored today are carried forward untouched.
    """
    status_path = specs_dir / "status.json"
    baseline = not status_path.exists()
    previous = {} if baseline else json.loads(status_path.read_text(encoding="utf-8"))
    status = dict(previous)
    date = today.isoformat()
    events: list[dict] = []

    for service, result in sorted(explored.items()):
        discovery = result["discovery"]
        if discovery["status"] == ERROR:
            status.setdefault(service, {"status": ERROR, "api": discovery["api"], "version": None})
            continue

        spec_path = specs_dir / "apis" / f"{service}.jsonl"
        old_methods = _method_ids(spec_path)
        new_methods = None
        if discovery["status"] in PUBLISHED:
            _write_if_changed(spec_path, spec_text(result))
            new_methods = {m["id"] for m in result["methods"]}

        entry = {"status": discovery["status"], "api": discovery["api"], "version": discovery["version"]}
        old = previous.get(service)
        status[service] = entry
        # Nothing to compare against on the first run, or when the only earlier
        # observation was a failed fetch.
        if baseline or (old is not None and old["status"] == ERROR):
            continue

        base = {"date": date, "service": service}
        if old is None:
            events.append({**base, "type": "new_api", "status": entry["status"], "version": entry["version"],
                           "methodCount": len(new_methods or ())})
            continue
        if old["status"] != entry["status"]:
            event = {**base, "type": "status_change", "from": old["status"], "to": entry["status"],
                     "version": entry["version"]}
            if new_methods is not None:
                event["methodCount"] = len(new_methods)
            events.append(event)
        elif entry["version"] and old.get("version") != entry["version"]:
            events.append({**base, "type": "version_change", "from": old.get("version"), "to": entry["version"]})

        if old_methods is not None and new_methods is not None:
            added = sorted(new_methods - old_methods)
            removed = sorted(old_methods - new_methods)
            if added:
                events.append({**base, "type": "new_methods", "count": len(added), "methods": added})
            if removed:
                events.append({**base, "type": "removed_methods", "count": len(removed), "methods": removed})

    specs_dir.mkdir(parents=True, exist_ok=True)
    status_path.write_text(json.dumps(status, sort_keys=True, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    if events:
        with (specs_dir / "changes.jsonl").open("a", encoding="utf-8") as fh:
            for event in events:
                fh.write(_dumps(event) + "\n")
    (specs_dir / "last_run.json").write_text(
        _dumps({"date": date, "services": len(explored), "changes": len(events), "baseline": baseline}) + "\n",
        encoding="utf-8",
    )
    return events


def load_changes(specs_dir: Path) -> list[dict]:
    path = specs_dir / "changes.jsonl"
    if not path.exists():
        return []
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    events.sort(key=lambda e: (e["date"], e["service"], e["type"]), reverse=True)
    return events
