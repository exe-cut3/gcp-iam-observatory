import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

from indexer import sync

PUBLISHED = {
    "./meta.json": {"generatedAt": "2026-09-16T01:05:00Z"},
    "./events.json": {"events": []},
    "./lookup.json": {"permissions": {}},
    "./cadence.json": {},
    "./detail/storage.json": {"service": "storage"},
}


def add(tar, name, data: bytes):
    info = tarfile.TarInfo(name)
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))


def make_archive(path, files, extra=None):
    with tarfile.open(path, "w:gz") as tar:
        for name, payload in files.items():
            add(tar, name, json.dumps(payload).encode())
        if extra:
            extra(tar)


class Sync(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.dist = self.tmp / "dist"
        (self.dist / "detail").mkdir(parents=True)
        (self.dist / "index.html").write_text("<html>", encoding="utf-8")
        (self.dist / "meta.json").write_text(json.dumps({"generatedAt": "2026-09-15T01:05:00Z"}), encoding="utf-8")
        (self.dist / "detail" / "gone.json").write_text("{}", encoding="utf-8")
        self.archive = self.tmp / "index.tar.gz"

    def generated_at(self):
        return json.loads((self.dist / "meta.json").read_text(encoding="utf-8"))["generatedAt"]

    def test_installs_newer_index_and_prunes_only_stale_data(self):
        make_archive(self.archive, PUBLISHED)
        self.assertTrue(sync.sync(self.archive.as_uri(), self.dist))
        self.assertEqual(self.generated_at(), "2026-09-16T01:05:00Z")
        self.assertTrue((self.dist / "detail" / "storage.json").exists())
        self.assertFalse((self.dist / "detail" / "gone.json").exists())
        self.assertTrue((self.dist / "index.html").exists())
        self.assertFalse((self.dist / ".incoming").exists())

    def test_same_index_is_not_reinstalled(self):
        make_archive(self.archive, PUBLISHED)
        sync.sync(self.archive.as_uri(), self.dist)
        self.assertFalse(sync.sync(self.archive.as_uri(), self.dist))

    def test_incomplete_archive_leaves_current_index_untouched(self):
        make_archive(self.archive, {"./meta.json": {"generatedAt": "2026-09-16T01:05:00Z"}})
        with self.assertRaises(ValueError):
            sync.sync(self.archive.as_uri(), self.dist)
        self.assertEqual(self.generated_at(), "2026-09-15T01:05:00Z")
        self.assertTrue((self.dist / "detail" / "gone.json").exists())

    def test_archive_escaping_its_directory_is_refused(self):
        make_archive(self.archive, PUBLISHED, lambda tar: add(tar, "../../escaped.json", b"{}"))
        with self.assertRaises(tarfile.FilterError):
            sync.sync(self.archive.as_uri(), self.dist)
        self.assertFalse((self.dist / "escaped.json").exists())
        self.assertEqual(self.generated_at(), "2026-09-15T01:05:00Z")


if __name__ == "__main__":
    unittest.main()
