"""Read the permission-catalog time series out of the collector repo's git history.

The collector commits a full permissions.txt snapshot each day. Storing every
snapshot would cost ~500KB per commit, so this module reduces the history to a
diff log (what appeared and disappeared at each commit) and caches that. A rerun
replays the cached diffs to rebuild the current set and only reads commits it has
not seen before.
"""

from __future__ import annotations

import datetime
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# A snapshot that loses more than this fraction of the catalog is treated as a
# collection failure rather than Google deleting half of IAM. This has happened
# once for real: the list was replaced by a 2-line test fixture.
CORRUPTION_RETAINED_RATIO = 0.5

# The collector only commits when the catalog changes, so a quiet stretch is
# indistinguishable from an outage in git history alone. Observed quiet periods
# top out around 12 days while real outages ran 18, 22, 263 and 308 days, so a
# fortnight separates them. Above this, "first seen on date D" only means
# "appeared somewhere in the window ending D" and dating in that span is coarse.
RELIABLE_GAP_DAYS = 14


@dataclass
class Change:
    sha: str
    date: datetime.date
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "sha": self.sha,
            "date": self.date.isoformat(),
            "added": self.added,
            "removed": self.removed,
        }

    @staticmethod
    def from_json(d: dict) -> "Change":
        return Change(
            sha=d["sha"],
            date=datetime.date.fromisoformat(d["date"]),
            added=d["added"],
            removed=d["removed"],
        )


@dataclass
class Anomaly:
    sha: str
    date: datetime.date
    previous: int
    observed: int


@dataclass
class History:
    changes: list[Change]
    anomalies: list[Anomaly]
    gaps: list[tuple[datetime.date, datetime.date, int]]
    catalog: set[str]

    @property
    def span(self) -> tuple[datetime.date, datetime.date] | None:
        if not self.changes:
            return None
        return self.changes[0].date, self.changes[-1].date

    def reliable_since(self) -> datetime.date | None:
        """First date after which snapshots are dense enough to date precisely."""
        if not self.changes:
            return None
        for start, end, days in reversed(self.gaps):
            if days > RELIABLE_GAP_DAYS:
                return end
        return self.changes[0].date


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def _commits(repo: Path, tracked_file: str) -> list[tuple[str, datetime.date]]:
    out = _git(repo, "log", "--format=%H|%cI", "--reverse", "--", tracked_file)
    commits = []
    for line in out.strip().splitlines():
        sha, iso = line.split("|", 1)
        commits.append((sha, datetime.datetime.fromisoformat(iso).date()))
    return commits


def _permissions_at(repo: Path, sha: str, tracked_file: str) -> set[str]:
    blob = _git(repo, "show", f"{sha}:{tracked_file}")
    return {line.strip() for line in blob.splitlines() if line.strip()}


def load_history(repo: Path, tracked_file: str, cache_path: Path | None = None) -> History:
    commits = _commits(repo, tracked_file)
    if not commits:
        raise RuntimeError(f"no commits touch {tracked_file} in {repo}")

    cached: list[Change] = []
    cached_anomalies: list[Anomaly] = []
    if cache_path and cache_path.exists():
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
        candidate = [Change.from_json(c) for c in raw["changes"]]
        # Only trust the cache if it still describes a prefix of this history;
        # a rewritten history must be reprocessed from scratch.
        shas = [s for s, _ in commits]
        if [c.sha for c in candidate] == shas[: len(candidate)]:
            cached = candidate
            cached_anomalies = [
                Anomaly(a["sha"], datetime.date.fromisoformat(a["date"]), a["previous"], a["observed"])
                for a in raw.get("anomalies", [])
            ]

    catalog: set[str] = set()
    for change in cached:
        catalog.update(change.added)
        catalog.difference_update(change.removed)

    changes = list(cached)
    anomalies = list(cached_anomalies)
    seen = {c.sha for c in cached}

    for sha, date in commits:
        if sha in seen:
            continue
        observed = _permissions_at(repo, sha, tracked_file)
        if catalog and len(observed) < len(catalog) * CORRUPTION_RETAINED_RATIO:
            anomalies.append(Anomaly(sha, date, len(catalog), len(observed)))
            continue
        added = sorted(observed - catalog)
        removed = sorted(catalog - observed)
        if added or removed or not changes:
            changes.append(Change(sha=sha, date=date, added=added, removed=removed))
        catalog = observed

    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(
                {
                    "changes": [c.to_json() for c in changes],
                    "anomalies": [
                        {"sha": a.sha, "date": a.date.isoformat(), "previous": a.previous, "observed": a.observed}
                        for a in anomalies
                    ],
                },
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )

    gaps = []
    for earlier, later in zip(changes, changes[1:]):
        days = (later.date - earlier.date).days
        if days > 1:
            gaps.append((earlier.date, later.date, days))

    return History(changes=changes, anomalies=anomalies, gaps=gaps, catalog=catalog)


def first_seen(history: History) -> dict[str, datetime.date]:
    seen: dict[str, datetime.date] = {}
    for change in history.changes:
        for permission in change.added:
            seen.setdefault(permission, change.date)
    return seen


def last_seen(history: History) -> dict[str, datetime.date]:
    """Date a permission was removed, for permissions no longer in the catalog."""
    gone: dict[str, datetime.date] = {}
    for change in history.changes:
        for permission in change.removed:
            gone[permission] = change.date
        for permission in change.added:
            gone.pop(permission, None)
    return gone
