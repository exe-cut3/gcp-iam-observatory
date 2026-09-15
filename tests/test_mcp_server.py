import json
import tempfile
import unittest
from pathlib import Path

from mcp import Client

from mcp_server.catalog import Catalog
from mcp_server.server import INSTRUCTIONS, create_server
from tests.test_catalog import build_dist

TOOLS = {
    "catalog_overview", "search", "get_service", "get_method",
    "get_schema", "get_permission", "get_role", "whats_new",
}


class McpServer(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        dist = Path(tempfile.mkdtemp())
        build_dist(dist)
        self.server = create_server(Catalog(dist))

    async def call(self, name, arguments):
        async with Client(self.server) as client:
            result = (await client.call_tool(name, arguments)).model_dump(by_alias=True)
        return result["isError"], result["content"][0]["text"]

    async def test_every_tool_is_read_only_and_described(self):
        async with Client(self.server) as client:
            self.assertEqual(client.instructions, INSTRUCTIONS)
            tools = (await client.list_tools()).model_dump(by_alias=True)["tools"]
        self.assertEqual({t["name"] for t in tools}, TOOLS)
        for tool in tools:
            self.assertTrue(tool["annotations"]["readOnlyHint"], tool["name"])
            self.assertFalse(tool["annotations"]["destructiveHint"], tool["name"])
            self.assertTrue(tool["description"], tool["name"])

    async def test_search_result_feeds_get_method(self):
        is_error, text = await self.call("search", {"query": "create a bucket"})
        self.assertFalse(is_error)
        method_id = json.loads(text)["results"][0]["id"]

        is_error, text = await self.call("get_method", {"method_id": method_id})
        self.assertFalse(is_error)
        self.assertEqual(json.loads(text)["requiredPermissions"][0]["name"], "storage.buckets.create")

    async def test_misses_come_back_as_actionable_errors(self):
        is_error, text = await self.call("get_method", {"method_id": "storage.buckets.frobnicate"})
        self.assertTrue(is_error)
        self.assertIn("search", text)

    async def test_invalid_arguments_are_rejected(self):
        is_error, _ = await self.call("search", {"query": "bucket", "kind": "endpoints"})
        self.assertTrue(is_error)


if __name__ == "__main__":
    unittest.main()
