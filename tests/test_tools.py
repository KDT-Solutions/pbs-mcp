"""Schreibende Tools (confirm/Vorschau, MCP_READONLY), Fehlermeldungen ohne interne Details, Token-Laenge."""

import asyncio
import os
import sys

import httpx
import pytest
import respx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pbs_mcp as pm  # noqa: E402

PBS = "https://pbs.example.com:8007"


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setattr(pm, "PBS_URL", PBS)
    monkeypatch.setattr(pm, "PBS_TOKEN_ID", "mcp@pbs!claude")
    monkeypatch.setattr(pm, "PBS_TOKEN_SECRET", "super-geheimes-token-secret")
    monkeypatch.delenv("MCP_READONLY", raising=False)
    monkeypatch.delenv("MCP_ALLOW_DELETE", raising=False)


@pytest.fixture
def pbs():
    with respx.mock(base_url=PBS, assert_all_mocked=True) as mock:
        yield mock


def call(name, **args):
    return asyncio.run(pm.call_tool(name, args))[0].text


# ---------------------------------------------------------------- confirm / Vorschau

def test_verify_start_preview_without_confirm(pbs):
    r = pm.verify_start("store1", ns="kunde", backup_type="vm", backup_id="101")
    assert r["vorschau"] is True and r["wuerde_starten"] == "Verify"
    assert r["umfang"]["filter"] == {"backup_type": "vm", "backup_id": "101"} and r["umfang"]["ns"] == "kunde"
    assert pbs.calls.call_count == 0


def test_verify_start_whole_store_preview_says_so(pbs):
    r = pm.verify_start("store1")
    assert r["umfang"]["filter"] == "ganzer Datastore"
    assert pbs.calls.call_count == 0


def test_verify_start_with_confirm_posts(pbs):
    route = pbs.post("/api2/json/admin/datastore/store1/verify").mock(return_value=httpx.Response(200, json={"data": "UPID:1"}))
    r = pm.verify_start("store1", ns="kunde", backup_type="vm", backup_id="101", backup_time="2026-01-31T19:00:00Z",
                        ignore_verified=True, confirm=True)
    assert r == {"gestartet": True, "upid": "UPID:1"}
    body = route.calls.last.request.content.decode()
    assert "ns=kunde" in body and "backup-type=vm" in body and "ignore-verified=1" in body
    assert "backup-time=1769886000" in body
    assert route.calls.last.request.headers["Authorization"] == "PBSAPIToken=mcp@pbs!claude:super-geheimes-token-secret"


def test_verify_job_run_and_gc_start_need_confirm(pbs):
    assert pm.verify_job_run("job-a")["vorschau"] is True
    assert pm.gc_start("store1")["vorschau"] is True
    assert pbs.calls.call_count == 0
    pbs.post("/api2/json/admin/verify/job-a/run").mock(return_value=httpx.Response(200, json={"data": "UPID:2"}))
    pbs.post("/api2/json/admin/datastore/store1/gc").mock(return_value=httpx.Response(200, json={"data": "UPID:3"}))
    assert pm.verify_job_run("job-a", confirm=True)["upid"] == "UPID:2"
    assert pm.gc_start("store1", confirm=True)["upid"] == "UPID:3"


@pytest.mark.parametrize("fn, args", [
    (pm.verify_start, {"store": "store1"}),
    (pm.verify_job_run, {"job_id": "j"}),
    (pm.gc_start, {"store": "store1"}),
    (pm.snapshot_forget, {"store": "s", "backup_type": "vm", "backup_id": "1", "backup_time": "1"}),
])
def test_readonly_blocks_even_preview(monkeypatch, pbs, fn, args):
    monkeypatch.setenv("MCP_READONLY", "true")
    with pytest.raises(RuntimeError, match="MCP_READONLY"):
        fn(**args)
    with pytest.raises(RuntimeError, match="MCP_READONLY"):
        fn(**args, confirm=True)
    assert pbs.calls.call_count == 0


def test_tool_schemas_have_confirm():
    tools = {t.name: t for t in asyncio.run(pm.list_tools())}
    for name in ("verify_start", "verify_job_run", "gc_start", "snapshot_forget"):
        assert tools[name].inputSchema["properties"]["confirm"]["type"] == "boolean"
        assert "confirm" not in tools[name].inputSchema.get("required", [])


# ---------------------------------------------------------------- Fehlermeldungen

def test_http_error_hides_url_and_body(pbs, caplog):
    pbs.get("/api2/json/admin/datastore/store1/namespace").mock(
        return_value=httpx.Response(403, text='{"errors":{"detail":"permission check failed on /datastore/store1"}}'))
    with caplog.at_level("WARNING", logger="pbs_mcp"):
        text = call("namespace_list", store="store1")
    assert text == "PBS API Fehler 403 bei namespace_list: keine Berechtigung (Rechte von Token UND User pruefen). Details im Server-Log."
    assert "pbs.example.com" not in text and "permission check" not in text
    assert "pbs.example.com" in caplog.text and "permission check" in caplog.text  # Details im Log


def test_http_error_unknown_code_uses_reason(pbs):
    pbs.get("/api2/json/version").mock(return_value=httpx.Response(418, text="teapot body"))
    text = call("server_version")
    assert text.startswith("PBS API Fehler 418 bei server_version:") and "teapot body" not in text


def test_connection_error_only_type(pbs):
    pbs.get("/api2/json/version").mock(side_effect=httpx.ConnectError("[Errno -2] Name or service not known"))
    text = call("server_version")
    assert text == "PBS nicht erreichbar bei server_version (ConnectError)"


def test_own_runtime_errors_are_passed_through(monkeypatch, pbs):
    monkeypatch.setenv("MCP_READONLY", "true")
    assert "MCP_READONLY" in call("gc_start", store="s", confirm=True)
    assert call("nicht_da") == "Unbekanntes Tool: nicht_da"
    assert pbs.calls.call_count == 0


# ---------------------------------------------------------------- Token

@pytest.mark.parametrize("token", ["", "kurz", "a" * 31])
def test_main_refuses_short_token(monkeypatch, token):
    monkeypatch.setattr(pm, "MCP_AUTH_TOKEN", token)
    with pytest.raises(RuntimeError, match="mind. 32 Zeichen"):
        pm.main()


def test_main_accepts_32_char_token(monkeypatch):
    monkeypatch.setattr(pm, "MCP_AUTH_TOKEN", "a" * 32)
    started = []
    monkeypatch.setattr(pm.asyncio, "run", lambda coro: (started.append(coro), coro.close()))
    pm.main()
    assert started


def test_empty_token_never_matches():
    from starlette.testclient import TestClient
    with TestClient(pm.build_http_app(""), base_url="https://mcp.example.com") as c:
        assert c.get("/mcp", headers={"Authorization": "Bearer "}).status_code == 401
        assert c.get("/mcp", headers={"Authorization": "Bearer"}).status_code == 401
