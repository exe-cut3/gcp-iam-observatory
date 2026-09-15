import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from indexer import discovery, history

CATALOG = {
    "agentidentity.locations.get",
    "agentidentity.accessSummaries.list",
    "aiplatform.endpoints.update",
    "aiplatform.operations.get",
}

IDENTITY_REFUSAL = json.dumps({"error": {
    "code": 403,
    "message": "Method doesn't allow unregistered callers (callers without established identity). "
               "Please use API Key or other form of API consumer identity to call this API.",
}}).encode()

DOC = {
    "rootUrl": "https://agentidentity.googleapis.com/",
    "servicePath": "",
    "resources": {"projects": {"resources": {"locations": {
        "methods": {"get": {
            "id": "agentidentity.projects.locations.get",
            "httpMethod": "GET",
            "path": "v1/{+name}",
            "flatPath": "v1/projects/{projectsId}/locations/{locationsId}",
            "parameters": {
                "name": {"location": "path", "required": True, "type": "string"},
                "alt": {"location": "header", "type": "string"},
            },
            "response": {"$ref": "Location"},
        }},
        "resources": {"accessSummaries": {"methods": {"list": {
            "id": "agentidentity.projects.locations.accessSummaries.list",
            "httpMethod": "GET",
            "path": "v1/{+parent}/accessSummaries",
            "response": {"$ref": "ListAccessSummariesResponse"},
        }}}},
    }}}},
    "schemas": {
        "Location": {"type": "object", "properties": {"name": {"type": "string"}}},
        "ListAccessSummariesResponse": {"type": "object", "properties": {
            "accessSummaries": {"type": "array", "items": {"$ref": "AccessSummary"}},
        }},
        "AccessSummary": {"type": "object", "properties": {
            "createTime": {"type": "string", "readOnly": True},
        }},
        "Unused": {"type": "object"},
    },
}


class InferPermissions(unittest.TestCase):
    def test_strips_parent_collections(self):
        self.assertEqual(
            discovery.infer_permissions(
                "agentidentity.projects.locations.accessSummaries.list", "agentidentity", CATALOG),
            ["agentidentity.accessSummaries.list"],
        )

    def test_patch_is_authorised_by_update(self):
        self.assertEqual(
            discovery.infer_permissions("aiplatform.projects.locations.endpoints.patch", "aiplatform", CATALOG),
            ["aiplatform.endpoints.update"],
        )

    def test_nested_resource_falls_back_to_innermost_collection(self):
        self.assertEqual(
            discovery.infer_permissions(
                "aiplatform.projects.locations.endpoints.operations.get", "aiplatform", CATALOG),
            ["aiplatform.operations.get"],
        )

    def test_location_methods_resolve(self):
        self.assertEqual(
            discovery.infer_permissions("agentidentity.projects.locations.get", "agentidentity", CATALOG),
            ["agentidentity.locations.get"],
        )

    def test_never_invents_a_permission(self):
        self.assertEqual(
            discovery.infer_permissions(
                "agentidentity.projects.locations.widgets.frobnicate", "agentidentity", CATALOG),
            [],
        )


class BuildDetail(unittest.TestCase):
    def setUp(self):
        resolved = {"status": "unlisted", "api": "agentidentity", "version": "v1", "doc": DOC}
        mapped = {"agentidentity.projects.locations.get": ["agentidentity.locations.get"]}
        self.detail = discovery.build_detail("agentidentity", resolved, CATALOG, mapped)
        self.by_id = {m["id"]: m for m in self.detail["methods"]}

    def test_flat_path_is_preferred(self):
        self.assertEqual(self.by_id["agentidentity.projects.locations.get"]["path"],
                         "v1/projects/{projectsId}/locations/{locationsId}")
        self.assertEqual(self.by_id["agentidentity.projects.locations.accessSummaries.list"]["path"],
                         "v1/{+parent}/accessSummaries")

    def test_map_is_preferred_over_inference_and_labelled(self):
        self.assertEqual(self.by_id["agentidentity.projects.locations.get"]["permissionSource"], "mapped")
        listed = self.by_id["agentidentity.projects.locations.accessSummaries.list"]
        self.assertEqual(listed["permissionSource"], "inferred")
        self.assertEqual(listed["resource"], "agentidentity.accessSummaries")

    def test_only_path_and_query_parameters_are_kept(self):
        names = [p["name"] for p in self.by_id["agentidentity.projects.locations.get"]["parameters"]]
        self.assertEqual(names, ["name"])

    def test_schemas_are_the_reachable_closure(self):
        schemas = self.detail["schemas"]
        self.assertEqual(set(schemas), {"Location", "ListAccessSummariesResponse", "AccessSummary"})
        self.assertTrue(schemas["AccessSummary"]["properties"]["createTime"]["readOnly"])


class Resolve(unittest.TestCase):
    def setUp(self):
        self.cache = Path(tempfile.mkdtemp())

    def probe(self, responses):
        def fake_get(url, timeout=30):
            version = url.rsplit("version=", 1)[-1]
            return responses.get(version, (404, b"<html>Not Found</html>"))

        with mock.patch.object(discovery, "_get", side_effect=fake_get):
            return discovery.resolve("svc", {}, {}, self.cache)

    def test_refusal_to_anonymous_callers_is_restricted(self):
        result = self.probe({"v1": (403, IDENTITY_REFUSAL)})
        self.assertEqual(result["status"], "restricted")
        self.assertEqual(len(result["tried"]), 1)
        detail = discovery.build_detail("svc", result, set(), {})
        self.assertEqual(detail["discovery"]["reasonKind"], "identity")

    def test_404_on_every_version_is_not_found(self):
        result = self.probe({})
        self.assertEqual(result["status"], "not_found")
        self.assertEqual(len(result["tried"]), len(discovery.PROBE_VERSIONS))

    def test_document_on_a_later_version_is_unlisted(self):
        body = json.dumps({"name": "svc", "rootUrl": "https://svc.googleapis.com/"}).encode()
        result = self.probe({"v1alpha": (200, body)})
        self.assertEqual((result["status"], result["version"]), ("unlisted", "v1alpha"))

    def test_network_failure_is_not_cached(self):
        result = self.probe({"v1": (None, b"timed out")})
        self.assertEqual(result["status"], "error")
        self.assertFalse((self.cache / "svc.json").exists())

    def test_listed_api_is_fetched_through_its_alias_and_preferred_version(self):
        directory = {"cloudresourcemanager": [
            {"version": "v1", "preferred": False, "discoveryRestUrl": "https://example.test/v1"},
            {"version": "v3", "preferred": True, "discoveryRestUrl": "https://example.test/v3"},
        ]}
        with mock.patch.object(discovery, "_get", return_value=(200, b'{"name": "cloudresourcemanager"}')) as get:
            result = discovery.resolve(
                "resourcemanager", directory, {"resourcemanager": "cloudresourcemanager"}, self.cache)
        self.assertEqual((result["status"], result["version"]), ("listed", "v3"))
        get.assert_called_once_with("https://example.test/v3")


class BrokenDirectoryLinks(unittest.TestCase):
    def setUp(self):
        self.cache = Path(tempfile.mkdtemp())

    def test_falls_back_to_another_listed_version(self):
        directory = {"dataproc": [
            {"version": "v2", "preferred": True, "discoveryRestUrl": "https://example.test/v2"},
            {"version": "v1", "preferred": False, "discoveryRestUrl": "https://example.test/v1"},
        ]}
        responses = {
            "https://example.test/v2": (404, b"not found"),
            "https://example.test/v1": (200, b'{"name": "dataproc"}'),
        }
        with mock.patch.object(discovery, "_get", side_effect=lambda url, timeout=30: responses[url]):
            result = discovery.resolve("dataproc", directory, {}, self.cache)
        self.assertEqual((result["status"], result["version"]), ("listed", "v1"))
        self.assertEqual([t["code"] for t in result["tried"]], [404, 200])

    def test_document_found_by_probing_a_listed_api_stays_listed(self):
        directory = {"integrations": [
            {"version": "v1", "preferred": True, "discoveryRestUrl": "https://example.test/v1"},
        ]}

        def fake_get(url, timeout=30):
            if url.endswith("version=v1alpha"):
                return 200, b'{"name": "integrations"}'
            return 404, b""

        with mock.patch.object(discovery, "_get", side_effect=fake_get):
            result = discovery.resolve("integrations", directory, {}, self.cache)
        self.assertEqual((result["status"], result["version"]), ("listed", "v1alpha"))

    def test_every_listed_version_missing_is_not_an_error(self):
        directory = {"integrations": [
            {"version": "v1", "preferred": True, "discoveryRestUrl": "https://example.test/v1"},
        ]}
        with mock.patch.object(discovery, "_get", return_value=(404, b"")):
            result = discovery.resolve("integrations", directory, {}, self.cache)
        self.assertEqual(result["status"], "not_found")
        self.assertTrue((self.cache / "integrations.json").exists())


class LoadMetadata(unittest.TestCase):
    def git(self, *args):
        subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True)

    def setUp(self):
        self.repo = Path(tempfile.mkdtemp())
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "test")
        (self.repo / "permissions.txt").write_text("a.b.get\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "init")

    def test_collector_without_metadata_returns_none(self):
        self.assertIsNone(history.load_metadata(self.repo, "permissions_metadata.jsonl"))

    def test_reads_committed_state_not_working_tree(self):
        path = self.repo / "permissions_metadata.jsonl"
        path.write_text(json.dumps({"name": "a.b.get", "stage": "BETA"}) + "\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "metadata")
        path.write_text(json.dumps({"name": "a.b.get", "stage": "GA"}) + "\n", encoding="utf-8")

        records = history.load_metadata(self.repo, "permissions_metadata.jsonl")
        self.assertEqual(records["a.b.get"]["stage"], "BETA")


if __name__ == "__main__":
    unittest.main()
