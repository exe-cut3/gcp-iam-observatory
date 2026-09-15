"""Release-rhythm statistics.

Weekday attribution is only meaningful where snapshots are dense. Early history
has multi-month gaps, and a single snapshot closing a 263-day gap would dump all
of that period's changes onto whatever weekday it happened to run. Those spans
are excluded rather than averaged in.
"""

from __future__ import annotations

import collections
import datetime

from .history import History, RELIABLE_GAP_DAYS

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def build_cadence(history: History, collection_hour_utc: int | None = None) -> dict:
    reliable_from = history.reliable_since()

    weekday_added = collections.Counter()
    weekday_snapshots = collections.Counter()
    monthly = collections.Counter()
    daily = []

    # The first snapshot is the whole catalog arriving at once. Counting it as
    # additions would put ~10k on the opening month and flatten every real month
    # next to it.
    baseline_sha = history.changes[0].sha if history.changes else None

    for change in history.changes:
        if change.sha == baseline_sha:
            continue
        month = change.date.strftime("%Y-%m")
        monthly[month] += len(change.added)
        daily.append(
            {
                "date": change.date.isoformat(),
                "added": len(change.added),
                "removed": len(change.removed),
                "reliable": reliable_from is not None and change.date >= reliable_from,
            }
        )
        if reliable_from is not None and change.date >= reliable_from:
            name = WEEKDAYS[change.date.weekday()]
            weekday_added[name] += len(change.added)
            weekday_snapshots[name] += 1

    total = sum(weekday_added.values())
    weekdays = [
        {
            "day": day,
            "added": weekday_added[day],
            "snapshots": weekday_snapshots[day],
            "share": round(100 * weekday_added[day] / total, 1) if total else 0.0,
        }
        for day in WEEKDAYS
    ]

    peak = max(weekdays, key=lambda w: w["added"]) if total else None

    return {
        "reliableFrom": reliable_from.isoformat() if reliable_from else None,
        "collectionHourUtc": collection_hour_utc,
        "weekdays": weekdays,
        "totalAdded": total,
        "peakDay": peak["day"] if peak else None,
        "peakShare": peak["share"] if peak else 0.0,
        "monthly": [
            {
                "month": m,
                "added": n,
                "reliable": reliable_from is not None and m >= reliable_from.strftime("%Y-%m"),
            }
            for m, n in sorted(monthly.items())
        ],
        "daily": daily,
    }


def build_coverage(history: History) -> dict:
    span = history.span
    return {
        "firstSnapshot": span[0].isoformat() if span else None,
        "lastSnapshot": span[1].isoformat() if span else None,
        "snapshots": len(history.changes),
        "catalogSize": len(history.catalog),
        "reliableFrom": history.reliable_since().isoformat() if history.reliable_since() else None,
        "reliableGapDays": RELIABLE_GAP_DAYS,
        "gaps": [
            {"from": a.isoformat(), "to": b.isoformat(), "days": d}
            for a, b, d in history.gaps
            if d > RELIABLE_GAP_DAYS
        ],
        "anomalies": [
            {
                "sha": a.sha[:8],
                "date": a.date.isoformat(),
                "previous": a.previous,
                "observed": a.observed,
            }
            for a in history.anomalies
        ],
    }
