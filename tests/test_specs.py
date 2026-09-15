import datetime
import json
import tempfile
import unittest
from pathlib import Path

from indexer import specs

DAY1, DAY2, DAY3 = (datetime.date(2026, 9, day) for day in (15, 16, 17))


def result(status="listed", version="v1", methods=("svc.things.get",)):
    published = status in ("listed", "unlisted")
    return {
        "discovery": {
            "status": status, "api": "svc", "version": version if published else None, "title": "Svc API",
            "rootUrl": "https://svc.googleapis.com/", "servicePath": "", "documentationLink": None,
        },
        "methods": [
            {"id": m, "httpMethod": "GET", "path": "v1/things", "description": "", "parameters": [],
             "request": None, "response": None, "permissions": [], "permissionSource": None,
             "resource": "svc.things"}
            for m in methods
        ] if published else [],
        "schemas": {},
    }


class UpdateSpecs(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def run_day(self, day, explored):
        return specs.update_specs(self.dir, explored, day)

    def test_first_run_is_a_baseline(self):
        self.assertEqual(self.run_day(DAY1, {"svc": result()}), [])
        self.assertTrue((self.dir / "apis" / "svc.jsonl").exists())
        self.assertTrue(json.loads((self.dir / "last_run.json").read_text(encoding="utf-8"))["baseline"])

    def test_new_and_removed_methods(self):
        self.run_day(DAY1, {"svc": result(methods=("svc.things.get", "svc.things.list"))})
        events = self.run_day(DAY2, {"svc": result(methods=("svc.things.get", "svc.things.create"))})
        by_type = {e["type"]: e for e in events}
        self.assertEqual(by_type["new_methods"]["methods"], ["svc.things.create"])
        self.assertEqual(by_type["removed_methods"]["methods"], ["svc.things.list"])
        self.assertEqual(specs.load_changes(self.dir)[0]["date"], "2026-09-16")

    def test_spec_becoming_public_is_one_event_with_its_size(self):
        self.run_day(DAY1, {"svc": result(status="restricted")})
        events = self.run_day(DAY2, {"svc": result(methods=("svc.a.get", "svc.a.list"))})
        self.assertEqual(events, [{
            "date": "2026-09-16", "service": "svc", "type": "status_change",
            "from": "restricted", "to": "listed", "version": "v1", "methodCount": 2,
        }])

    def test_version_change(self):
        self.run_day(DAY1, {"svc": result(version="v1beta")})
        events = self.run_day(DAY2, {"svc": result(version="v1")})
        self.assertEqual([(e["type"], e["from"], e["to"]) for e in events], [("version_change", "v1beta", "v1")])

    def test_fetch_error_changes_nothing_and_recovery_is_silent(self):
        self.run_day(DAY1, {"svc": result()})
        spec = self.dir / "apis" / "svc.jsonl"
        before = spec.read_text(encoding="utf-8")
        self.assertEqual(self.run_day(DAY2, {"svc": result(status="error")}), [])
        self.assertEqual(self.run_day(DAY3, {"svc": result()}), [])
        self.assertEqual(spec.read_text(encoding="utf-8"), before)

    def test_service_first_seen_after_the_baseline(self):
        self.run_day(DAY1, {"svc": result()})
        events = self.run_day(DAY2, {"svc": result(), "fresh": result(status="restricted")})
        self.assertEqual([(e["service"], e["type"], e["status"]) for e in events], [("fresh", "new_api", "restricted")])

    def test_unchanged_api_does_not_rewrite_its_spec(self):
        self.run_day(DAY1, {"svc": result()})
        spec = self.dir / "apis" / "svc.jsonl"
        written = spec.stat().st_mtime_ns
        self.assertEqual(self.run_day(DAY2, {"svc": result()}), [])
        self.assertEqual(spec.stat().st_mtime_ns, written)


if __name__ == "__main__":
    unittest.main()
