"""
PBS MCP Server (read-only) fuer den Proxmox Backup Server.
Aufbau wie zammad-mcp/bexio-mcp: roher mcp.server.Server, Tool-Funktionen in TOOL_FUNCS,
Transport ueber MCP_TRANSPORT:
- "stdio" (Standard) - lokal via uvx/Claude Desktop
- "http" - Streamable HTTP fuer Docker/Cloud hinter Reverse-Proxy, erfordert MCP_AUTH_TOKEN

Alle Werte kommen aus Umgebungsvariablen (PBS_URL, PBS_TOKEN_ID, PBS_TOKEN_SECRET,
PBS_VERIFY_SSL, MCP_AUTH_TOKEN, MCP_HOST, MCP_PORT) - keine Secrets im Code.
"""

import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

PBS_URL = os.environ.get("PBS_URL", "").rstrip("/")
PBS_TOKEN_ID = os.environ.get("PBS_TOKEN_ID", "")
PBS_TOKEN_SECRET = os.environ.get("PBS_TOKEN_SECRET", "")


def _verify() -> Any:
    raw = os.environ.get("PBS_VERIFY_SSL", "true")
    if raw.lower() == "true":
        return True
    if raw.lower() == "false":
        return False
    return raw  # Pfad zu CA-Datei


def api_get(path: str, params: dict = None) -> Any:
    if not (PBS_URL and PBS_TOKEN_ID and PBS_TOKEN_SECRET):
        raise RuntimeError("PBS_URL, PBS_TOKEN_ID und PBS_TOKEN_SECRET muessen gesetzt sein (Umgebungsvariablen)")
    clean = {k: v for k, v in (params or {}).items() if v is not None}
    response = httpx.get(
        f"{PBS_URL}/api2/json{path}",
        headers={"Authorization": f"PBSAPIToken={PBS_TOKEN_ID}:{PBS_TOKEN_SECRET}"},
        params=clean,
        verify=_verify(),
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["data"]


def _ts(value) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(int(value), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _q(value: str) -> str:
    return quote(value, safe="")


def _snap(s: dict) -> dict:
    ver = s.get("verification") or {}
    return {
        "typ": s.get("backup-type"),
        "id": s.get("backup-id"),
        "zeit": _ts(s.get("backup-time")),
        "verify": ver.get("state", "nicht verifiziert"),
        "verify_upid": ver.get("upid"),
        "groesse": s.get("size"),
        "protected": s.get("protected"),
        "kommentar": s.get("comment"),
    }


# ---------------------------------------------------------------- Tool-Funktionen

def server_version() -> dict:
    """PBS-Version und Release abrufen (Verbindungstest)."""
    return api_get("/version")


def datastore_list() -> list[dict]:
    """Alle Datastores mit Belegung, GC-Status und geschaetztem Voll-Datum."""
    return api_get("/status/datastore-usage")


def namespace_list(store: str) -> list[dict]:
    """Namespaces eines Datastores auflisten."""
    return api_get(f"/admin/datastore/{_q(store)}/namespace")


def group_list(store: str, ns: str = None) -> list[dict]:
    """Backup-Gruppen eines Datastores bzw. Namespaces."""
    data = api_get(f"/admin/datastore/{_q(store)}/groups", {"ns": ns})
    return [
        {
            "typ": g.get("backup-type"),
            "id": g.get("backup-id"),
            "anzahl": g.get("backup-count"),
            "letztes_backup": _ts(g.get("last-backup")),
            "owner": g.get("owner"),
        }
        for g in data
    ]


def snapshot_list(store: str, ns: str = None, backup_type: str = None, backup_id: str = None, limit: int = 50) -> dict:
    """Snapshots eines Datastores (neueste zuerst) inkl. Verify-Status."""
    data = api_get(
        f"/admin/datastore/{_q(store)}/snapshots",
        {"ns": ns, "backup-type": backup_type, "backup-id": backup_id},
    )
    data.sort(key=lambda s: s.get("backup-time", 0), reverse=True)
    return {"gesamt": len(data), "angezeigt": [_snap(s) for s in data[:limit]]}


def verify_failed_list(store: str, ns: str = None) -> dict:
    """Snapshots mit Verify-Status 'failed', gruppiert nach Gruppe, plus Anzahl nie verifizierter Snapshots."""
    data = api_get(f"/admin/datastore/{_q(store)}/snapshots", {"ns": ns})
    groups: dict[str, dict] = {}
    unverified = 0
    for s in data:
        state = (s.get("verification") or {}).get("state")
        if state is None:
            unverified += 1
        if state != "failed":
            continue
        key = f"{s.get('backup-type')}/{s.get('backup-id')}"
        g = groups.setdefault(key, {"gruppe": key, "anzahl": 0, "zeiten": []})
        g["anzahl"] += 1
        g["zeiten"].append(s.get("backup-time", 0))
    result = []
    for g in groups.values():
        zeiten = g.pop("zeiten")
        g["aeltester"] = _ts(min(zeiten))
        g["neuester"] = _ts(max(zeiten))
        result.append(g)
    return {"snapshots_gesamt": len(data), "nie_verifiziert": unverified, "failed_gruppen": result}


def verify_job_list() -> list[dict]:
    """Verify-Jobs mit Zeitplan, letztem Lauf und naechstem Lauf."""
    return [
        {
            "id": j.get("id"),
            "store": j.get("store"),
            "ns": j.get("ns"),
            "zeitplan": j.get("schedule"),
            "ignore_verified": j.get("ignore-verified"),
            "outdated_after_tage": j.get("outdated-after"),
            "letzter_lauf_status": j.get("last-run-state"),
            "letzter_lauf_ende": _ts(j.get("last-run-endtime")),
            "letzter_lauf_upid": j.get("last-run-upid"),
            "naechster_lauf": _ts(j.get("next-run")),
        }
        for j in api_get("/admin/verify")
    ]


def task_list(
    limit: int = 20,
    errors_only: bool = False,
    running_only: bool = False,
    typefilter: str = None,
    store: str = None,
    since_hours: int = None,
) -> list[dict]:
    """Tasks auflisten (neueste zuerst)."""
    since = None
    if since_hours is not None:
        since = int(datetime.now(timezone.utc).timestamp()) - since_hours * 3600
    data = api_get(
        "/nodes/localhost/tasks",
        {
            "limit": limit,
            "errors": 1 if errors_only else None,
            "running": 1 if running_only else None,
            "typefilter": typefilter,
            "store": store,
            "since": since,
        },
    )
    return [
        {
            "upid": t.get("upid"),
            "typ": t.get("worker_type"),
            "id": t.get("worker_id"),
            "start": _ts(t.get("starttime")),
            "ende": _ts(t.get("endtime")),
            "status": t.get("status", "laeuft"),
            "user": t.get("user"),
        }
        for t in data
    ]


def task_status(upid: str) -> dict:
    """Status eines Tasks anhand der UPID."""
    return api_get(f"/nodes/localhost/tasks/{_q(upid)}/status")


def task_log(upid: str, start: int = 0, limit: int = 200) -> str:
    """Log eines Tasks anhand der UPID."""
    data = api_get(f"/nodes/localhost/tasks/{_q(upid)}/log", {"start": start, "limit": limit})
    return "\n".join(str(line.get("t", "")) for line in data)


TOOL_FUNCS = {
    f.__name__: f
    for f in (
        server_version, datastore_list, namespace_list, group_list, snapshot_list,
        verify_failed_list, verify_job_list, task_list, task_status, task_log,
    )
}

# ---------------------------------------------------------------- MCP-Server

server = Server("pbs-mcp")

_STORE = {"type": "string", "description": "Datastore-Name, z.B. pbs-osisa"}
_NS = {"type": "string", "description": "Namespace (optional), z.B. osisa"}
_UPID = {"type": "string", "description": "UPID des Tasks"}


@server.list_tools()
async def list_tools():
    return [
        Tool(name="server_version", description="PBS-Version und Release abrufen (Verbindungstest).",
             inputSchema={"type": "object", "properties": {}}),
        Tool(name="datastore_list",
             description="Alle Datastores mit Belegung (total/used/avail), GC-Status und geschaetztem Voll-Datum.",
             inputSchema={"type": "object", "properties": {}}),
        Tool(name="namespace_list", description="Namespaces eines Datastores auflisten.",
             inputSchema={"type": "object", "properties": {"store": _STORE}, "required": ["store"]}),
        Tool(name="group_list",
             description="Backup-Gruppen (vm/ct/host + ID) eines Datastores bzw. Namespaces mit Anzahl und letztem Backup.",
             inputSchema={"type": "object", "properties": {"store": _STORE, "ns": _NS}, "required": ["store"]}),
        Tool(name="snapshot_list",
             description="Snapshots eines Datastores (neueste zuerst) inkl. Verify-Status. Filter: ns, backup_type (vm/ct/host), backup_id (z.B. 101).",
             inputSchema={"type": "object", "properties": {
                 "store": _STORE, "ns": _NS,
                 "backup_type": {"type": "string"}, "backup_id": {"type": "string"},
                 "limit": {"type": "integer", "default": 50},
             }, "required": ["store"]}),
        Tool(name="verify_failed_list",
             description="Alle Snapshots mit Verify-Status 'failed', gruppiert nach vm/ID inkl. Zeitraum, plus Anzahl nie verifizierter Snapshots.",
             inputSchema={"type": "object", "properties": {"store": _STORE, "ns": _NS}, "required": ["store"]}),
        Tool(name="verify_job_list",
             description="Konfigurierte Verify-Jobs mit Zeitplan, letztem Lauf (Status, UPID) und naechstem Lauf.",
             inputSchema={"type": "object", "properties": {}}),
        Tool(name="task_list",
             description="Tasks des PBS auflisten (neueste zuerst). typefilter z.B. verificationjob, verify, backup, garbage_collection, prune, sync.",
             inputSchema={"type": "object", "properties": {
                 "limit": {"type": "integer", "default": 20},
                 "errors_only": {"type": "boolean", "default": False},
                 "running_only": {"type": "boolean", "default": False},
                 "typefilter": {"type": "string"}, "store": {"type": "string"},
                 "since_hours": {"type": "integer", "description": "Nur Tasks der letzten N Stunden"},
             }}),
        Tool(name="task_status", description="Status eines Tasks anhand der UPID.",
             inputSchema={"type": "object", "properties": {"upid": _UPID}, "required": ["upid"]}),
        Tool(name="task_log",
             description="Log eines Tasks anhand der UPID (z.B. um zu sehen, welche Chunks beim Verify fehlgeschlagen sind).",
             inputSchema={"type": "object", "properties": {
                 "upid": _UPID,
                 "start": {"type": "integer", "default": 0},
                 "limit": {"type": "integer", "default": 200},
             }, "required": ["upid"]}),
    ]


@server.call_tool()
async def call_tool(name, arguments):
    func = TOOL_FUNCS.get(name)
    if not func:
        return [TextContent(type="text", text=f"Unbekanntes Tool: {name}")]
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, lambda: func(**(arguments or {})))
        text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, indent=2)
        return [TextContent(type="text", text=text)]
    except httpx.HTTPStatusError as e:
        return [TextContent(type="text", text=f"PBS API Fehler {e.response.status_code} @ {e.request.url}: {e.response.text[:500]}")]
    except Exception as e:
        return [TextContent(type="text", text=f"Fehler: {str(e)}")]


# ---------------------------------------------------------------------------
# Cloud/HTTP-Betrieb (identisch zu zammad-mcp): statisches Bearer-Token schuetzt /mcp
# ---------------------------------------------------------------------------

MCP_AUTH_TOKEN = os.environ.get("MCP_AUTH_TOKEN", "")


class _StreamableHTTPASGIApp:
    def __init__(self, session_manager):
        self.session_manager = session_manager

    async def __call__(self, scope, receive, send):
        await self.session_manager.handle_request(scope, receive, send)


class BearerAuthMiddleware:
    """Prueft 'Authorization: Bearer <token>'."""

    def __init__(self, app, token: str):
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        auth_header = headers.get(b"authorization", b"").decode("latin-1")
        if auth_header != f"Bearer {self.token}":
            from starlette.responses import JSONResponse
            await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
            return
        await self.app(scope, receive, send)


async def run_http_server():
    from starlette.applications import Starlette
    from starlette.routing import Route
    from starlette.middleware.cors import CORSMiddleware
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from mcp.server.transport_security import TransportSecuritySettings
    import uvicorn

    host = os.environ.get("MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("MCP_PORT", "8000"))

    session_manager = StreamableHTTPSessionManager(
        app=server,
        json_response=True,
        security_settings=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    app = Starlette(
        routes=[Route("/mcp", endpoint=_StreamableHTTPASGIApp(session_manager))],
        lifespan=lambda app: session_manager.run(),
    )
    cors_app = CORSMiddleware(
        BearerAuthMiddleware(app, MCP_AUTH_TOKEN),
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["mcp-session-id"],
    )
    srv = uvicorn.Server(uvicorn.Config(cors_app, host=host, port=port, log_level="info"))
    print(f"PBS MCP HTTP server running on {host}:{port}", flush=True)
    await srv.serve()


def main():
    transport = os.environ.get("MCP_TRANSPORT", "stdio").lower()
    if transport in ("http", "streamable-http"):
        if not MCP_AUTH_TOKEN:
            raise RuntimeError(
                "MCP_TRANSPORT=http erfordert MCP_AUTH_TOKEN (statisches Bearer-Token) - "
                "aus Sicherheitsgruenden kein Start ohne Token."
            )
        asyncio.run(run_http_server())
    else:
        async def _run_stdio():
            async with stdio_server() as (read_stream, write_stream):
                await server.run(read_stream, write_stream, server.create_initialization_options())
        asyncio.run(_run_stdio())


if __name__ == "__main__":
    main()
