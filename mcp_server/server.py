"""MCP server that exposes the GCP IAM catalog to AI agents.

Knowledge only: every tool is read-only, answers from the index the observatory
container builds, holds no credentials and never calls Google. Acting on
infrastructure is left to the agent's own tools under the user's approval, which
keeps credentials and write access out of this process.
"""

from __future__ import annotations

from typing import Literal

import mcp.types as types
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .catalog import Catalog, IndexNotReady, NotFound

INSTRUCTIONS = """\
GCP IAM Catalog: a read-only reference for understanding and maintaining Google Cloud infrastructure.

How Google Cloud access fits together
- A service (storage, compute, aiplatform) exposes resources (storage.buckets) through REST API methods \
(storage.buckets.insert).
- Each method is authorised by one or more IAM permissions, named service.resource.verb \
(storage.buckets.create). The permission verb often differs from the method verb: insert -> create, patch -> update.
- Permissions are granted through roles bound to a principal on a project, folder, organization or single resource. \
Predefined roles bundle related permissions; basic roles (roles/owner, roles/editor, roles/viewer) are very broad; \
custom roles hold exactly what you choose.

How to use these tools
1. search to go from a task ("create a pub/sub topic") to a method id, permission, role or service.
2. get_method for the exact request and everything it needs; get_schema for deeper body fields.
3. get_permission and get_role for least privilege: prefer the narrowest predefined role that covers every permission \
the task needs, or a custom role; avoid basic roles.
4. whats_new, get_service and catalog_overview to understand new or unfamiliar services.

Acting on infrastructure
- This server holds no credentials and never calls Google. To make a change, use your own tools (gcloud, or the \
request templates from get_method) with the user's credentials, and show the user each change before making it.
- Read current state with get/list methods before changing anything, and confirm the target project.
- Request templates use UPPER_CASE placeholders; ACCESS_TOKEN comes from `gcloud auth print-access-token`.

How far to trust the data
- Permissions come from daily snapshots of Google's queryTestablePermissions. First-seen dates are only precise \
after firstSeenDatesPreciseSince in catalog_overview.
- That catalog is what Google lists as testable on a project. Most organization- and folder-level permissions \
(VPC Service Controls, organization policy, Assured Workloads) are absent from it; get_permission still returns \
role data for them, but no first-seen dates or API methods.
- Methods and schemas come from Google's Discovery documents. Services whose document is refused to anonymous \
callers (apiStatus "restricted") have no endpoints here.
- Method-to-permission links marked "inferred" were matched by name; confirm them with testIamPermissions.
- Role membership comes from the community iam-dataset and can lag behind brand-new permissions.
- The index is rebuilt every day (indexBuiltAt in catalog_overview). API spec changes are found by comparing each \
day's specs with the previous day's, so they are dated by the day they were first seen.
"""

READ_ONLY = types.ToolAnnotations.model_validate(
    {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
)


def create_server(catalog: Catalog) -> MCPServer:
    server = MCPServer(name="gcp-iam-catalog", title="GCP IAM Catalog", instructions=INSTRUCTIONS)

    def answer(call, *args) -> dict:
        try:
            return call(*args)
        except (IndexNotReady, NotFound, ValueError) as exc:
            # Expected misses become a message the agent can act on. Anything
            # else is a bug and is left to surface as one.
            raise ToolError(str(exc)) from exc

    @server.tool(annotations=READ_ONLY)
    def catalog_overview() -> dict:
        """Start here. What the catalog contains and how fresh it is: permission, service, role and API method
        counts, API document coverage, the last 30 days of changes, and Google's release rhythm by weekday."""
        return answer(catalog.overview)

    @server.tool(annotations=READ_ONLY)
    def search(
        query: str,
        kind: Literal["methods", "permissions", "roles", "services"] = "methods",
        service: str | None = None,
        limit: int = 10,
    ) -> dict:
        """Find API methods, IAM permissions, predefined roles or services by keywords. Plain task wording works
        ("create bucket", "set iam policy topic"); everyday verbs also match API verbs (create/insert, update/patch).
        `service` filters by permission prefix such as "compute". Pass ids from the results to get_method,
        get_permission, get_role or get_service."""
        return answer(catalog.search, query, kind, service, limit)

    @server.tool(annotations=READ_ONLY)
    def get_service(service: str) -> dict:
        """Everything about one service, by its permission prefix ("storage", "compute", "aiplatform"): API status
        and base URL, resources with their permission and method counts, predefined roles narrowest first, and
        recent changes in the catalog."""
        return answer(catalog.get_service, service)

    @server.tool(annotations=READ_ONLY)
    def get_method(method_id: str) -> dict:
        """Full detail for one API endpoint by Discovery method id ("storage.buckets.insert"): HTTP method and URL,
        parameters, request body and response fields, the IAM permissions it needs with the narrowest predefined
        roles that grant them, and ready request templates (raw HTTP and curl) with UPPER_CASE placeholders. Types
        nested deeper than two levels are left as expand hints for get_schema."""
        return answer(catalog.get_method, method_id)

    @server.tool(annotations=READ_ONLY)
    def get_schema(service: str, name: str, depth: int = 2) -> dict:
        """Fields of a request or response type named in get_method output, nested types expanded to `depth`
        levels (1-4)."""
        return answer(catalog.get_schema, service, name, depth)

    @server.tool(annotations=READ_ONLY)
    def get_permission(name: str) -> dict:
        """One IAM permission ("compute.instances.create"): when it first appeared, whether it was removed, its
        launch stage and description when collected, the API methods that require it, and the narrowest
        predefined roles that grant it."""
        return answer(catalog.get_permission, name)

    @server.tool(annotations=READ_ONLY)
    def get_role(name: str, service: str | None = None, limit: int = 300) -> dict:
        """A predefined role ("roles/storage.admin" or "storage.admin"): title, description, launch stage,
        permission counts per service, and its permissions. Use `service` to list one service's permissions from
        a broad role."""
        return answer(catalog.get_role, name, service, limit)

    @server.tool(annotations=READ_ONLY)
    def whats_new(
        days: int = 30,
        tiers: list[int] | None = None,
        service: str | None = None,
        include_removed: bool = False,
        limit: int = 50,
    ) -> dict:
        """Recent changes to the GCP IAM catalog, grouped into events. Tier 0 is a new service, 1 a new resource
        type on a known service, 2 new verbs on a known resource; the default is tiers 0 and 1. New permissions
        usually appear before the feature they belong to is documented. apiChanges lists changes to API specs in
        the same window: new or removed methods, new API versions, and specs that became public."""
        return answer(catalog.whats_new, days, tiers, service, include_removed, limit)

    return server
