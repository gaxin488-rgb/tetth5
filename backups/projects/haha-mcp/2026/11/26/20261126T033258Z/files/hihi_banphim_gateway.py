"""Combine the existing hihi Windows MCP server with BanPhim MCP over HTTP.

The tunnel keeps using the existing hihi tunnel ID and exposes both backends
through one MCP stdio connection. Existing hihi tool names are preserved;
BanPhim tools are prefixed with ``banphim_``.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
from contextlib import AsyncExitStack, asynccontextmanager
from io import TextIOWrapper

import anyio
import jsonschema
from datetime import datetime
from pathlib import Path
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamablehttp_client
from mcp.server import InitializationOptions
from mcp.server.lowlevel import NotificationOptions, Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, ListToolsRequest, TextContent, Tool


ORCHESTRATOR_DIR = Path(r"D:\MCP\orchestrator")
if str(ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR_DIR))
from task_mcp_tools import ORCHESTRATOR_TOOLS, dispatch_orchestrator_tool
ORCHESTRATOR_NAMES = frozenset(t.name for t in ORCHESTRATOR_TOOLS)
from recommended_actions import (
    RECOMMENDED_NAMES,
    build_tools as build_recommended_tools,
    dispatch as dispatch_recommended_tool,
)
from collections import deque, Counter
import time

HIHI_PYTHON = Path(r"C:\Python314\python.exe")
HIHI_SERVER = Path(
    r"C:\Users\Admin\AppData\Local\hihi-windows-fs\hihi_windows_fs_mcp.py"
)
BANPHIM_MCP_URL = "http://127.0.0.1:9890/mcp"
BANPHIM_PREFIX = "banphim_"
CODEX_SERVER_NAME = "haha"
GATEWAY_SCRIPT = Path(__file__).resolve()

CODEX_TOOLS: list[Tool] = [
    Tool(
        name="codex_mcp_connect",
        description=(
            "Register this local haha gateway in the current Windows user's Codex MCP config. "
            "Requires confirm='CONNECT haha'. This writes a backup before adding the server. "
            "It does not force an already-open Codex chat to reload its MCP catalog."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "confirm": {
                    "type": "string",
                    "enum": ["CONNECT haha"],
                    "description": "Must be exactly CONNECT haha to update Codex MCP configuration.",
                }
            },
            "required": ["confirm"],
            "additionalProperties": False,
        },
    ),
    Tool(
        name="codex_mcp_registry_refresh",
        description=(
            "Ask the MCP client connected to this gateway to rediscover its current tools using "
            "notifications/tools/list_changed. This only notifies an existing connection; it "
            "cannot add a server that is missing from Codex MCP configuration, and the client "
            "does not acknowledge whether it applied the refreshed catalog."
        ),
        inputSchema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    ),
]


def _find_codex_cli() -> Path:
    command = shutil.which("codex.exe") or shutil.which("codex")
    if command and Path(command).is_file() and Path(command).suffix.lower() == ".exe":
        return Path(command).resolve()

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates = list(
            (Path(local_app_data) / "OpenAI" / "Codex" / "bin").glob("*/codex.exe")
        )
        candidates = [candidate for candidate in candidates if candidate.is_file()]
        if candidates:
            return max(candidates, key=lambda candidate: candidate.stat().st_mtime).resolve()

    raise FileNotFoundError("Codex CLI was not found in PATH or the standard Windows install directory")


def _run_codex(cli: Path, arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(cli), *arguments],
        cwd=str(GATEWAY_SCRIPT.parent),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _codex_servers(cli: Path) -> list[dict[str, Any]]:
    result = _run_codex(cli, ["mcp", "list", "--json"])
    if result.returncode != 0:
        raise RuntimeError(f"codex mcp list failed with exit code {result.returncode}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Codex returned an unreadable MCP server list") from exc
    if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
        raise RuntimeError("Codex returned an unexpected MCP server list")
    return payload


def _normal_path(value: Any) -> str:
    return os.path.normcase(os.path.normpath(str(value))) if value else ""


def _is_haha_gateway(server: dict[str, Any]) -> bool:
    transport = server.get("transport")
    if not isinstance(transport, dict) or transport.get("type") != "stdio":
        return False
    command = _normal_path(transport.get("command"))
    args = transport.get("args")
    if not isinstance(args, list):
        return False
    return command == _normal_path(sys.executable) and _normal_path(GATEWAY_SCRIPT) in {
        _normal_path(argument) for argument in args
    }


def _backup_codex_config() -> str | None:
    codex_home = Path(os.path.expandvars(os.path.expanduser(os.environ.get("CODEX_HOME", ""))))
    if not os.environ.get("CODEX_HOME"):
        codex_home = Path.home() / ".codex"
    config_path = codex_home / "config.toml"
    if not config_path.is_file():
        return None

    backup_dir = codex_home / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = backup_dir / f"config.toml.{stamp}.bak"
    suffix = 1
    while backup_path.exists():
        backup_path = backup_dir / f"config.toml.{stamp}-{suffix}.bak"
        suffix += 1
    shutil.copy2(config_path, backup_path)
    return str(backup_path)


def _cleanup_codex_backups(keep_path: str | None) -> list[str]:
    if not keep_path:
        return []
    keep = Path(keep_path).resolve()
    backup_dir = keep.parent.resolve()
    removed: list[str] = []
    for candidate in backup_dir.glob("config.toml.*.bak"):
        if candidate.is_symlink() or candidate.resolve().parent != backup_dir or candidate.resolve() == keep:
            continue
        candidate.unlink()
        removed.append(candidate.name)
    return removed


def _connect_haha_to_codex(confirm: str) -> dict[str, Any]:
    if confirm != "CONNECT haha":
        raise PermissionError("Pass confirm='CONNECT haha' to change the Codex MCP configuration")

    cli = _find_codex_cli()
    servers = _codex_servers(cli)
    existing = next(
        (server for server in servers if server.get("name") == CODEX_SERVER_NAME),
        None,
    )
    if existing is not None:
        if _is_haha_gateway(existing):
            enabled = bool(existing.get("enabled", True))
            if not enabled:
                return {
                    "status": "configured_but_disabled",
                    "server": CODEX_SERVER_NAME,
                    "enabled": False,
                    "config_registered": True,
                    "live_chat_refreshed": False,
                }
            tool_count = asyncio.run(_probe_haha_gateway())
            return {
                "status": "already_configured",
                "server": CODEX_SERVER_NAME,
                "enabled": True,
                "config_registered": True,
                "connection_verified": True,
                "tool_count": tool_count,
                "live_chat_refreshed": False,
            }
        raise FileExistsError(
            "A different MCP server already uses the name 'haha'; its configuration was left unchanged"
        )

    backup_path = _backup_codex_config()
    result = _run_codex(
        cli,
        [
            "mcp",
            "add",
            CODEX_SERVER_NAME,
            "--",
            str(Path(sys.executable).resolve()),
            str(GATEWAY_SCRIPT),
        ],
    )
    if result.returncode != 0:
        raise RuntimeError(f"codex mcp add failed with exit code {result.returncode}; backup retained")

    updated = next(
        (server for server in _codex_servers(cli) if server.get("name") == CODEX_SERVER_NAME),
        None,
    )
    if updated is None or not _is_haha_gateway(updated):
        raise RuntimeError("Codex did not report the haha gateway after registration; backup retained")

    tool_count = asyncio.run(_probe_haha_gateway())
    removed_backups = _cleanup_codex_backups(backup_path)

    return {
        "status": "configured",
        "server": CODEX_SERVER_NAME,
        "config_registered": True,
        "connection_verified": True,
        "tool_count": tool_count,
        "backup": backup_path,
        "older_backups_removed": removed_backups,
        "live_chat_refreshed": False,
        "next_step": "Start a new Codex chat or call codex_mcp_registry_refresh from an existing haha connection.",
    }


DIAGNOSTIC_TOOLS = [
    Tool(name="mcp_local_server_restart",description="Replace this gateway's owned local hihi stdio child after a successful independent initialization and health probe. Requires confirm=RESTART hihi. Does not restart tunnel, gateway or Codex.",inputSchema={"type":"object","properties":{"confirm":{"type":"string","enum":["RESTART hihi"]}},"required":["confirm"],"additionalProperties":False}),
    Tool(name="mcp_tools_audit", description="Compare actual routed gateway tool names with optional client catalog; report duplicates and missing names.", inputSchema={"type":"object","properties":{"client_tools":{"type":"array","items":{"type":"string"},"maxItems":1000}},"additionalProperties":False}),
    Tool(name="mcp_connection_diagnose", description="Probe connected gateway downstream MCP sessions without exposing credentials or restarting anything.", inputSchema={"type":"object","properties":{},"additionalProperties":False}),
    Tool(name="mcp_error_history", description="Read bounded sanitized error history persisted across gateway restarts; no arguments or raw error content logged.", inputSchema={"type":"object","properties":{"limit":{"type":"integer","minimum":1,"maximum":200}},"additionalProperties":False}),
    Tool(name="mcp_request_trace", description="Read bounded request metadata persisted across gateway restarts; arguments and results are not recorded.", inputSchema={"type":"object","properties":{"limit":{"type":"integer","minimum":1,"maximum":200}},"additionalProperties":False}),
    Tool(name="mcp_self_test", description="Run bounded read-only end-to-end probes through gateway routing; optional existing task ID.", inputSchema={"type":"object","properties":{"task_id":{"type":"string","pattern":"^task_[0-9a-f]{12}$"}},"additionalProperties":False}),
    Tool(name="mcp_tool_registry_sync", description="Audit current gateway catalog and optionally notify the connected client. Does not reload client registry or restart servers.", inputSchema={"type":"object","properties":{"notify":{"type":"boolean"},"client_tools":{"type":"array","items":{"type":"string"},"maxItems":1000}},"additionalProperties":False}),
]
DIAGNOSTIC_NAMES = frozenset(t.name for t in DIAGNOSTIC_TOOLS)

async def all_tools(session: ClientSession) -> list[Tool]:
    tools: list[Tool] = []
    cursor: str | None = None
    while True:
        page = await session.list_tools(cursor=cursor)
        tools.extend(page.tools)
        cursor = page.nextCursor
        if not cursor:
            return tools


async def _probe_haha_gateway() -> int:
    async with AsyncExitStack() as stack:
        streams = await stack.enter_async_context(
            stdio_client(
                StdioServerParameters(
                    command=str(Path(sys.executable).resolve()),
                    args=[str(GATEWAY_SCRIPT)],
                    cwd=str(GATEWAY_SCRIPT.parent),
                    env=dict(os.environ),
                )
            )
        )
        session = await stack.enter_async_context(ClientSession(*streams))
        await session.initialize()
        tools = await all_tools(session)
        if not tools:
            raise RuntimeError("haha gateway initialized but returned no tools")
        return len(tools)


class HihiChild:
    """Own one SDK session in its own coroutine; close scopes in the same task."""
    def __init__(self):
        self.stop=asyncio.Event()
        self.ready=asyncio.get_running_loop().create_future()
        self.startup_phase = "spawn"
        self.task=asyncio.create_task(self.run())
    async def run(self):
        try:
            async with stdio_client(StdioServerParameters(command=str(HIHI_PYTHON),args=[str(HIHI_SERVER)],cwd=str(HIHI_SERVER.parent),env=dict(os.environ))) as streams:
                async with ClientSession(*streams) as session:
                    self.startup_phase = "initialize"
                    init=await session.initialize()
                    self.startup_phase = "tools/list"
                    tools=await all_tools(session)
                    names=[t.name for t in tools]
                    if not names or len(names)!=len(set(names)):
                        raise RuntimeError("Invalid hihi tool catalog")
                    self.startup_phase = "health_check"
                    health=await session.call_tool("mcp_health_check",{})
                    if health.isError:raise RuntimeError("hihi health probe failed")
                    self.startup_phase = "ready"
                    if not self.ready.done():
                        self.ready.set_result((session,init.serverInfo.version,len(names)))
                    await self.stop.wait()
        except Exception as exc:
            if not self.ready.done():self.ready.set_exception(exc)
            else:print("hihi child ended: "+type(exc).__name__,file=sys.stderr)
    async def start(self):
        try:
            return await asyncio.wait_for(asyncio.shield(self.ready),15)
        except asyncio.TimeoutError as exc:
            phase = self.startup_phase
            self.ready.cancel()
            self.task.cancel()
            raise TimeoutError(f"hihi startup timed out after 15s (phase={phase})") from exc
        except asyncio.CancelledError:
            self.ready.cancel()
            self.task.cancel()
            raise
    async def close(self):
        self.stop.set()
        if not self.ready.done():
            self.ready.cancel()
            self.task.cancel()
        try:await asyncio.wait_for(asyncio.shield(self.task),10)
        except asyncio.CancelledError:
            if not self.task.cancelled():
                raise
        except asyncio.TimeoutError:
            self.task.cancel()
            try:await self.task
            except asyncio.CancelledError:pass

_MODERN_PROTOCOL = "2026-07-28"
_PROTOCOL_KEY = "io.modelcontextprotocol/protocolVersion"


class _StdioWriter:
    def __init__(self, stream):
        self.stream = stream
        self.lock = asyncio.Lock()

    async def write(self, line):
        async with self.lock:
            await self.stream.write(line)
            await self.stream.flush()

    async def flush(self):
        # Complete newline-delimited messages are flushed inside the write lock.
        return None


@asynccontextmanager
async def _compatible_stdio(handle_modern):
    """Keep the SDK legacy session and dispatch modern requests before its decoder."""
    reader = anyio.wrap_file(TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace"))
    writer = _StdioWriter(anyio.wrap_file(TextIOWrapper(sys.stdout.buffer, encoding="utf-8")))
    modern_tasks = {}

    async def answer(request):
        try:
            response = await handle_modern(request)
            await writer.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
        finally:
            modern_tasks.pop(request["id"], None)

    async with asyncio.TaskGroup() as group:
        async def legacy_input():
            async for line in reader:
                try:
                    request = json.loads(line)
                except ValueError:
                    yield line
                    continue
                if not isinstance(request, dict):
                    yield line
                    continue
                method = request.get("method")
                params = request.get("params")
                meta = params.get("_meta") if isinstance(params, dict) else None
                modern = method == "server/discover" or isinstance(meta, dict) and _PROTOCOL_KEY in meta
                if method == "notifications/cancelled" and isinstance(params, dict):
                    request_id = params.get("requestId")
                    job = modern_tasks.get(request_id) if type(request_id) in (str, int) else None
                    if job is not None:
                        job.cancel()
                        continue
                if not modern:
                    yield line
                    continue
                request_id = request.get("id")
                if type(request_id) not in (str, int):
                    if "id" in request:
                        response = {"jsonrpc": "2.0", "id": None,
                                    "error": {"code": -32600, "message": "Invalid request ID"}}
                        await writer.write(json.dumps(response) + "\n")
                    continue
                if request_id in modern_tasks:
                    response = {"jsonrpc": "2.0", "id": request_id,
                                "error": {"code": -32600, "message": "Request ID is already in flight"}}
                    await writer.write(json.dumps(response) + "\n")
                    continue
                modern_tasks[request_id] = group.create_task(answer(request))

        async with stdio_server(stdin=legacy_input(), stdout=writer) as streams:
            yield streams


async def main() -> None:
    if not HIHI_PYTHON.is_file():
        raise FileNotFoundError(f"hihi Python was not found: {HIHI_PYTHON}")
    if not HIHI_SERVER.is_file():
        raise FileNotFoundError(f"hihi MCP server was not found: {HIHI_SERVER}")

    async with AsyncExitStack() as stack:
        hihi_owner=HihiChild()
        stack.push_async_callback(hihi_owner.close)
        hihi,_,_=await hihi_owner.start()
        hihi_lock=asyncio.Lock()
        async def hihi_tools():
            async with hihi_lock:return await all_tools(hihi)
        async def hihi_call(name,arguments):
            async with hihi_lock:return await hihi.call_tool(name,arguments)

        banphim: ClientSession | None = None
        try:
            http_streams = await stack.enter_async_context(
                streamablehttp_client(
                    BANPHIM_MCP_URL,
                    timeout=30,
                    sse_read_timeout=None,
                )
            )
            banphim = await stack.enter_async_context(
                ClientSession(http_streams[0], http_streams[1])
            )
            await banphim.initialize()
            print("BanPhim MCP bridge ready at 127.0.0.1:9890", file=sys.stderr)
        except Exception as exc:
            # Keep the established hihi connection usable if BanPhim is offline.
            print(
                f"BanPhim MCP is unavailable; hihi tools remain available ({type(exc).__name__}).",
                file=sys.stderr,
            )

        server = Server(
            "hihi-banphim-gateway",
            version="1.4.8",
            instructions=(
                "This MCP server combines the existing hihi tools with BanPhim. "
                "Existing hihi names are unchanged. BanPhim tools use the "
                "banphim_ prefix. codex_mcp_connect registers this local gateway "
                "in Codex configuration after explicit confirmation. "
                "codex_mcp_registry_refresh sends a tools/list_changed notification "
                "to the client connected to this gateway; it does not add an unconfigured server. "
                "Treat tool failures and ok=false as incomplete; preserve continuation IDs, replay the original safe action after repair, and verify the original objective before completing."
            ),
        )

        recommended_tool_names: set[str] = set()

        @server.list_tools()
        async def list_tools() -> list[Tool]:
            combined = await hihi_tools()
            if banphim is not None:
                for tool in await all_tools(banphim):
                    combined.append(
                        tool.model_copy(update={"name": f"{BANPHIM_PREFIX}{tool.name}"})
                    )
            combined.extend(ORCHESTRATOR_TOOLS)
            combined.extend(CODEX_TOOLS)
            combined.extend(DIAGNOSTIC_TOOLS)
            recommended = build_recommended_tools(combined)
            replacement_names = {tool.name for tool in recommended}
            if replacement_names:
                combined = [tool for tool in combined if tool.name not in replacement_names]
            recommended_tool_names.clear()
            recommended_tool_names.update(tool.name for tool in recommended)
            combined.extend(recommended)
            counts = Counter(t.name for t in combined)
            duplicates = sorted(n for n, c in counts.items() if c > 1)
            if duplicates:
                raise RuntimeError("Duplicate gateway tools: " + ", ".join(duplicates))
            return combined

        errors = deque(maxlen=200)
        request_traces = deque(maxlen=1000)
        from gateway_failure_journal import GatewayJournal
        journal = GatewayJournal(Path(r"D:\MCP\logs\gateway_diagnostics.sqlite3"))

        def record_event(kind, entry):
            (errors if kind == "error" else request_traces).append(entry)
            journal.record(kind, entry)

        async def replay_health(cid):
            state = journal.inspect(cid)
            if not state["ok"]:
                return state
            token = journal.claim(cid)
            if not token:
                state = journal.inspect(cid)
                return {**state, "ok": state["workflow_complete"], "original_action_executed": False}
            call = state["original_call"]
            try:
                result = await asyncio.wait_for(route(call["tool"], call["arguments"]), 10)
                data = result_payload(result)
                passed = not result.isError and journal.passed(call["tool"], call["arguments"], data)
            except Exception as exc:
                passed = False
                data = {"ok": False, "error_type": type(exc).__name__}
            except asyncio.CancelledError:
                journal.finish(cid, False, token)
                raise
            committed = journal.finish(cid, passed, token)
            if not passed:
                record_event("error", {"timestamp": datetime.now().isoformat(), "tool": call["tool"],
                                       "kind": "replay_failure", "continuation_id": cid})
            return {**journal.inspect(cid), "ok": passed and committed, "original_action_executed": True,
                    "original_result": data}

        def diagnostic_result(data, error=False):
            return CallToolResult(content=[TextContent(type="text", text=json.dumps(data, ensure_ascii=False))], structuredContent=data, isError=error)

        async def catalog_audit(arguments):

            active = await asyncio.wait_for(list_tools(), 16)
            names = [tool.name for tool in active]
            hihi_names = [tool.name for tool in await asyncio.wait_for(hihi_tools(), 8)]
            bp_names = [name for name in names if name.startswith(BANPHIM_PREFIX)]
            counts = Counter(names)

            duplicates = sorted(n for n,c in counts.items() if c>1)

            client = arguments.get("client_tools")

            return {"ok":not duplicates,"gateway_tool_count":len(set(names)),"hihi_count":len(hihi_names),"banphim_count":len(bp_names),"orchestrator_count":len(ORCHESTRATOR_NAMES),"duplicates":duplicates,"tool_names":sorted(set(names)),"client_catalog_provided":client is not None,"missing_in_client":sorted(set(names)-set(client)) if client is not None else None,"extra_in_client":sorted(set(client)-set(names)) if client is not None else None}



        async def refresh_registered_catalog():
            # Refresh the same tool cache used by MCP input validation.
            result = await server.request_handlers[ListToolsRequest](ListToolsRequest(method="tools/list"))
            return result.root.tools

        async def replace_child(arguments):

            nonlocal hihi,hihi_owner

            if arguments.get("confirm")!="RESTART hihi":raise PermissionError("Exact RESTART hihi confirmation required")

            async with hihi_lock:

                new=HihiChild()

                try:new_session,version,count=await new.start()

                except Exception:

                    await new.close()

                    raise

                old=hihi_owner

                hihi,hihi_owner=new_session,new

                stack.push_async_callback(new.close)

                await old.close()

                closed=old.task.done()

            catalog = await refresh_registered_catalog()

            notified=False

            try:

                notified = await registry_notification()

            except Exception:pass

            return diagnostic_result({"ok":closed,"component":"hihi stdio child","old_child_closed":closed,"new_child_initialized":True,"new_hihi_version":version,"new_hihi_tool_count":count,"gateway_catalog_rebuilt":True,"gateway_tool_count":len(catalog),"notification_sent":notified,"gateway_restarted":False,"tunnel_restarted":False,"client_catalog_rebuilt":False},not closed)



        def result_payload(result):
            if isinstance(result.structuredContent, dict):
                return result.structuredContent
            if result.content and isinstance(result.content[0], TextContent):
                try:
                    value = json.loads(result.content[0].text)
                    if isinstance(value, dict):
                        return value
                except json.JSONDecodeError:
                    pass
            return {}

        async def compiler_contract():
            from tempfile import TemporaryDirectory

            with TemporaryDirectory(prefix="haha_erl_contract_") as directory:
                source = Path(directory) / "haha_erl_contract.erl"
                source.write_text(
                    "-module(haha_erl_contract).\n-export([value/0]).\nvalue() -> 42.\n",
                    encoding="utf-8",
                )
                args = {"source_path": str(source), "output_dir": str(source.parent / "beam")}
                denied = result_payload(await route("erlang_module_compile", {**args, "confirm": ""}))
                denial_verified = (
                    denied.get("ok") is False
                    and "confirm=COMPILE" in denied.get("error", "")
                    and not Path(args["output_dir"]).exists()
                )
                compiled = result_payload(await route(
                    "erlang_module_compile", {**args, "confirm": "COMPILE " + source.name}
                ))
                beam = compiled.get("details", {}).get("beam", {})
                passed = bool(
                    denial_verified and compiled.get("ok") is True
                    and beam.get("module") == source.stem
                    and {"function": "value", "arity": 0} in beam.get("exports", [])
                    and beam.get("sha256")
                )
                return {
                    "action": "erlang_module_compile", "tested": True, "passed": passed,
                    "invalid_confirmation_rejected": denial_verified,
                    "beam_sha256": beam.get("sha256"), "evidence_path": compiled.get("evidence_path"),
                    "otp_release": compiled.get("details", {}).get("otp_release"),
                    "scope": "local temporary module compilation",
                }

        async def invoke(name, arguments):
            return result_payload(await route(name, arguments))

        async def route(name: str, arguments: dict[str, Any]) -> CallToolResult:
            from recommended_actions import ALIASES, CUSTOM_DESCRIPTIONS
            import gateway_backend_audit as audit

            if name == "mcp_tool_capability_inspect":
                requested = arguments.get("tool_name")
                active = {tool.name: tool for tool in await list_tools()}
                if requested in ALIASES and requested in active and requested not in recommended_tool_names:
                    item = active[requested]
                    return diagnostic_result({
                        "ok": True, "tool_name": requested, "registered_or_declared": True,
                        "target_action": requested, "target_present": True, "classification": "direct_backend",
                        "description": item.description, "input_schema": item.inputSchema,
                        "scope": "active Hihi route in the composite gateway",
                    })

            op = arguments.get("operation")
            cid = arguments.get("continuation_id")
            if name == "mcp_task_auto_resume" and op == "capture_call":
                call = arguments.get("original_call", {})
                if isinstance(cid, str) and cid.startswith("gwcont_"):
                    tid = arguments.get("task_id")
                    if call or not isinstance(tid, str) or not audit.re.fullmatch(r"task_[0-9a-f]{12}", tid):
                        return diagnostic_result({"ok": False, "reason": "EXPLICIT_TASK_BIND_REQUIRED", "workflow_complete": False}, True)
                    from android_job_contracts import normalize
                    task = normalize(await invoke("task_result", {"task_id": tid}))
                    state = journal.inspect(cid)
                    if task.get("task_id") != tid or not state["ok"] or not journal.bind(cid, tid):
                        return diagnostic_result({"ok": False, "reason": "CONTINUATION_BIND_REJECTED", "workflow_complete": False}, True)
                    return diagnostic_result(journal.inspect(cid))
                if isinstance(call, dict) and journal.eligible(call.get("tool"), call.get("arguments")):
                    expected = arguments.get("expected_fields", {})
                    if expected not in ({}, {"ok": True}) or arguments.get("recipe") is not None:
                        return diagnostic_result({"ok": False, "reason": "UNSUPPORTED_GATEWAY_EXPECTATIONS", "workflow_complete": False}, True)
                    scoped = call["arguments"].get("task_id")
                    tid = arguments.get("task_id", scoped)
                    if scoped is not None and tid != scoped:
                        return diagnostic_result({"ok": False, "reason": "TASK_SCOPE_MISMATCH", "workflow_complete": False}, True)
                    if call["tool"] in {"task_status","task_result"} and expected:
                        return diagnostic_result({"ok": False, "reason": "UNSUPPORTED_GATEWAY_EXPECTATIONS", "workflow_complete": False}, True)
                    if tid is not None:
                        from android_job_contracts import normalize
                        task = normalize(await invoke("task_result", {"task_id": tid}))
                        if task.get("task_id") != tid:
                            return diagnostic_result({"ok": False, "reason": "TASK_NOT_FOUND", "workflow_complete": False}, True)
                    cid = journal.capture(call["tool"], call["arguments"], tid)
                    return diagnostic_result(journal.inspect(cid))
            if isinstance(cid, str) and cid.startswith("gwcont_"):
                if name == "mcp_task_auto_resume" and op in {"inspect_call", "replay_call"}:
                    data = journal.inspect(cid) if op == "inspect_call" else await replay_health(cid)
                    return diagnostic_result(data, data.get("ok") is not True)
                if name == "mcp_backend_regression_suite":
                    state = journal.inspect(cid)
                    if not state["ok"] or state["original_call"]["tool"] not in arguments.get("tool_names", []):
                        return diagnostic_result({"ok": False, "reason": "ORIGINAL_ACTION_NOT_IN_REGRESSION", "workflow_complete": False}, True)
                    tests = await invoke("mcp_backend_regression_suite", {"tool_names": arguments["tool_names"]})
                    if tests.get("ok") is not True:
                        return diagnostic_result({**tests, "workflow_complete": False}, True)
                    data = await replay_health(cid)
                    return diagnostic_result({**data, "regression": tests}, data.get("ok") is not True)
            if cid and ((name == "mcp_task_auto_resume" and op == "replay_call")
                        or name == "mcp_backend_regression_suite"):
                checkpoint = audit.read_discovery_checkpoint(cid)
                if checkpoint is not None:
                    followup = None
                    if name == "mcp_backend_regression_suite":
                        if "mcp_action_auto_discovery" not in arguments.get("tool_names", []):
                            return diagnostic_result({"ok": False, "reason": "ORIGINAL_ACTION_NOT_IN_REGRESSION",
                                                      "workflow_complete": False}, True)
                        followup = await invoke("mcp_backend_contract_test", {"tool_names": arguments["tool_names"]})
                        if followup.get("ok") is not True:
                            return diagnostic_result({**followup, "continuation_id": cid,
                                                      "workflow_complete": False}, True)
                    replay = await audit.replay_discovery(cid, invoke)
                    if followup is not None:
                        replay["regression"] = followup
                    return diagnostic_result(replay, replay.get("ok") is not True)

            if name == "mcp_dependency_check":
                from h5_server_actions import probe_erlang_installations
                result = await hihi_call(name, arguments)
                data = result_payload(result)
                if not isinstance(data.get("executables"), dict):
                    return result
                installations = await probe_erlang_installations()
                chosen = installations[0] if installations else None
                data["executables"].update(erl=chosen["erl"] if chosen else None,
                                           erlc=chosen["erlc"] if chosen else None)
                data["erlang"] = {"installations": installations,
                                  "otp_releases": sorted({row["otp_release"] for row in installations}),
                                  "otp19_available": any(row["otp_release"] == "19" for row in installations)}
                return diagnostic_result(data, result.isError)

            if name == "h5_combat_trace_start":
                role_id = arguments.get("role_id")
                if type(role_id) is not int or role_id < 1:
                    return diagnostic_result({"ok": False, "reason": "INVALID_TRACE_ROLE",
                                              "status": "NOT_RECORDING", "listener_attached": False}, True)
                started = await hihi_call(name, arguments)
                data = result_payload(started)
                if data.get("ok") is not True or not data.get("trace_id"):
                    return started
                observation = await invoke("h5_combat_trace_read", {"trace_id": data["trace_id"], "limit": 1})
                listener_fields = ("active_combat_pid", "role_pid_verified", "listener_attached",
                                   "listener_active", "listener_mode", "live_fsm_observed", "fsm_reason")
                data["listener_observation_attempted"] = True
                data.update({key: observation[key] for key in listener_fields if key in observation})
                data["listener_verified"] = (
                    observation.get("role_pid_verified") is True and observation.get("listener_attached") is True
                    and bool(observation.get("active_combat_pid"))
                )
                return diagnostic_result(data, started.isError)

            if name in {"h5_combat_id_collision_scan", "h5_combat_skill_id_collision_scan",
                        "h5_combat_skill_bundle_publish"} and (name != "h5_combat_skill_bundle_publish"
                                                              or op in {None, "preflight"}):
                if name in recommended_tool_names:
                    result = await dispatch_recommended_tool(name, arguments, route, list_tools)
                else:
                    result = await hihi_call(name, arguments)
                data = result_payload(result)
                scan = data.get("collision_evidence") if name.endswith("bundle_publish") else data
                if isinstance(scan, dict) and scan.get("conflicts"):
                    mapping = await invoke("h5_combat_hero_skill_mapping", {})
                    if type(mapping.get("hero_id")) is int:
                        scan["ownership_evidence"] = audit.collision_ownership(scan, mapping)
                    if name.endswith("bundle_publish"):
                        import hashlib

                        manifest = data.get("manifest_path")
                        if isinstance(manifest, str) and (path := Path(manifest).resolve()).is_relative_to(
                                Path(r"D:\h5").resolve()) and path.name == "manifest.json":
                            try:
                                with path.open("rb") as stream:
                                    manifest_bytes = stream.read(65537)
                                if (len(manifest_bytes) <= 65536
                                        and hashlib.sha256(manifest_bytes).hexdigest() == data.get("manifest_sha256")):
                                    payload = json.loads(manifest_bytes)
                                    data["candidate_identity"] = {
                                        "manifest_verified": True,
                                        "hero_id": payload.get("hero_id"),
                                        "bundle_id": payload.get("bundle_id"),
                                        "source_sha256": payload.get("source_sha256"),
                                        "builder_sha256": payload.get("builder_sha256"),
                                        "otp_release": payload.get("otp"),
                                    }
                            except (OSError, ValueError, TypeError):
                                pass
                        data["release_qualification"] = {
                            "preflight_ready": data.get("preflight_ready") is True,
                            "failed_gates": data.get("failed_gates", []),
                            "post_deploy_checks": data.get("post_deploy_checks", {}),
                            "ownership_proven": False,
                            "archive_matches_loaded": scan.get("archive_matches_loaded") is True,
                        }
                    return diagnostic_result(data, result.isError)
                return result

            if name == "mcp_action_coverage_matrix":
                # The composite catalog, not the child registry, is authoritative.
                seed = result_payload(await hihi_call(name, {**arguments, "run_contract_tests": False}))
                rows = seed.get("rows")
                if not isinstance(rows, list) or not rows:
                    return diagnostic_result(seed, True)
                active = {tool.name for tool in await list_tools()}
                tests = {}
                if arguments.get("run_contract_tests"):
                    checked = await invoke("mcp_backend_contract_test", {"tool_names": list(dict.fromkeys(r["action"] for r in rows)), "include_staged": True})
                    tests = {r["action"]: r for r in checked.get("rows", [])}
                for row in rows:
                    action = row["action"]
                    present = action in active
                    row["registered"] = present
                    if present and row.get("backend_kind") == "missing":
                        row["backend_kind"] = "composite"
                    elif not present and not str(row.get("backend_kind", "")).startswith("generated_"):
                        row["backend_kind"] = "missing"
                    test = tests.get(action, {})
                    row["contract_test"] = ("PASS" if test.get("tested") is True and test.get("passed") is True else
                        "FAIL" if test.get("tested") is True else "NOT_TESTED" if arguments.get("run_contract_tests") else "NOT_RUN")
                    row["runtime_verified"] = present and test.get("runtime_verified") is True
                seed.update(registry_scope="composite_gateway",
                    registered_count=sum(r["registered"] for r in rows),
                    tested_pass_count=sum(r["contract_test"] == "PASS" for r in rows),
                    ok=all(r["registered"] and (not arguments.get("run_contract_tests") or r["contract_test"] == "PASS") for r in rows))
                return diagnostic_result(seed, not seed["ok"])

            if name == "mcp_backend_completion_gate":
                from android_job_contracts import normalize
                names = arguments.get("tool_names", [])
                evidence_args = arguments.get("evidence", [])
                tid = arguments.get("task_id")
                if (not isinstance(tid, str) or not audit.re.fullmatch(r"task_[0-9a-f]{12}", tid)
                        or not 1 <= len(names) <= 100 or not 1 <= len(evidence_args) <= 20
                        or any(not isinstance(n, str) or not audit.NAME.fullmatch(n) or n.startswith("mcp_backend_") or n == name or n == "mcp_action_coverage_matrix" or n == "mcp_task_auto_resume" for n in names)):
                    return diagnostic_result({"ok": False, "reason": "INVALID_COMPLETION_GATE_INPUT"}, True)
                pending = journal.pending_for_task(tid)
                if pending:
                    return diagnostic_result({"ok": False, "task_id": tid, "reason": "UNRESOLVED_REQUIRED_CONTINUATIONS",
                                              "pending_continuations": pending, "workflow_complete": False}, True)
                task = normalize(await invoke("task_result", {"task_id": tid}))
                acceptance = normalize(await invoke("task_acceptance_check", {"task_id": tid}))
                stages = task.get("stages", [])
                task_ok = (task.get("task_id") == tid and task.get("status") == "completed"
                    and bool(stages) and all(s.get("status") == "completed" and s.get("data", {}).get("gate", {}).get("passed") is True for s in stages)
                    and isinstance(task.get("result"), dict) and task["result"].get("workflow_complete") is True
                    and acceptance.get("task_id") == tid and acceptance.get("acceptance_verified") is True)
                evidence = [await invoke("mcp_backend_evidence_verify", item) for item in evidence_args]
                evidence_ok = all(item.get("ok") is True and item.get("hash_match") is True for item in evidence)
                tests = await invoke("mcp_backend_regression_suite", {"tool_names": names}) if task_ok and evidence_ok else {"ok": False, "reason": "PREREQUISITES_FAILED"}
                pending = journal.pending_for_task(tid)
                passed = task_ok and evidence_ok and tests.get("ok") is True and not pending
                return diagnostic_result({"ok": passed, "task_id": tid, "task_status": task.get("status"),
                    "task_verified": task_ok, "evidence_verified": evidence_ok, "contracts": tests,
                    "evidence": evidence, "live_semantics_verified": False,
                    "pending_continuations": pending, "workflow_complete": passed,
                    "scope": "composite contracts, recorded task state and file integrity only"}, not passed)

            metadata = {"mcp_backend_gap_scan", "mcp_action_auto_discovery",
                        "mcp_task_requirement_analyze", "mcp_backend_repair_plan"}
            contracts = {"mcp_backend_contract_test", "mcp_backend_regression_suite"}
            checks = {"mcp_registry_consistency_check", "mcp_registry_hot_reload_ack"}
            if name in metadata | contracts | checks:
                active = {tool.name for tool in await list_tools()}
                limited = {tool for tool, description in CUSTOM_DESCRIPTIONS.items()
                           if audit.re.search(r"(unavailable|not exposed|not available)", description,
                                              audit.re.IGNORECASE)}
                if name == "mcp_backend_repair_plan" and op in {"preview_template", "apply_template"}:
                    selected = arguments.get("recipe", {}).get("action_name")
                    child_actions = {tool.name for tool in await hihi_tools()}
                    if selected in active and selected not in child_actions:
                        return diagnostic_result({"ok": False, "reason": "COMPOSITE_ACTION_ALREADY_REGISTERED",
                                                  "action": selected, "workflow_complete": False}, True)
                    return await hihi_call(name, arguments)
                if name == "mcp_backend_regression_suite" and cid:
                    return await hihi_call(name, arguments)
                result = await hihi_call(name, arguments)
                data = result_payload(result)
                if not data:
                    return result
                data["registry_scope"] = "composite_gateway"
                if name in metadata:
                    target = data.get("gaps", {}) if name == "mcp_backend_repair_plan" else data
                    names = set(target.get("missing", [])) | {
                        row["action"] for row in target.get("rows", []) if not row.get("registered")
                    }
                    names.update(action for action, row in target.get("availability", {}).items()
                                 if row.get("kind") != "direct")
                    verified = audit.verified_actions(active, names, ALIASES, limited)
                    audit.update_discovery(target, verified)
                    if name == "mcp_action_auto_discovery":
                        prefix = arguments.get("prefix", "")
                        matching = sorted(tool for tool in active if tool.startswith(prefix))
                        data.update(active_count=len(active), matching_names=matching[:250],
                                    possibly_truncated=len(matching) > 250)
                    if name == "mcp_backend_repair_plan" and verified:
                        audit.update_discovery(data, verified)
                        for step in data.get("steps", []):
                            if step.get("action") in verified:
                                step.update(next="use canonical action " + verified[step["action"]]["target"], canonical_action=verified[step["action"]]["target"], source_review_required=False)
                    return diagnostic_result(data, result.isError)
                if name == "mcp_registry_consistency_check" and isinstance(data.get("rows"), list):
                    for row in data["rows"]:
                        row["hihi_backend"] = row.get("backend")
                        row["backend"] = row["action"] in active
                    data["backend_complete"] = all(row["backend"] for row in data["rows"])
                    external_fields = tuple(field for field in ("gateway", "client")
                                            if field + "_names" in arguments)
                    data["external_catalogs_match"] = (
                        all(row.get(field) is True for row in data["rows"] for field in external_fields)
                        if external_fields else None
                    )
                    data["ok"] = data["backend_complete"] and data["external_catalogs_match"] is not False
                elif name == "mcp_registry_hot_reload_ack" and isinstance(data.get("expected"), list):
                    data["missing"] = [action for action in data["expected"] if action not in active]
                    data.update(server_catalog_count=len(active), server_catalog_contains_all=not data["missing"],
                                ok=not data["missing"])
                elif name in contracts and isinstance(data.get("rows"), list):
                    from android_job_contracts import Contracts, NAMES as ANDROID_JOB_CONTRACTS
                    android_contracts = Contracts(invoke)
                    for index, row in enumerate(data["rows"]):
                        action = row["action"]
                        if action in active:
                            if action in ANDROID_JOB_CONTRACTS:
                                row = await android_contracts.run(action)
                            elif action == "erlang_module_compile":
                                row = await compiler_contract()
                            elif action in {"code_apply_patch", "code_patch_validate"}:
                                row = await audit.python_patch_contract(action, invoke)
                            elif action == "h5_combat_skill_dependency_validate":
                                row = audit.dependency_contract(action, await invoke(action, {}))
                            elif action in {"fs_run_script_file", "mcp_initialize", "mcp_request_trace"}:
                                row = await audit.gateway_diagnostic_contract(action, invoke)
                            elif action == "code_read_range":
                                row = await audit.read_range_contract(invoke)
                            elif action == "mcp_dependency_check":
                                row = audit.runtime_dependency_contract(await invoke(action, {}))
                            elif action == "h5_combat_trace_start":
                                row = await audit.trace_start_contract(invoke)
                            elif action in {"h5_combat_skill_bundle_publish", "h5_combat_skill_trigger_assert"}:
                                row = audit.combat_guard_contract(row)
                        row["catalog_present"] = action in active
                        if row["catalog_present"] and not row.get("tested"):
                            row["reason"] = "REGISTERED_WITHOUT_BOUNDED_CONTRACT"
                        data["rows"][index] = audit.contract_status(row)
                    data["tests_run"] = sum(row.get("tested") is True for row in data["rows"])
                    data["passed"] = sum(row.get("passed") is True for row in data["rows"])
                    data["ok"] = bool(data["rows"]) and data["passed"] == len(data["rows"])
                    data["contract_passes"] = sum(row.get("passed") is True for row in data["rows"])
                    data["business_verified_actions"] = [row["action"] for row in data["rows"] if row.get("business_verified") is True]
                else:
                    return result
                return diagnostic_result(data, data.get("ok") is False)

            if name in recommended_tool_names:

                return await dispatch_recommended_tool(name, arguments, route, list_tools)
            if name=="mcp_local_server_restart":return await replace_child(arguments)
            if name in DIAGNOSTIC_NAMES:
                if name == "mcp_error_history":
                    n=max(1,min(200,int(arguments.get("limit",50))))
                    count, events = journal.history("error", n)
                    return diagnostic_result({"ok":True,"scope":"persistent gateway journal","error_count":count,"events":events})
                if name == "mcp_request_trace":
                    n=max(1,min(200,int(arguments.get("limit",50))))
                    count, events = journal.history("trace", n)
                    return diagnostic_result({"ok":True,"scope":"persistent gateway journal","event_count":count,"events":events,"arguments_recorded":False,"results_recorded":False})
                if name == "mcp_connection_diagnose":
                    probes={}
                    for label, downstream in (("hihi",hihi),("banphim",banphim)):
                        if downstream is None:
                            probes[label]={"reachable":False,"reason":"not connected at startup"}
                            continue
                        started=time.monotonic()
                        try:
                            ts=await asyncio.wait_for(hihi_tools() if label=="hihi" else all_tools(downstream),8)
                            probes[label]={"reachable":True,"tool_count":len(ts),"elapsed_ms":round((time.monotonic()-started)*1000)}
                        except Exception as exc:
                            probes[label]={"reachable":False,"error_type":type(exc).__name__}
                    return diagnostic_result({"ok":all(v["reachable"] for v in probes.values()),"gateway_version":"1.4.8","gateway_pid":os.getpid(),"probes":probes,"restart_performed":False,"client_reload_verified":False})
                if name in ("mcp_tools_audit","mcp_tool_registry_sync"):
                    data=await catalog_audit(arguments)
                    if name=="mcp_tool_registry_sync":
                        data["notification_sent"]=False
                        if arguments.get("notify",False) and data["ok"]:
                            data["notification_sent"] = await registry_notification()
                            data["notification_requested"]=True
                        data["client_catalog_rebuilt"]=False
                    return diagnostic_result(data,not data["ok"])
                if name == "mcp_self_test":
                    probes=[("mcp_health_check",{}),("task_list",{"limit":1}),("process_list",{"limit":1}),("android_sdk_status",{}),("livesub_session_status",{})]
                    tid=arguments.get("task_id")
                    if tid:
                        probes += [(x, {"task_id":tid}) for x in ("task_status","task_progress","task_metrics","task_events","task_artifacts","task_acceptance_check","task_lock_manage","task_live_metrics")]
                        probes.append(("task_find",{"query":tid}))
                    items=[]
                    for tool,args in probes:
                        started=time.monotonic()
                        try:
                            v=await asyncio.wait_for(route(tool,args),5)
                            success=not v.isError
                            sc=result_payload(v)
                            if isinstance(sc,dict) and sc.get("ok") is False:success=False
                            blocked = (tool == "livesub_session_status" and args == {} and isinstance(sc, dict) and sc.get("ok") is False and sc.get("exists") is True and sc.get("reason") == "SESSION_STATE_NOT_FOUND" and "session_id" in sc and sc["session_id"] is None)
                            item = {"name":tool,"passed":success,"status":"BLOCKED" if blocked else ("PASS" if success else "FAIL"),"elapsed_ms":round((time.monotonic()-started)*1000)}
                            if isinstance(sc, dict) and isinstance(sc.get("reason"), str) and not success:
                                item["reason_code"] = sc["reason"] if sc["reason"] in {"SESSION_STATE_NOT_FOUND", "SESSION_ID_MISMATCH", "INVALID_SESSION_STATE"} else "BACKEND_REPORTED_FAILURE"
                            items.append(item)
                        except Exception as exc:
                            items.append({"name":tool,"passed":False,"status":"FAIL","error_type":type(exc).__name__})
                    failed = sum(x["status"] == "FAIL" for x in items)
                    blocked = sum(x["status"] == "BLOCKED" for x in items)
                    return diagnostic_result({"ok":failed == 0,"backend_probes_ok":failed == 0,"all_probes_verified":failed == 0 and blocked == 0,"verification_status":"FAIL" if failed else ("UNVERIFIED" if blocked else "PASS"),"failed":failed,"blocked":blocked,"tested":len(items),"passed":sum(x["passed"] for x in items),"checks":items,"scope":"read-only selected probes, not full functional test"})

            if name == "codex_mcp_connect":
                try:
                    result = await asyncio.to_thread(
                        _connect_haha_to_codex,
                        arguments.get("confirm", ""),
                    )
                    return CallToolResult(
                        content=[TextContent(type="text", text=json.dumps(result, indent=2))],
                        isError=False,
                    )
                except Exception as exc:
                    return CallToolResult(
                        content=[
                            TextContent(
                                type="text",
                                text=f"Codex MCP connection failed: {type(exc).__name__}: {exc}",
                            )
                        ],
                        isError=True,
                    )
            if name == "codex_mcp_registry_refresh":
                try:
                    current_tools = await refresh_registered_catalog()
                    notification_sent = await registry_notification()
                    result = {
                        "notification_sent": "notifications/tools/list_changed" if notification_sent else False,
                        "server_tool_count": len(current_tools),
                        "client_catalog_rebuilt": False,
                        "scope": "active gateway catalog and the connected legacy session, when present",
                    }
                    return CallToolResult(
                        content=[TextContent(type="text", text=json.dumps(result, indent=2))],
                        isError=False,
                    )
                except Exception as exc:
                    return CallToolResult(
                        content=[
                            TextContent(
                                type="text",
                                text=f"Tool registry refresh request failed: {type(exc).__name__}: {exc}",
                            )
                        ],
                        isError=True,
                    )
            if name in ORCHESTRATOR_NAMES:
                return await dispatch_orchestrator_tool(name, arguments)
            if name.startswith(BANPHIM_PREFIX):
                if banphim is None:
                    return CallToolResult(
                        content=[
                            TextContent(
                                type="text",
                                text="BanPhim MCP is offline. Start D:\\banphim\\mcp_server\\start_background.ps1 and refresh plugin tools.",
                            )
                        ],
                        isError=True,
                    )
                return await banphim.call_tool(name[len(BANPHIM_PREFIX) :], arguments)
            return await hihi_call(name, arguments)

        @server.call_tool()
        async def call_tool(name: str, arguments: dict[str, Any]) -> CallToolResult:
            from gateway_backend_audit import error_code

            started = time.monotonic()
            outcome = "ok"
            try:
                result = await route(name, arguments)
                data = result_payload(result)
                failed_result = bool(result.isError or data.get("ok") is False or
                                     (journal.eligible(name, arguments) and not journal.passed(name, arguments, data)))
                if failed_result:
                    outcome = "tool_error" if result.isError else "logical_failure"
                    entry = {"timestamp": datetime.now().isoformat(), "tool": name,
                             "kind": outcome, "elapsed_ms": round((time.monotonic() - started) * 1000)}
                    code = error_code(data)
                    if name == "fs_run_script_file":
                        script_codes = {"SCRIPT_CONFIRMATION_REQUIRED", "SCRIPT_RUNTIME_MISSING",
                                        "SCRIPT_EXIT_NONZERO", "SCRIPT_TIMEOUT", "SCRIPT_LAUNCH_FAILED"}
                        reason = data.get("reason")
                        if isinstance(reason, str) and reason in script_codes:
                            code = reason
                            stage = data.get("failure_stage")
                            if stage in ("validation", "runtime_selection", "script", "launch"):
                                entry["failure_stage"] = stage
                            if type(data.get("return_code")) is int:
                                entry["return_code"] = data["return_code"]
                            if type(data.get("script_started")) is bool:
                                entry["script_started"] = data["script_started"]
                    if code is not None:
                        entry["reason_code"] = code
                    record_event("error", entry)
                if failed_result and journal.eligible(name, arguments):
                    cid = journal.capture(name, arguments)
                    failed = {**data, "ok": False, "mcp_recovery": {
                        **journal.inspect(cid), "failure_kind": "DIAGNOSE_FAILURE",
                        "workflow_complete": False}}
                    return diagnostic_result(failed, True)
                structured = result.structuredContent
                if structured is None and result.isError:
                    err_msg = (
                        result.content[0].text
                        if result.content and isinstance(result.content[0], TextContent)
                        else "Tool returned an error"
                    )
                    structured = data if data else {"ok": False, "error": err_msg}
                return CallToolResult(
                    content=result.content,
                    structuredContent=structured,
                    isError=failed_result,
                )
            except Exception as exc:
                outcome = type(exc).__name__
                record_event("error", {"timestamp": datetime.now().isoformat(), "tool": name,
                               "kind": outcome, "elapsed_ms": round((time.monotonic() - started) * 1000)})
                if journal.eligible(name, arguments):
                    cid = journal.capture(name, arguments)
                    transient = isinstance(exc, (TimeoutError, ConnectionError, anyio.EndOfStream, anyio.ClosedResourceError, anyio.BrokenResourceError))
                    if transient:
                        for attempt in range(3):
                            recovered = await replay_health(cid)
                            if recovered.get("ok") is True and recovered.get("original_action_executed") is True:
                                outcome = "recovered"
                                return diagnostic_result(recovered["original_result"])
                            # Do not auto-retry a logical failure or a non-transient exception.
                            if recovered.get("original_result", {}).get("error_type") not in {
                                "TimeoutError", "ConnectionError", "EndOfStream", "ClosedResourceError", "BrokenResourceError"}:
                                break
                            if attempt < 2:
                                await asyncio.sleep(0.1 * (attempt+1))
                    return diagnostic_result({"ok": False, "error_type": type(exc).__name__,
                        "mcp_recovery": {**journal.inspect(cid), "failure_kind": "CONNECTION_FAILURE" if transient else "DIAGNOSE_FAILURE"}}, True)
                err_payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                err_text = json.dumps(err_payload, ensure_ascii=False)
                return CallToolResult(
                    content=[TextContent(type="text", text=err_text)],
                    structuredContent=err_payload,
                    isError=True,
                )
            finally:
                record_event("trace", {"timestamp": datetime.now().isoformat(), "request_id": str(time.time_ns()),
                    "tool": name, "outcome": outcome, "duration_ms": round((time.monotonic() - started) * 1000)})

        async def registry_notification():
            try:
                session = server.request_context.session
            except LookupError:
                # Modern tools calls have per-request metadata, no legacy session.
                return False
            await session.send_tool_list_changed()
            return True

        async def handle_modern(request):
            envelope = {"jsonrpc": "2.0", "id": request["id"]}

            def error(code, message, data=None):
                details = {"code": code, "message": message}
                if data is not None:
                    details["data"] = data
                return {**envelope, "error": details}

            def complete(result):
                return {**envelope, "result": {**result, "resultType": "complete"}}

            try:
                method = request.get("method")
                params = request.get("params", {})
                if request.get("jsonrpc") != "2.0" or not isinstance(method, str):
                    return error(-32600, "Invalid JSON-RPC request")
                if not isinstance(params, dict):
                    return error(-32602, "Request params must be an object")
                if method == "server/discover":
                    return complete({
                        "supportedVersions": [_MODERN_PROTOCOL],
                        "capabilities": {"tools": {}},
                        "_meta": {"io.modelcontextprotocol/serverInfo": {
                            "name": "hihi-banphim-gateway", "version": "1.4.8"}},
                        "instructions": server.instructions,
                    })
                meta = params.get("_meta", {})
                requested = meta.get(_PROTOCOL_KEY) if isinstance(meta, dict) else None
                if requested != _MODERN_PROTOCOL:
                    return error(-32022, "Unsupported protocol version",
                                 {"supported": [_MODERN_PROTOCOL], "requested": requested})
                if method == "ping":
                    return complete({})
                if method == "tools/list":
                    if params.get("cursor"):
                        return error(-32602, "Invalid tool cursor")
                    active = await refresh_registered_catalog()
                    return complete({"tools": [tool.model_dump(mode="json", by_alias=True, exclude_none=True)
                                               for tool in active]})
                if method != "tools/call":
                    return error(-32601, "Method not found")
                name, arguments = params.get("name"), params.get("arguments", {})
                if not isinstance(name, str) or not isinstance(arguments, dict):
                    return error(-32602, "Expected a tool name and argument object")
                active = await refresh_registered_catalog()
                tool = next((entry for entry in active if entry.name == name), None)
                if tool is None:
                    return error(-32602, "Unknown tool")
                try:
                    jsonschema.validate(arguments, tool.inputSchema)
                except jsonschema.ValidationError as exc:
                    return complete({"isError": True, "content": [
                        {"type": "text", "text": "Input validation error: " + exc.message}]})
                result = await call_tool(name, arguments)
                if tool.outputSchema is not None and not result.isError:
                    jsonschema.validate(result.structuredContent, tool.outputSchema)
                return complete(result.model_dump(mode="json", by_alias=True, exclude_none=True))
            except Exception as exc:
                print("MCP modern request failed: " + type(exc).__name__, file=sys.stderr)
                return error(-32603, "Gateway failed to process the MCP request")

        read_stream, write_stream = await stack.enter_async_context(_compatible_stdio(handle_modern))
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="hihi-banphim-gateway",
                server_version="1.4.8",
                capabilities=server.get_capabilities(
                    NotificationOptions(tools_changed=True),
                    {},
                ),
                instructions=server.instructions,
            ),
        )


if __name__ == "__main__":
    asyncio.run(main())
