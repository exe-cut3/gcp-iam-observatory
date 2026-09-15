"""API explorer data built from Google's Discovery documents.

A Discovery document is Google's own machine-readable API description, the
equivalent of an OpenAPI spec: methods, HTTP paths, parameters and schemas. It
says nothing about IAM, so each method is joined to the permission it needs, from
iam-dataset's method map where that covers the API and otherwise by matching the
method name against the permission catalog. The map lags new services, which are
exactly the ones worth exploring, hence the fallback.

Only anonymous requests are made. When Google declines to serve a document to an
anonymous caller, that refusal is recorded as the finding, not worked around.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

DIRECTORY_URL = "https://discovery.googleapis.com/discovery/v1/apis"
PROBE_VERSIONS = ("v1", "v1beta", "v1alpha", "v1beta1", "v1alpha1", "v2")
CACHE_TTL_SECONDS = 24 * 3600
MAX_SCHEMAS = 400
MAX_TEXT = 1200

LISTED = "listed"
UNLISTED = "unlisted"
RESTRICTED = "restricted"
NOT_FOUND = "not_found"
ERROR = "error"

SERVICE_NAME = re.compile(r"^[a-z][a-z0-9-]*$")

# Parent collections that appear in method ids but not in permission names:
# aiplatform.projects.locations.endpoints.get is authorised by aiplatform.endpoints.get.
CONTAINER_COLLECTIONS = frozenset(
    {"projects", "locations", "organizations", "folders", "billingAccounts", "global", "regions", "zones"}
)


def _get(url: str, timeout: int = 30) -> tuple[int | None, bytes]:
    request = urllib.request.Request(url, headers={"User-Agent": "gcp-iam-observatory"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return None, str(exc).encode()


def _fresh(path: Path) -> bool:
    return path.exists() and time.time() - path.stat().st_mtime < CACHE_TTL_SECONDS


def _clip(text: str | None) -> str:
    text = (text or "").strip()
    return text if len(text) <= MAX_TEXT else text[: MAX_TEXT - 1] + "…"


def _error_message(body: bytes) -> str:
    try:
        return json.loads(body)["error"]["message"]
    except (ValueError, KeyError, TypeError):
        return body.decode("utf-8", "replace")[:200]


def _reason_kind(reason: str | None) -> str:
    text = (reason or "").lower()
    identity_markers = ("unregistered callers", "api key", "authentication credential")
    return "identity" if any(marker in text for marker in identity_markers) else "other"


def load_directory(cache_dir: Path, refresh: bool = False) -> dict[str, list[dict]]:
    path = cache_dir / "_directory.json"
    if not refresh and _fresh(path):
        return json.loads(path.read_text(encoding="utf-8"))
    code, body = _get(DIRECTORY_URL)
    if code != 200:
        fallback = "using cached copy" if path.exists() else "probing hosts only"
        print(f"  ! API directory unavailable ({code}); {fallback}")
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    by_name: dict[str, list[dict]] = {}
    for item in json.loads(body).get("items", []):
        by_name.setdefault(item["name"], []).append({
            key: item.get(key)
            for key in ("version", "preferred", "discoveryRestUrl", "title", "description", "documentationLink")
        })
    path.write_text(json.dumps(by_name), encoding="utf-8")
    return by_name


def load_method_map(map_path: Path | None) -> tuple[dict[str, list[str]], dict[str, str]]:
    """Method id -> permissions, and permission prefix -> API name.

    Permission prefixes and API names differ for some services (resourcemanager
    permissions guard the cloudresourcemanager API). The map is the one place that
    records the pairing, so aliases are derived from it rather than kept by hand.
    """
    if not map_path or not Path(map_path).exists():
        return {}, {}
    apis = json.loads(Path(map_path).read_text(encoding="utf-8")).get("api", {})
    method_permissions: dict[str, list[str]] = {}
    votes: dict[str, Counter] = {}
    for api_name, entry in apis.items():
        for method_id, info in ((entry or {}).get("methods") or {}).items():
            names = [p["name"] for p in (info or {}).get("permissions", []) if p.get("name")]
            if not names:
                continue
            method_permissions[method_id] = names
            for name in names:
                votes.setdefault(name.split(".")[0], Counter())[api_name] += 1
    aliases = {prefix: counter.most_common(1)[0][0] for prefix, counter in votes.items()}
    return method_permissions, aliases


def _preferred(entries: list[dict]) -> dict:
    return (
        next((e for e in entries if e.get("preferred")), None)
        or next((e for e in entries if e.get("version") == "v1"), None)
        or entries[0]
    )


def _document(status: str, api: str, version: str, body: bytes, tried: list, listing: dict) -> dict:
    try:
        doc = json.loads(body)
    except ValueError:
        return {"status": ERROR, "api": api, "reason": "unparseable discovery document", "tried": tried}
    return {
        "status": status,
        "api": api,
        "version": version,
        "title": listing.get("title") or doc.get("title"),
        "description": listing.get("description") or doc.get("description"),
        "documentationLink": listing.get("documentationLink") or doc.get("documentationLink"),
        "tried": tried,
        "doc": doc,
    }


def _resolve_uncached(service: str, directory: dict, aliases: dict) -> dict:
    api = service if service in directory else aliases.get(service)
    tried: list[dict] = []
    if api in directory:
        preferred = _preferred(directory[api])
        # The directory sometimes advertises a document that 404s (dataproc v2,
        # integrations v1), so try its other versions before giving up on it.
        for entry in [preferred, *(e for e in directory[api] if e is not preferred)]:
            code, body = _get(entry["discoveryRestUrl"])
            tried.append({"version": entry["version"], "code": code})
            if code == 200:
                return _document(LISTED, api, entry["version"], body, tried, entry)
            if code in (401, 403):
                return {"status": RESTRICTED, "api": api, "reason": _error_message(body), "tried": tried}
            if code != 404:
                return {"status": ERROR, "api": api, "reason": _error_message(body), "tried": tried}

    listing = _preferred(directory[service]) if service in directory else {}
    for version in PROBE_VERSIONS:
        code, body = _get(f"https://{service}.googleapis.com/$discovery/rest?version={version}")
        tried.append({"version": version, "code": code})
        if code == 200:
            return _document(LISTED if listing else UNLISTED, service, version, body, tried, listing)
        if code in (401, 403):
            # An authentication or permission refusal comes from the service's own
            # front end, so the host is real, and it is the same for every version.
            return {"status": RESTRICTED, "api": service, "reason": _error_message(body), "tried": tried}
        if code != 404:
            return {"status": ERROR, "api": service, "reason": _error_message(body), "tried": tried}
    return {"status": NOT_FOUND, "api": service, "tried": tried}


def resolve(service: str, directory: dict, aliases: dict, cache_dir: Path, refresh: bool = False) -> dict:
    path = cache_dir / f"{service}.json"
    if not refresh and _fresh(path):
        return json.loads(path.read_text(encoding="utf-8"))
    result = _resolve_uncached(service, directory, aliases)
    # A timeout or 5xx says nothing about the API. Caching it would report a
    # transient failure as the service's state for a whole day.
    if result["status"] != ERROR:
        path.write_text(json.dumps(result), encoding="utf-8")
    return result


def infer_permissions(method_id: str, service: str, catalog: set[str]) -> list[str]:
    """Guess the permission for a method the upstream map does not cover.

    Only returns a name that exists in the catalog, so a wrong guess can at worst
    point at a real permission on a neighbouring resource, never an invented one.
    """
    parts = method_id.split(".")
    if len(parts) < 3:
        return []
    collections, verb = parts[1:-1], parts[-1]
    verbs = [verb, "update"] if verb == "patch" else [verb]
    stripped = [c for c in collections if c not in CONTAINER_COLLECTIONS]
    for shape in (collections, stripped, collections[-2:], collections[-1:]):
        if not shape:
            continue
        for candidate_verb in verbs:
            candidate = ".".join([service, *shape, candidate_verb])
            if candidate in catalog:
                return [candidate]
    return []


def _walk_methods(resource: dict):
    for method in (resource.get("methods") or {}).values():
        yield method
    for child in (resource.get("resources") or {}).values():
        yield from _walk_methods(child)


def _refs(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                yield value
            else:
                yield from _refs(value)
    elif isinstance(node, list):
        for value in node:
            yield from _refs(value)


def _compact_property(spec: dict) -> dict:
    out = {key: spec[key] for key in ("type", "format", "$ref", "readOnly", "enum") if key in spec}
    if spec.get("description"):
        out["description"] = _clip(spec["description"])
    if isinstance(spec.get("items"), dict):
        out["items"] = {key: spec["items"][key] for key in ("type", "$ref", "format") if key in spec["items"]}
    return out


def _compact_schema(schema: dict) -> dict:
    out = {"type": schema.get("type", "object")}
    if schema.get("description"):
        out["description"] = _clip(schema["description"])
    properties = schema.get("properties") or {}
    if properties:
        out["properties"] = {name: _compact_property(spec) for name, spec in properties.items()}
    return out


def _schema_closure(schemas: dict, roots: set[str]) -> dict:
    """Only the schemas reachable from this API's methods, not the whole document."""
    kept: dict[str, dict] = {}
    pending = sorted(roots)
    while pending and len(kept) < MAX_SCHEMAS:
        name = pending.pop()
        if name in kept or name not in schemas:
            continue
        kept[name] = _compact_schema(schemas[name])
        pending.extend(ref for ref in _refs(schemas[name]) if ref not in kept)
    return kept


def build_detail(service: str, resolved: dict, catalog: set[str], method_permissions: dict) -> dict:
    status = resolved["status"]
    reason = resolved.get("reason")
    doc = resolved.get("doc") or {}
    discovery = {
        "status": status,
        "api": resolved.get("api"),
        "version": resolved.get("version"),
        "title": resolved.get("title"),
        "description": _clip(resolved.get("description")),
        "documentationLink": resolved.get("documentationLink"),
        "rootUrl": doc.get("rootUrl"),
        "servicePath": doc.get("servicePath", ""),
        "tried": resolved.get("tried", []),
        "reason": reason,
        "reasonKind": _reason_kind(reason) if status == RESTRICTED else None,
    }

    methods, roots = [], set()
    for method in _walk_methods(doc):
        method_id = method.get("id", "")
        permissions = method_permissions.get(method_id) or []
        source = "mapped" if permissions else None
        if not permissions:
            permissions = infer_permissions(method_id, service, catalog)
            source = "inferred" if permissions else None

        if permissions:
            resource = permissions[0].rsplit(".", 1)[0]
        else:
            inner = [c for c in method_id.split(".")[1:-1] if c not in CONTAINER_COLLECTIONS]
            resource = ".".join([service, *inner[-1:]])

        request = (method.get("request") or {}).get("$ref")
        response = (method.get("response") or {}).get("$ref")
        roots.update(ref for ref in (request, response) if ref)

        methods.append({
            "id": method_id,
            "httpMethod": method.get("httpMethod", "GET"),
            "path": method.get("flatPath") or method.get("path", ""),
            "description": _clip(method.get("description")),
            "parameters": [
                {
                    "name": name,
                    "location": spec.get("location"),
                    "required": bool(spec.get("required")),
                    "type": spec.get("type", "string"),
                    "enum": spec.get("enum"),
                    "description": _clip(spec.get("description")),
                }
                for name, spec in (method.get("parameters") or {}).items()
                if spec.get("location") in ("path", "query")
            ],
            "request": request,
            "response": response,
            "permissions": permissions,
            "permissionSource": source,
            "resource": resource,
        })

    methods.sort(key=lambda m: (m["resource"], m["path"], m["httpMethod"]))
    summary = {
        "status": status,
        "api": discovery["api"],
        "version": discovery["version"],
        "title": discovery["title"],
        "methods": len(methods),
        "mapped": sum(1 for m in methods if m["permissionSource"] == "mapped"),
        "inferred": sum(1 for m in methods if m["permissionSource"] == "inferred"),
        "reasonKind": discovery["reasonKind"],
    }
    return {
        "summary": summary,
        "discovery": discovery,
        "methods": methods,
        "schemas": _schema_closure(doc.get("schemas") or {}, roots),
    }


def explore(
    services: list[str],
    cache_dir: Path,
    catalog: set[str],
    map_path: Path | None = None,
    refresh: bool = False,
    workers: int = 12,
) -> dict[str, dict]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    directory = load_directory(cache_dir, refresh)
    method_permissions, aliases = load_method_map(map_path)
    targets = [s for s in services if SERVICE_NAME.match(s)]

    results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(resolve, s, directory, aliases, cache_dir, refresh): s for s in targets}
        for future in as_completed(futures):
            service = futures[future]
            results[service] = build_detail(service, future.result(), catalog, method_permissions)
    return results
