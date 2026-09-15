"""Replace the local index with the one the daily workflow published.

The published archive holds only data files; the dashboard's HTML, JS and CSS
come from the image. Files are moved in one at a time with meta.json last,
because the MCP server reloads when meta.json changes and must not pick up a
half-installed index.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tarfile
import urllib.request
from pathlib import Path

MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
REQUIRED = ("meta.json", "events.json", "lookup.json", "cadence.json")


def _download(url: str, target: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "gcp-iam-observatory"})
    total = 0
    with urllib.request.urlopen(request, timeout=300) as response, target.open("wb") as out:
        while chunk := response.read(1 << 20):
            total += len(chunk)
            if total > MAX_ARCHIVE_BYTES:
                raise ValueError(f"archive is larger than {MAX_ARCHIVE_BYTES} bytes")
            out.write(chunk)


def _data_files(root: Path) -> set[Path]:
    return {p.relative_to(root) for p in root.rglob("*.json") if p.is_file()}


def sync(url: str, dist: Path) -> bool:
    """Install the published index if it is newer. Returns whether it installed one."""
    dist.mkdir(parents=True, exist_ok=True)
    incoming = dist / ".incoming"
    shutil.rmtree(incoming, ignore_errors=True)
    incoming.mkdir()
    try:
        archive = incoming / "index.tar.gz"
        _download(url, archive)
        unpacked = incoming / "index"
        with tarfile.open(archive, "r:gz") as tar:
            # The "data" filter refuses absolute paths, links out of the tree and
            # device files, so a tampered archive cannot write outside it.
            tar.extractall(unpacked, filter="data")

        for name in REQUIRED:
            if not (unpacked / name).is_file():
                raise ValueError(f"published index is missing {name}")
        published = json.loads((unpacked / "meta.json").read_text(encoding="utf-8")).get("generatedAt")

        current = dist / "meta.json"
        if current.exists() and json.loads(current.read_text(encoding="utf-8")).get("generatedAt") == published:
            return False

        files = _data_files(unpacked)
        for rel in sorted(files, key=lambda p: p == Path("meta.json")):
            target = dist / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(unpacked / rel, target)

        for rel in _data_files(dist) - files:
            if rel.parts[0] != incoming.name:
                (dist / rel).unlink()
        return True
    finally:
        shutil.rmtree(incoming, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Install the index published by the daily workflow.")
    parser.add_argument("--url", required=True)
    parser.add_argument("--dist", type=Path, required=True)
    args = parser.parse_args()
    try:
        installed = sync(args.url, args.dist)
    except Exception as exc:
        # Any failure (network, bad archive) must leave the current index serving.
        print(f"sync failed: {exc}", file=sys.stderr)
        return 1
    print("installed a newer index" if installed else "index already current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
