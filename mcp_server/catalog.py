"""Query layer over the observatory index, shaped for AI agents.

The dashboard serves a person scanning a page. An agent maintaining
infrastructure asks narrower questions and pays for every token of the answer:
which endpoint does this task, what does it need, which is the narrowest role
that grants that, and exactly what to send. This module answers those from the
same index the dashboard reads. It has no MCP dependency so it can be tested on
its own; server.py only exposes it as tools.

Read-only, holds no credentials, and never calls Google.
"""

from __future__ import annotations

import datetime
import json
import re
import threading
from collections import Counter, OrderedDict, defaultdict
from pathlib import Path
from urllib.parse import urlsplit

BASIC_ROLES = ("roles/owner", "roles/editor", "roles/viewer")
SERVICE_NAME = re.compile(r"^[a-z][a-z0-9-]*$")
MAX_LIMIT = 50
DETAIL_CACHE_SIZE = 32

TIER_DESCRIPTIONS = {
    0: "new service: a permission prefix never seen before",
    1: "new resource: a known service exposes a new resource type",
    2: "new capability: a known resource gains new verbs",
}

STATUS_MEANING = {
    "listed": "Public Discovery document, listed in Google's API directory.",
    "unlisted": "Public Discovery document, but the API is not listed in Google's directory.",
    "restricted": "The API host exists but will not serve its Discovery document to anonymous callers, "
                  "so its endpoints are not in this catalog. That alone does not prove the API is private.",
    "not_found": "Nothing answers at <service>.googleapis.com; these permissions may guard an API "
                 "published under another name.",
    "error": "Google could not be reached for this API during the last index build.",
    "not_explored": "No API document has been fetched for this service.",
}

STOPWORDS = frozenset({
    "a", "an", "and", "all", "by", "do", "for", "from", "how", "i", "in", "is", "it",
    "my", "of", "on", "or", "sub", "the", "to", "with",
})

# Discovery verbs and everyday wording differ: compute and storage "insert" what
# most APIs "create", and updates are usually exposed as "patch".
SYNONYMS = {
    "create": ("insert", "add"),
    "add": ("create", "insert"),
    "make": ("create", "insert"),
    "insert": ("create",),
    "update": ("patch",),
    "modify": ("update", "patch"),
    "change": ("update", "patch"),
    "edit": ("update", "patch"),
    "patch": ("update",),
    "remove": ("delete",),
    "delete": ("remove",),
    "read": ("get",),
    "show": ("get", "list"),
}

_CAMEL = re.compile(r"([a-z0-9])([A-Z])")
_NON_WORD = re.compile(r"[^a-z0-9]+")


class IndexNotReady(RuntimeError):
    pass


class NotFound(LookupError):
    pass


def _stem(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def token_list(text: str) -> list[str]:
    words = _NON_WORD.split(_CAMEL.sub(r"\1 \2", text or "").lower())
    seen: set[str] = set()
    out: list[str] = []
    for word in words:
        if word and word not in STOPWORDS:
            stem = _stem(word)
            if stem not in seen:
                seen.add(stem)
                out.append(stem)
    return out


def tokens(text: str) -> set[str]:
    return set(token_list(text))


# Product names people use for services whose ids say something else. Each
# expands into every word the matching method ids contain, so "create gke
# cluster" requires container and cluster rather than matching any cluster.
# ("pub/sub" splits into "pub" and "sub"; "sub" is a stopword.)
EXPANSIONS = {
    _stem(word): tuple(_stem(w) for w in words)
    for word, words in {
        "gke": ("container", "cluster"),
        "kubernetes": ("container",),
        "vm": ("compute", "instance"),
        "vms": ("compute", "instance"),
        "gcs": ("storage",),
        "kms": ("cloudkms",),
        "bq": ("bigquery",),
        "pub": ("pubsub",),
        "secret": ("secretmanager", "secret"),
        "functions": ("cloudfunctions", "function"),
    }.items()
}


def query_terms(query: str) -> list[set[str]]:
    terms: list[set[str]] = []
    seen: set[str] = set()
    for token in token_list(query):
        for word in EXPANSIONS.get(token, (token,)):
            if word not in seen:
                seen.add(word)
                terms.append({word, *(_stem(s) for s in SYNONYMS.get(word, ()))})
    return terms


def _score(terms: list[set[str]], strong: set[str], medium: set[str], weak: set[str]) -> tuple[int, int]:
    matched = score = 0
    for alternatives in terms:
        for weight, pool in ((3, strong), (2, medium), (1, weak)):
            if alternatives & pool:
                matched += 1
                score += weight
                break
    return matched, score


def _clip(text: str | None, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _role_service(role: str) -> str:
    return role.removeprefix("roles/").split(".")[0]


def _limit(value, default: int, maximum: int = MAX_LIMIT) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(number, maximum))


# ---------------------------------------------------------------------------
# Request templates (mirror of buildRequest in web/app.js)
# ---------------------------------------------------------------------------

def _placeholder(name: str) -> str:
    if re.fullmatch(r"projects?(Id)?", name):
        return "PROJECT_ID"
    if re.fullmatch(r"locations?(Id)?", name):
        return "LOCATION"
    return re.sub(r"[^A-Za-z0-9_]", "_", _CAMEL.sub(r"\1_\2", name)).upper()


def _body_skeleton(ref: str, schemas: dict, depth: int = 0) -> dict:
    schema = schemas.get(ref)
    if not schema or depth > 2:
        return {}
    out = {}
    for field, spec in (schema.get("properties") or {}).items():
        if spec.get("readOnly"):
            continue
        if spec.get("$ref"):
            out[field] = _body_skeleton(spec["$ref"], schemas, depth + 1)
        else:
            out[field] = {"array": [], "boolean": False, "integer": 0, "number": 0, "object": {}}.get(
                spec.get("type"), "")
    return out


def build_request(method: dict, discovery: dict, schemas: dict) -> dict:
    path = re.sub(r"\{\+?([^}]+)\}", lambda m: _placeholder(m.group(1)), method["path"])
    url = f"{discovery.get('rootUrl') or ''}{discovery.get('servicePath') or ''}{path}"
    required = [p for p in method["parameters"] if p["location"] == "query" and p["required"]]
    if required:
        url += "?" + "&".join(f"{p['name']}={_placeholder(p['name'])}" for p in required)
    body = json.dumps(_body_skeleton(method["request"], schemas), indent=2) if method.get("request") else None
    headers = [("Content-Type", "application/json")] if body is not None else []
    return {"httpMethod": method["httpMethod"], "url": url, "headers": headers, "body": body}


def format_raw_http(req: dict) -> str:
    target = urlsplit(req["url"])
    lines = [
        f"{req['httpMethod']} {target.path}{'?' + target.query if target.query else ''} HTTP/1.1",
        f"Host: {target.netloc}",
        "Authorization: Bearer ACCESS_TOKEN",
        *(f"{name}: {value}" for name, value in req["headers"]),
    ]
    if req["body"] is not None:
        lines.append(f"Content-Length: {len(req['body'].encode('utf-8'))}")
    return "\r\n".join(lines) + "\r\n\r\n" + (req["body"] or "")


def format_curl(req: dict) -> str:
    lines = [
        f"curl -X {req['httpMethod']} \\",
        '  -H "Authorization: Bearer $(gcloud auth print-access-token)" \\',
    ]
    lines += [f'  -H "{name}: {value}" \\' for name, value in req["headers"]]
    if req["body"] is not None:
        indented = req["body"].replace("\n", "\n  ")
        lines.append(f"  -d '{indented}' \\")
    lines.append('  "' + req["url"] + '"')
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

def _type_label(spec: dict) -> str:
    if spec.get("$ref"):
        return spec["$ref"]
    if spec.get("type") == "array":
        items = spec.get("items") or {}
        return f"{items.get('$ref') or items.get('type') or 'any'}[]"
    label = spec.get("type") or "any"
    return f"{label} ({spec['format']})" if spec.get("format") else label


def schema_shape(ref: str, schemas: dict, max_depth: int, _depth: int = 0, _seen: frozenset = frozenset()) -> dict:
    """Fields of a schema, nested types expanded down to max_depth levels.

    Anything deeper is left as an `expand` hint rather than inlined, because a
    fully expanded compute Instance runs to many thousands of tokens.
    """
    schema = schemas.get(ref)
    if schema is None:
        return {"note": f"definition of {ref} is not included in the index"}
    seen = _seen | {ref}
    fields = {}
    for name, spec in (schema.get("properties") or {}).items():
        field = {"type": _type_label(spec)}
        if spec.get("readOnly"):
            field["outputOnly"] = True
        if spec.get("enum"):
            field["enum"] = spec["enum"][:12]
        if spec.get("description"):
            field["description"] = _clip(spec["description"], 200)
        nested = spec.get("$ref") or (spec.get("items") or {}).get("$ref")
        if nested and nested in schemas:
            if _depth + 1 < max_depth and nested not in seen:
                field["fields"] = schema_shape(nested, schemas, max_depth, _depth + 1, seen)
            else:
                field["expand"] = f"get_schema(service, name='{nested}')"
        fields[name] = field
    return fields


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

class Catalog:
    def __init__(self, dist: Path | str):
        self.dist = Path(dist)
        self._lock = threading.Lock()
        self._marker: int | None = None
        self._details: OrderedDict[str, dict | None] = OrderedDict()

    # ---------- loading ----------

    def _read(self, name: str, default=None):
        path = self.dist / name
        if default is not None and not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))

    def _ensure_loaded(self) -> None:
        try:
            marker = (self.dist / "meta.json").stat().st_mtime_ns
        except FileNotFoundError:
            raise IndexNotReady(
                "The index has not been built yet. The observatory container builds it on start; retry shortly."
            ) from None
        if marker == self._marker:
            return
        # meta.json is the last file a build writes, so a changed mtime means a
        # complete new index is on disk.
        with self._lock:
            if marker != self._marker:
                self._load()
                self._marker = marker

    def _load(self) -> None:
        self.meta = self._read("meta.json")
        self.events = self._read("events.json")["events"]
        lookup = self._read("lookup.json")
        self.baseline = lookup["baseline"]
        self.lookup = lookup["permissions"]
        self.cadence = self._read("cadence.json")
        self.apis = self._read("apis.json", {"services": {}}).get("services", {})
        self.roles = self._read("roles.json", {"roles": {}}).get("roles", {})
        index = self._read("methods.json", {"fields": [], "rows": []})
        self.methods = [dict(zip(index["fields"], row)) for row in index["rows"]]

        self.method_by_id = {m["id"]: m for m in self.methods}
        self.methods_by_permission: dict[str, list[str]] = defaultdict(list)
        self._method_tokens = []
        for m in self.methods:
            for permission in m["permissions"]:
                self.methods_by_permission[permission].append(m["id"])
            # A method's id says what it does, and its first permission usually says
            # the same in IAM terms. Secondary permissions and prose only hint; weighing
            # them equally let moveInstance, which needs two dozen permissions, outrank
            # instances.list for "list compute instances".
            primary = tokens(m["permissions"][0]) if m["permissions"] else set()
            weak = tokens(m["summary"]) | tokens(re.sub(r"\{[^}]*\}", " ", m["path"]))
            for permission in m["permissions"][1:]:
                weak |= tokens(permission)
            self._method_tokens.append((tokens(m["id"]), primary, weak))

        self.roles_by_permission: dict[str, list[str]] = defaultdict(list)
        self._role_tokens = {}
        for role, info in self.roles.items():
            for permission in info.get("permissions", []):
                self.roles_by_permission[permission].append(role)
            self._role_tokens[role] = (
                tokens(role),
                tokens(f"{info.get('title', '')} {info.get('description', '')}"),
            )

        self._permission_tokens = {name: tokens(name) for name in self.lookup}
        self.active_services = sorted({name.split(".")[0] for name, rec in self.lookup.items() if not rec[2]})
        self._details.clear()

    def _detail(self, service: str) -> dict | None:
        if not SERVICE_NAME.match(service or ""):
            return None
        with self._lock:
            if service in self._details:
                self._details.move_to_end(service)
                return self._details[service]
            path = self.dist / "detail" / f"{service}.json"
            detail = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
            self._details[service] = detail
            if len(self._details) > DETAIL_CACHE_SIZE:
                self._details.popitem(last=False)
            return detail

    # ---------- shared pieces ----------

    def _api_block(self, service: str, discovery: dict | None) -> dict:
        if not discovery:
            status = (self.apis.get(service) or {}).get("status", "not_explored")
            return {"status": status, "meaning": STATUS_MEANING.get(status, "")}
        block = {k: discovery[k] for k in ("status", "api", "version", "title", "documentationLink") if discovery.get(k)}
        if discovery.get("rootUrl"):
            block["baseUrl"] = discovery["rootUrl"] + (discovery.get("servicePath") or "")
        block["meaning"] = STATUS_MEANING.get(discovery["status"], "")
        if discovery.get("reason"):
            block["googleSaid"] = discovery["reason"]
        return block

    def _role_size(self, role: str) -> int:
        return len(self.roles.get(role, {}).get("permissions", []))

    def _is_service_agent_role(self, role: str) -> bool:
        title = self.roles.get(role, {}).get("title", "")
        return "serviceagent" in role.lower() or "service agent" in title.lower()

    def _status(self, name: str, rec: list) -> str:
        if not rec[2]:
            return "active"
        if name in self.roles_by_permission:
            # Organization-level services left the project catalog wholesale without
            # being retired; role data still listing the permission is the tell.
            return (f"not listed as testable on a project since {rec[2]}, but still granted by predefined roles, "
                    "so it most likely still exists at organization or folder level")
        return f"removed on {rec[2]}"

    def _granting_roles(self, permission: str, limit: int = 5) -> dict:
        roles = [r for r in self.roles_by_permission.get(permission, []) if not self.roles.get(r, {}).get("deleted")]
        if not roles:
            return {
                "note": "No predefined role grants this permission in the upstream role data. It may only be "
                        "grantable through a custom role, or the data may lag behind a new permission.",
            }
        service = permission.split(".")[0]
        predefined = [r for r in roles if r not in BASIC_ROLES]
        agents = [r for r in predefined if self._is_service_agent_role(r)]
        # Service agent roles belong to Google-managed service accounts and must not
        # be granted to your own principals, however small they are. Among the rest,
        # the permission's own service comes first: a storagetransfer role that happens
        # to contain pubsub.topics.create is small, but it is the wrong role for Pub/Sub.
        narrow = sorted(
            (r for r in predefined if r not in agents),
            key=lambda r: (_role_service(r) != service, self._role_size(r), r),
        )
        result = {
            "narrowest": [
                {
                    "role": r,
                    "title": self.roles[r].get("title", ""),
                    "permissionCount": self._role_size(r),
                    "sameService": _role_service(r) == service,
                }
                for r in narrow[:limit]
            ],
            "predefinedRolesTotal": len(narrow),
            "basicRoles": [r for r in BASIC_ROLES if r in roles],
        }
        if agents:
            result["serviceAgentRolesExcluded"] = len(agents)
        if not narrow:
            # Common for new permissions: roles/owner picks them up long before any
            # predefined role does, and granting owner for one permission is the
            # opposite of least privilege.
            result["note"] = ("No role suitable for your own principals grants this permission in the upstream "
                              "role data; only basic or service agent roles do. For least privilege, put it in a "
                              "custom role (check that custom roles support it).")
        return result

    def _permission_brief(self, name: str, source: str | None) -> dict:
        brief: dict = {"name": name}
        if source:
            brief["source"] = source
        rec = self.lookup.get(name)
        if rec is None:
            brief["inCatalog"] = False
        else:
            if len(rec) > 3 and rec[3]:
                brief["stage"] = rec[3]
            if rec[2]:
                brief["removedFromCatalog"] = rec[2]
        brief["grantedBy"] = self._granting_roles(name)
        return brief

    def _event(self, event: dict) -> dict:
        return {
            "date": event["date"],
            "weekday": datetime.date.fromisoformat(event["date"]).strftime("%A"),
            "change": event["change"],
            "tier": event["tier"],
            "kind": event["tierLabel"],
            "service": event["service"],
            "subject": event["title"],
            "permissionCount": event["count"],
            "permissions": event["permissions"][:15],
            "apiStatus": (self.apis.get(event["service"]) or {}).get("status", "not_explored"),
        }

    # ---------- tools ----------

    def overview(self) -> dict:
        self._ensure_loaded()
        coverage = self.meta["coverage"]
        return {
            "indexBuiltAt": self.meta["generatedAt"],
            "catalog": {
                "activePermissions": coverage["catalogSize"],
                "services": len(self.active_services),
                "predefinedRoles": sum(1 for r in self.roles.values() if not r.get("deleted")),
                "apiMethods": len(self.methods),
            },
            "record": {
                "firstSnapshot": coverage["firstSnapshot"],
                "lastSnapshot": coverage["lastSnapshot"],
                "snapshots": coverage["snapshots"],
                "firstSeenDatesPreciseSince": coverage["reliableFrom"],
            },
            "apiDocuments": {
                status: {"services": count, "meaning": STATUS_MEANING.get(status, "")}
                for status, count in (self.meta.get("discovery") or {}).get("byStatus", {}).items()
            },
            "permissionMetadata": self.meta.get("metadata"),
            "last30Days": self.meta.get("summary30"),
            "noveltyTiers": {str(tier): text for tier, text in TIER_DESCRIPTIONS.items()},
            "releaseCadence": {
                "peakWeekday": self.cadence.get("peakDay"),
                "peakShare": self.cadence.get("peakShare"),
                "measuredSince": self.cadence.get("reliableFrom"),
                "shareByWeekday": {w["day"]: w["share"] for w in self.cadence.get("weekdays", [])},
            },
        }

    def search(self, query: str, kind: str = "methods", service: str | None = None, limit: int = 10) -> dict:
        self._ensure_loaded()
        terms = query_terms(query)
        if not terms:
            raise ValueError("The query has no searchable words.")
        searchers = {
            "methods": self._search_methods,
            "permissions": self._search_permissions,
            "roles": self._search_roles,
            "services": self._search_services,
        }
        if kind not in searchers:
            raise ValueError(f"kind must be one of: {', '.join(searchers)}")
        results = searchers[kind](terms, service or None)
        limit = _limit(limit, 10)
        return {
            "query": query,
            "kind": kind,
            "service": service or None,
            "total": len(results),
            "results": results[:limit],
        }

    def _search_methods(self, terms, service):
        scored = []
        for method, (strong, medium, weak) in zip(self.methods, self._method_tokens):
            if service and method["service"] != service:
                continue
            matched, score = _score(terms, strong, medium, weak)
            if matched:
                # Among equal matches, fewer words in the id means a more direct
                # method: instances.list before instanceGroupManagers.aggregatedList.
                key = (-matched, -score, len(strong), method["path"].count("/"), method["id"])
                scored.append((key, matched, method))
        scored.sort(key=lambda item: item[0])
        return [
            {
                "id": m["id"],
                "service": m["service"],
                "httpMethod": m["httpMethod"],
                "path": m["path"],
                "summary": m["summary"],
                "permissions": m["permissions"],
                "matchedAllTerms": matched == len(terms),
            }
            for _, matched, m in scored
        ]

    def _search_permissions(self, terms, service):
        scored = []
        for name, name_tokens in self._permission_tokens.items():
            if service and name.split(".")[0] != service:
                continue
            matched, score = _score(terms, name_tokens, set(), set())
            if matched:
                scored.append(((-matched, -score, name), name))
        scored.sort(key=lambda item: item[0])
        results = []
        for _, name in scored:
            rec = self.lookup[name]
            item = {"name": name, "firstSeen": None if rec[1] else rec[0], "predatesRecord": bool(rec[1])}
            if rec[2]:
                item["removed"] = rec[2]
            if len(rec) > 3 and rec[3]:
                item["stage"] = rec[3]
            results.append(item)
        return results

    def _search_roles(self, terms, service):
        scored = []
        for role, (strong, weak) in self._role_tokens.items():
            if service and _role_service(role) != service:
                continue
            matched, score = _score(terms, strong, set(), weak)
            if matched:
                scored.append(((-matched, -score, self._role_size(role), role), role))
        scored.sort(key=lambda item: item[0])
        return [
            {
                "role": role,
                "title": self.roles[role].get("title", ""),
                "stage": self.roles[role].get("stage", ""),
                "deleted": bool(self.roles[role].get("deleted")),
                "permissionCount": self._role_size(role),
            }
            for _, role in scored
        ]

    def _search_services(self, terms, service):
        scored = []
        for name in self.active_services:
            info = self.apis.get(name) or {}
            strong = tokens(name) | tokens(info.get("api") or "")
            matched, score = _score(terms, strong, set(), tokens(info.get("title") or ""))
            if matched:
                scored.append(((-matched, -score, name), name, info))
        scored.sort(key=lambda item: item[0])
        return [
            {
                "service": name,
                "title": info.get("title"),
                "apiStatus": info.get("status", "not_explored"),
                "methods": info.get("methods", 0),
            }
            for _, name, info in scored
        ]

    def get_service(self, service: str) -> dict:
        self._ensure_loaded()
        permissions = [name for name in self.lookup if name.split(".")[0] == service]
        if not permissions:
            raise NotFound(f"No permissions start with '{service}.'. Use search(query, kind='services').")
        active = [name for name in permissions if not self.lookup[name][2]]
        detail = self._detail(service) or {}
        methods = detail.get("methods", [])

        resources: dict[str, dict] = defaultdict(lambda: {"permissions": 0, "methods": 0})
        for name in active:
            resources[name.rsplit(".", 1)[0]]["permissions"] += 1
        for method in methods:
            resources[method["resource"]]["methods"] += 1

        records = [self.lookup[name] for name in permissions]
        if any(rec[1] for rec in records):
            first = f"predates the record, which starts {self.baseline}"
        else:
            first = min(rec[0] for rec in records)

        roles = sorted(
            (
                {
                    "role": role,
                    "title": info.get("title", ""),
                    "stage": info.get("stage", ""),
                    "permissionCount": len(info.get("permissions", [])),
                    "serviceAgent": self._is_service_agent_role(role),
                }
                for role, info in self.roles.items()
                if _role_service(role) == service and not info.get("deleted")
            ),
            key=lambda r: (r["serviceAgent"], r["permissionCount"], r["role"]),
        )
        return {
            "service": service,
            "api": self._api_block(service, detail.get("discovery")),
            "permissions": {"active": len(active), "removed": len(permissions) - len(active), "firstAppeared": first},
            "resources": [{"resource": name, **counts} for name, counts in sorted(resources.items())],
            "roles": roles,
            "recentChanges": [self._event(e) for e in self.events if e["service"] == service][:10],
        }

    def get_method(self, method_id: str) -> dict:
        self._ensure_loaded()
        row = self.method_by_id.get(method_id)
        detail = self._detail(row["service"]) if row else None
        method = next((m for m in (detail or {}).get("methods", []) if m["id"] == method_id), None)
        if method is None:
            raise NotFound(f"Unknown method '{method_id}'. Use search(query, kind='methods') to find method ids.")

        discovery = detail["discovery"]
        schemas = detail.get("schemas", {})
        req = build_request(method, discovery, schemas)
        result = {
            "id": method["id"],
            "service": row["service"],
            "api": self._api_block(row["service"], discovery),
            "httpMethod": method["httpMethod"],
            "path": method["path"],
            "url": req["url"],
            "description": method["description"],
            "parameters": [{k: v for k, v in p.items() if v not in (None, "")} for p in method["parameters"]],
            "requiredPermissions": [
                self._permission_brief(name, method["permissionSource"]) for name in method["permissions"]
            ],
            "requestBody": schema_shape(method["request"], schemas, max_depth=2) if method["request"] else None,
            "response": schema_shape(method["response"], schemas, max_depth=1) if method["response"] else None,
            "request": {
                "rawHttp": format_raw_http(req),
                "curl": format_curl(req),
                "howToFill": "Replace ACCESS_TOKEN with the output of `gcloud auth print-access-token`; "
                             "UPPER_CASE values are placeholders. The body lists writable fields with empty values.",
            },
        }
        notes = []
        if method["permissionSource"] == "inferred":
            notes.append("The required permission was inferred from the method name because the upstream method "
                         "map does not cover this API. Confirm it with testIamPermissions or the API docs.")
        if not method["permissions"]:
            notes.append("The required IAM permission for this method could not be determined.")
        if notes:
            result["notes"] = notes
        return result

    def get_schema(self, service: str, name: str, depth: int = 2) -> dict:
        self._ensure_loaded()
        detail = self._detail(service)
        schemas = (detail or {}).get("schemas", {})
        if name not in schemas:
            raise NotFound(f"No schema '{name}' for service '{service}'. Schema names appear as types in get_method.")
        return {
            "service": service,
            "schema": name,
            "description": schemas[name].get("description", ""),
            "fields": schema_shape(name, schemas, max_depth=_limit(depth, 2, 4)),
        }

    def get_permission(self, name: str) -> dict:
        self._ensure_loaded()
        rec = self.lookup.get(name)
        if rec is None:
            if name not in self.roles_by_permission:
                raise NotFound(f"'{name}' is not in the permission catalog or the role data. "
                               "Use search(query, kind='permissions').")
            # The catalog is what Google lists as testable on a project, so most
            # organization- and folder-level permissions never appear in it even
            # though roles grant them (accesscontextmanager, for example).
            return {
                "name": name,
                "service": name.split(".")[0],
                "resource": name.rsplit(".", 1)[0],
                "inProjectCatalog": False,
                "note": "Granted by predefined roles but not listed as testable on a project, which usually means it "
                        "applies at organization or folder level. First-seen dates and API methods are not available.",
                "grantedBy": self._granting_roles(name),
            }
        service = name.split(".")[0]
        result: dict = {
            "name": name,
            "service": service,
            "resource": name.rsplit(".", 1)[0],
            "firstSeen": f"predates the record, which starts {self.baseline}" if rec[1] else rec[0],
            "status": self._status(name, rec),
        }
        if len(rec) > 3 and rec[3]:
            result["stage"] = rec[3]
        metadata = ((self._detail(service) or {}).get("permissions") or {}).get(name)
        if metadata:
            result["metadata"] = metadata

        method_ids = self.methods_by_permission.get(name, [])
        result["requiredBy"] = [
            {k: self.method_by_id[mid][k] for k in ("id", "httpMethod", "path")} for mid in method_ids[:20]
        ]
        if len(method_ids) > 20:
            result["requiredByTotal"] = len(method_ids)
        if not method_ids:
            status = (self.apis.get(service) or {}).get("status", "not_explored")
            result["requiredByNote"] = (
                f"No indexed API method requires this permission. API document status for {service}: "
                f"{status}. {STATUS_MEANING.get(status, '')}"
            )
        result["grantedBy"] = self._granting_roles(name)
        return result

    def get_role(self, name: str, service: str | None = None, limit: int = 300) -> dict:
        self._ensure_loaded()
        role = name if name.startswith("roles/") else f"roles/{name}"
        info = self.roles.get(role)
        if info is None:
            raise NotFound(f"Unknown role '{role}'. Use search(query, kind='roles').")
        permissions = info.get("permissions", [])
        shown = [p for p in permissions if not service or p.split(".")[0] == service]
        limit = _limit(limit, 300, 1000)
        result = {
            "role": role,
            "title": info.get("title", ""),
            "description": info.get("description", ""),
            "stage": info.get("stage", ""),
            "deleted": bool(info.get("deleted")),
            "permissionCount": len(permissions),
            "servicesCovered": dict(Counter(p.split(".")[0] for p in permissions).most_common()),
            "permissions": shown[:limit],
        }
        if role in BASIC_ROLES:
            result["warning"] = "Basic roles are very broad; prefer a predefined or custom role for least privilege."
        if len(shown) > limit:
            result["truncated"] = f"{len(shown)} permissions; pass service=... to narrow the list."
        return result

    def whats_new(
        self,
        days: int = 30,
        tiers: list[int] | None = None,
        service: str | None = None,
        include_removed: bool = False,
        limit: int = 50,
    ) -> dict:
        self._ensure_loaded()
        days = _limit(days, 30, 3650)
        wanted = {0, 1} if not tiers else {int(t) for t in tiers}
        if not wanted <= set(TIER_DESCRIPTIONS):
            raise ValueError("tiers may only contain 0, 1 and 2.")
        since = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
        matching = [
            e for e in self.events
            if e["date"] >= since
            and e["tier"] in wanted
            and (include_removed or e["change"] == "added")
            and (not service or e["service"] == service)
        ]
        limit = _limit(limit, 50, 200)
        return {
            "since": since,
            "tiers": {str(t): TIER_DESCRIPTIONS[t] for t in sorted(wanted)},
            "total": len(matching),
            "events": [self._event(e) for e in matching[:limit]],
            "truncated": len(matching) > limit,
        }
