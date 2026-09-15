import datetime
import json
import os
import tempfile
import unittest
from pathlib import Path

from mcp_server.catalog import Catalog, IndexNotReady, NotFound

TODAY = datetime.date.today()


def days_ago(n):
    return (TODAY - datetime.timedelta(days=n)).isoformat()


def build_dist(root: Path) -> None:
    def put(name, payload):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")

    put("events.json", {"events": [
        {"date": days_ago(5), "tier": 2, "tierLabel": "new capability", "change": "added", "service": "storage",
         "resource": "storage.buckets", "title": "storage.buckets", "permissions": ["storage.buckets.get"], "count": 1},
        {"date": days_ago(10), "tier": 0, "tierLabel": "new service", "change": "added", "service": "agentidentity",
         "resource": "", "title": "agentidentity", "permissions": ["agentidentity.authProviders.create"], "count": 1},
        {"date": days_ago(400), "tier": 1, "tierLabel": "new resource", "change": "added", "service": "storage",
         "resource": "storage.objects", "title": "storage.objects", "permissions": ["storage.objects.get"], "count": 1},
    ]})
    put("lookup.json", {"baseline": "2024-06-06", "catalogSize": 4, "permissions": {
        "storage.buckets.create": ["2024-06-06", 1, None, "GA"],
        "storage.buckets.get": ["2024-06-06", 1, None],
        "storage.objects.get": [days_ago(400), 0, None],
        "storage.legacy.get": ["2025-01-01", 0, "2026-01-01"],
        "accesscontextmanager.accessLevels.list": ["2024-06-06", 1, "2026-01-30"],
        "agentidentity.authProviders.create": [days_ago(10), 0, None, "BETA"],
    }})
    put("cadence.json", {"reliableFrom": "2026-05-07", "peakDay": "Thursday", "peakShare": 29.9,
                         "weekdays": [{"day": "Thursday", "added": 230, "snapshots": 9, "share": 29.9}]})
    put("apis.json", {"exploredDays": 0, "services": {
        "storage": {"status": "listed", "api": "storage", "version": "v1", "title": "Cloud Storage JSON API", "methods": 2},
        "compute": {"status": "listed", "api": "compute", "version": "v1", "title": "Compute Engine API", "methods": 1},
        "agentidentity": {"status": "listed", "api": "agentidentity", "version": "v1", "title": "Agent Identity API", "methods": 1},
    }})
    all_active = ["storage.buckets.create", "storage.buckets.get", "storage.objects.get",
                  "agentidentity.authProviders.create"]
    put("roles.json", {"roles": {
        "roles/owner": {"title": "Owner", "permissions": all_active},
        "roles/editor": {"title": "Editor", "permissions": ["storage.buckets.create", "storage.buckets.get"]},
        "roles/storage.admin": {"title": "Storage Admin", "stage": "GA",
                                "permissions": ["storage.buckets.create", "storage.buckets.get", "storage.objects.get"]},
        "roles/storage.bucketCreator": {"title": "Bucket Creator", "stage": "GA",
                                        "permissions": ["storage.buckets.create"]},
        "roles/storage.legacyCreator": {"title": "Legacy", "deleted": True, "permissions": ["storage.buckets.create"]},
        "roles/storagetransfer.serviceAgent": {"title": "Storage Transfer Service Agent",
                                               "permissions": ["storage.buckets.create"]},
        "roles/iam.oddRole": {"title": "Unrelated small role", "permissions": ["storage.buckets.create"]},
        "roles/accesscontextmanager.policyAdmin": {"title": "Access Context Manager Admin",
                                                   "permissions": ["accesscontextmanager.accessLevels.create",
                                                                   "accesscontextmanager.accessLevels.list"]},
    }})
    put("methods.json", {"fields": ["id", "service", "httpMethod", "path", "summary", "permissions"], "rows": [
        ["storage.buckets.insert", "storage", "POST", "b", "Creates a new bucket.", ["storage.buckets.create"]],
        ["storage.buckets.get", "storage", "GET", "b/{bucket}", "Returns metadata for the specified bucket.",
         ["storage.buckets.get"]],
        ["compute.backendBuckets.insert", "compute", "POST", "projects/{project}/global/backendBuckets",
         "Creates a BackendBucket resource.", ["compute.backendBuckets.create"]],
        ["agentidentity.projects.locations.authProviders.create", "agentidentity", "POST",
         "v1/projects/{projectsId}/locations/{locationsId}/authProviders", "Creates an auth provider.",
         ["agentidentity.authProviders.create"]],
        ["compute.instances.list", "compute", "GET", "projects/{project}/zones/{zone}/instances",
         "Retrieves the list of instances.", ["compute.instances.list"]],
        ["compute.instanceGroupManagers.aggregatedList", "compute", "GET",
         "projects/{project}/aggregated/instanceGroupManagers", "Retrieves the list of managed instance groups.",
         ["compute.instanceGroupManagers.list"]],
        ["compute.projects.moveInstance", "compute", "POST", "projects/{project}/moveInstance",
         "Moves an instance.", ["compute.addresses.create", "compute.instances.list", "compute.instances.create"]],
        ["container.projects.locations.clusters.create", "container", "POST",
         "v1/projects/{projectsId}/locations/{locationsId}/clusters", "Creates a cluster.",
         ["container.clusters.create"]],
        ["bigtableadmin.projects.instances.create", "bigtableadmin", "POST", "v2/projects/{projectsId}/instances",
         "Create an instance.", ["bigtable.instances.create", "bigtable.clusters.create"]],
    ]})
    put("detail/storage.json", {
        "service": "storage",
        "discovery": {"status": "listed", "api": "storage", "version": "v1", "title": "Cloud Storage JSON API",
                      "documentationLink": "https://cloud.google.com/storage/docs/json_api/",
                      "rootUrl": "https://storage.googleapis.com/", "servicePath": "storage/v1/",
                      "tried": [], "reason": None},
        "methods": [
            {"id": "storage.buckets.insert", "httpMethod": "POST", "path": "b", "description": "Creates a new bucket.",
             "parameters": [{"name": "project", "location": "query", "required": True, "type": "string",
                             "enum": None, "description": "A valid API project identifier."}],
             "request": "Bucket", "response": "Bucket", "permissions": ["storage.buckets.create"],
             "permissionSource": "mapped", "resource": "storage.buckets"},
            {"id": "storage.buckets.get", "httpMethod": "GET", "path": "b/{bucket}",
             "description": "Returns metadata for the specified bucket.",
             "parameters": [{"name": "bucket", "location": "path", "required": True, "type": "string",
                             "enum": None, "description": ""}],
             "request": None, "response": "Bucket", "permissions": ["storage.buckets.get"],
             "permissionSource": "mapped", "resource": "storage.buckets"},
        ],
        "schemas": {
            "Bucket": {"type": "object", "properties": {
                "name": {"type": "string", "description": "The name of the bucket."},
                "id": {"type": "string", "readOnly": True},
                "owner": {"$ref": "Owner"},
            }},
            "Owner": {"type": "object", "properties": {"entity": {"type": "string"}}},
        },
        "permissions": {},
    })
    put("detail/agentidentity.json", {
        "service": "agentidentity",
        "discovery": {"status": "listed", "api": "agentidentity", "version": "v1",
                      "rootUrl": "https://agentidentity.googleapis.com/", "servicePath": ""},
        "methods": [
            {"id": "agentidentity.projects.locations.authProviders.create", "httpMethod": "POST",
             "path": "v1/projects/{projectsId}/locations/{locationsId}/authProviders",
             "description": "Creates an auth provider.", "parameters": [], "request": None, "response": None,
             "permissions": ["agentidentity.authProviders.create"], "permissionSource": "inferred",
             "resource": "agentidentity.authProviders"},
        ],
        "schemas": {},
        "permissions": {"agentidentity.authProviders.create": {"title": "Create auth providers", "stage": "BETA"}},
    })
    put("meta.json", {
        "generatedAt": "2026-09-15T00:00:00Z",
        "coverage": {"catalogSize": 4, "firstSnapshot": "2024-06-06", "lastSnapshot": "2026-09-10",
                     "snapshots": 3, "reliableFrom": "2026-05-07"},
        "summary30": {"events": 2},
        "metadata": {"available": False, "permissions": 0, "stages": {}},
        "discovery": {"explored": 3, "days": 0, "byStatus": {"listed": 3}},
    })


class CatalogTest(unittest.TestCase):
    def setUp(self):
        self.dist = Path(tempfile.mkdtemp())
        build_dist(self.dist)
        self.catalog = Catalog(self.dist)


class Loading(CatalogTest):
    def test_missing_index_is_reported_as_not_ready(self):
        with self.assertRaises(IndexNotReady):
            Catalog(tempfile.mkdtemp()).overview()

    def test_rebuilt_index_is_picked_up(self):
        self.assertEqual(self.catalog.get_permission("storage.objects.get")["status"], "active")
        lookup_path = self.dist / "lookup.json"
        lookup = json.loads(lookup_path.read_text(encoding="utf-8"))
        lookup["permissions"]["storage.objects.get"][2] = "2026-09-01"
        lookup_path.write_text(json.dumps(lookup), encoding="utf-8")
        meta = self.dist / "meta.json"
        stat = meta.stat()
        os.utime(meta, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))

        self.assertIn("2026-09-01", self.catalog.get_permission("storage.objects.get")["status"])

    def test_overview_counts_live_roles_and_methods(self):
        overview = self.catalog.overview()
        self.assertEqual(overview["catalog"]["apiMethods"], 9)
        self.assertEqual(overview["catalog"]["predefinedRoles"], 7)
        self.assertEqual(overview["releaseCadence"]["peakWeekday"], "Thursday")


class Search(CatalogTest):
    def test_everyday_verbs_find_discovery_verbs_and_shallow_paths_rank_first(self):
        results = self.catalog.search("create a bucket")["results"]
        ids = [r["id"] for r in results]
        self.assertEqual(ids[0], "storage.buckets.insert")
        self.assertTrue(results[0]["matchedAllTerms"])
        self.assertLess(ids.index("storage.buckets.insert"), ids.index("compute.backendBuckets.insert"))

    def test_direct_methods_outrank_ones_that_merely_need_the_permission(self):
        self.assertEqual(self.catalog.search("list compute instances")["results"][0]["id"], "compute.instances.list")

    def test_product_names_expand_to_service_and_resource(self):
        self.assertEqual(self.catalog.search("create gke cluster")["results"][0]["id"],
                         "container.projects.locations.clusters.create")

    def test_service_filter(self):
        ids = [r["id"] for r in self.catalog.search("bucket", service="compute")["results"]]
        self.assertEqual(ids, ["compute.backendBuckets.insert"])

    def test_other_kinds(self):
        self.assertEqual(self.catalog.search("bucket creator", kind="roles")["results"][0]["role"],
                         "roles/storage.bucketCreator")
        self.assertEqual(self.catalog.search("agentidentity", kind="services")["results"][0]["service"],
                         "agentidentity")
        self.assertEqual(self.catalog.search("buckets create", kind="permissions")["results"][0]["name"],
                         "storage.buckets.create")

    def test_rejects_unknown_kind_and_empty_query(self):
        with self.assertRaises(ValueError):
            self.catalog.search("bucket", kind="endpoints")
        with self.assertRaises(ValueError):
            self.catalog.search("the a of")


class GetMethod(CatalogTest):
    def setUp(self):
        super().setUp()
        self.method = self.catalog.get_method("storage.buckets.insert")

    def test_narrowest_live_role_comes_first_and_basic_roles_are_separate(self):
        permission = self.method["requiredPermissions"][0]
        self.assertEqual(permission["stage"], "GA")
        granted = permission["grantedBy"]
        roles = [r["role"] for r in granted["narrowest"]]
        self.assertEqual(roles, ["roles/storage.bucketCreator", "roles/storage.admin", "roles/iam.oddRole"])
        self.assertEqual([r["sameService"] for r in granted["narrowest"]], [True, True, False])
        self.assertEqual(granted["serviceAgentRolesExcluded"], 1)
        self.assertEqual(granted["basicRoles"], ["roles/owner", "roles/editor"])

    def test_raw_http_is_exact(self):
        head, body = self.method["request"]["rawHttp"].split("\r\n\r\n", 1)
        lines = head.split("\r\n")
        self.assertEqual(lines[0], "POST /storage/v1/b?project=PROJECT_ID HTTP/1.1")
        self.assertEqual(lines[1], "Host: storage.googleapis.com")
        self.assertIn(f"Content-Length: {len(body.encode('utf-8'))}", lines)
        self.assertEqual(json.loads(body), {"name": "", "owner": {"entity": ""}})

    def test_curl_targets_the_same_url(self):
        self.assertIn('"https://storage.googleapis.com/storage/v1/b?project=PROJECT_ID"', self.method["request"]["curl"])

    def test_schema_depth_is_bounded(self):
        self.assertEqual(self.method["requestBody"]["owner"]["fields"]["entity"]["type"], "string")
        self.assertTrue(self.method["requestBody"]["id"]["outputOnly"])
        self.assertIn("expand", self.method["response"]["owner"])

    def test_inferred_permission_is_flagged(self):
        method = self.catalog.get_method("agentidentity.projects.locations.authProviders.create")
        self.assertTrue(any("inferred" in note for note in method["notes"]))

    def test_unknown_method(self):
        with self.assertRaises(NotFound):
            self.catalog.get_method("storage.buckets.frobnicate")

    def test_get_schema_expands_on_request(self):
        schema = self.catalog.get_schema("storage", "Bucket", depth=2)
        self.assertEqual(schema["fields"]["owner"]["fields"]["entity"]["type"], "string")


class Permissions(CatalogTest):
    def test_permission_only_in_basic_roles_points_to_custom_roles(self):
        permission = self.catalog.get_permission("agentidentity.authProviders.create")
        self.assertEqual(permission["firstSeen"], days_ago(10))
        self.assertEqual(permission["stage"], "BETA")
        self.assertEqual(permission["metadata"]["title"], "Create auth providers")
        self.assertEqual(permission["grantedBy"]["basicRoles"], ["roles/owner"])
        self.assertIn("custom role", permission["grantedBy"]["note"])
        self.assertEqual(permission["requiredBy"][0]["id"], "agentidentity.projects.locations.authProviders.create")

    def test_permission_that_left_the_project_catalog_but_is_still_in_roles_is_not_called_removed(self):
        status = self.catalog.get_permission("accesscontextmanager.accessLevels.list")["status"]
        self.assertTrue(status.startswith("not listed as testable on a project since 2026-01-30"), status)

    def test_org_level_permission_outside_the_project_catalog_still_gets_role_advice(self):
        permission = self.catalog.get_permission("accesscontextmanager.accessLevels.create")
        self.assertFalse(permission["inProjectCatalog"])
        self.assertEqual(permission["grantedBy"]["narrowest"][0]["role"], "roles/accesscontextmanager.policyAdmin")

    def test_removed_and_unknown_permissions(self):
        removed = self.catalog.get_permission("storage.legacy.get")
        self.assertEqual(removed["status"], "removed on 2026-01-01")
        self.assertIn("No predefined role", removed["grantedBy"]["note"])
        with self.assertRaises(NotFound):
            self.catalog.get_permission("nothing.here.get")


class ServicesAndRoles(CatalogTest):
    def test_service_merges_permissions_methods_and_roles(self):
        service = self.catalog.get_service("storage")
        self.assertEqual(service["api"]["baseUrl"], "https://storage.googleapis.com/storage/v1/")
        resources = {r["resource"]: r for r in service["resources"]}
        self.assertEqual(resources["storage.buckets"], {"resource": "storage.buckets", "permissions": 2, "methods": 2})
        self.assertEqual(service["permissions"], {
            "active": 3, "removed": 1, "firstAppeared": "predates the record, which starts 2024-06-06"})
        self.assertEqual([r["role"] for r in service["roles"]], ["roles/storage.bucketCreator", "roles/storage.admin"])

    def test_role_lookup_accepts_short_names_and_filters_by_service(self):
        self.assertEqual(self.catalog.get_role("storage.admin")["permissionCount"], 3)
        owner = self.catalog.get_role("roles/owner", service="agentidentity")
        self.assertEqual(owner["permissions"], ["agentidentity.authProviders.create"])
        self.assertIn("warning", owner)


class WhatsNew(CatalogTest):
    def test_defaults_to_services_and_resources_in_the_last_30_days(self):
        self.assertEqual([e["subject"] for e in self.catalog.whats_new()["events"]], ["agentidentity"])

    def test_tiers_and_window_are_respected(self):
        self.assertEqual(
            [e["subject"] for e in self.catalog.whats_new(days=30, tiers=[0, 1, 2])["events"]],
            ["storage.buckets", "agentidentity"],
        )
        self.assertEqual([e["subject"] for e in self.catalog.whats_new(days=500, tiers=[1])["events"]],
                         ["storage.objects"])
        with self.assertRaises(ValueError):
            self.catalog.whats_new(tiers=[5])


if __name__ == "__main__":
    unittest.main()
