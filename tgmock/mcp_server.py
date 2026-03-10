"""
tgmock MCP server — lets Claude Code control a Telegram bot test session interactively.

Install:
    claude mcp add tgmock --transport stdio -- python -m tgmock.mcp

Tools:
    tg_start    Start mock server + bot subprocess
    tg_send     Send a message as a test user
    tg_tap      Click an inline keyboard button
    tg_snapshot Get current conversation snapshot
    tg_events   Get custom events posted by the bot
    tg_reset    Reset user state
    tg_users    List active test users
    tg_stop     Stop the server and bot

Requires: pip install tgmock[mcp]
"""
from __future__ import annotations

try:
    import mcp.server.stdio
    import mcp.types as types
    from mcp.server import Server
    _MCP_AVAILABLE = True
except ImportError:
    _MCP_AVAILABLE = False

import asyncio
import json
import os
import subprocess
import sys
from typing import Any

# ── Global session state ──────────────────────────────────────────────────────

_server_runner = None
_mock: Any = None          # TelegramMockServer instance
_bot_proc: subprocess.Popen | None = None
_client_session = None     # aiohttp.ClientSession
_base_url: str = "http://localhost:8999"
_default_user_id: int = 111
_default_timeout: float = 25.0


def _snapshot_text(messages: list[dict]) -> str:
    """Convert bot response messages into a readable snapshot string."""
    if not messages:
        return "(no response)"
    parts = []
    for i, msg in enumerate(messages):
        if i > 0:
            parts.append("---")
        text = msg.get("text", "")
        if text:
            parts.append(f"[Bot] {text}")
        kb = msg.get("reply_markup")
        if kb and "inline_keyboard" in kb:
            buttons = [btn["text"] for row in kb["inline_keyboard"] for btn in row]
            if buttons:
                parts.append(f"[Buttons: {' | '.join(buttons)}]")
    return "\n".join(parts)


async def _get_session():
    """Lazily create a shared aiohttp session."""
    global _client_session
    if _client_session is None or _client_session.closed:
        import aiohttp
        _client_session = aiohttp.ClientSession()
    return _client_session


async def _wait_ready(proc: subprocess.Popen, ready_log: str, timeout: float) -> None:
    loop = asyncio.get_event_loop()
    async def _read():
        while True:
            line = await loop.run_in_executor(None, proc.stdout.readline)
            if not line:
                raise RuntimeError("Bot exited before ready")
            sys.stderr.write(f"[BOT] {line}")
            if ready_log.lower() in line.lower():
                return
    await asyncio.wait_for(_read(), timeout=timeout)


# ── MCP tool implementations ──────────────────────────────────────────────────

async def _tg_start(bot_command: str, port: int = 8999, ready_log: str = "bot starting",
                    env: dict | None = None, startup_timeout: float = 15.0) -> dict:
    global _mock, _bot_proc, _server_runner, _base_url

    from tgmock.server import TelegramMockServer
    _base_url = f"http://localhost:{port}"

    # Start mock server
    _mock = TelegramMockServer(token="test:token", port=port)
    _server_runner = await _mock.start()

    # Start bot subprocess
    bot_env = {**os.environ, "BOT_API_BASE": _base_url, "BOT_TOKEN": "test:token"}
    if env:
        bot_env.update(env)
    cmd = bot_command.split()
    _bot_proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1, env=bot_env,
    )

    t0 = asyncio.get_event_loop().time()
    await _wait_ready(_bot_proc, ready_log, startup_timeout)
    elapsed = asyncio.get_event_loop().time() - t0

    return {"ok": True, "port": port, "pid": _bot_proc.pid, "message": f"Bot ready after {elapsed:.1f}s"}


async def _tg_send(text: str, user_id: int = 111, timeout: float = 25.0) -> dict:
    session = await _get_session()
    async with session.delete(f"{_base_url}/test/responses", params={"user_id": user_id}) as r:
        pass
    async with session.post(f"{_base_url}/test/send", json={"text": text, "user_id": user_id}) as r:
        data = await r.json()
    after_seq = data.get("after_seq", 0)
    async with session.get(f"{_base_url}/test/wait-response",
                           params={"user_id": user_id, "after_seq": after_seq, "timeout": timeout}) as r:
        result = await r.json()
    if not result.get("ok"):
        return {"ok": False, "reason": result.get("reason", "timeout"), "snapshot": "(timeout)"}
    async with session.get(f"{_base_url}/test/responses", params={"user_id": user_id}) as r:
        messages = await r.json()
    return {"ok": True, "snapshot": _snapshot_text(messages), "messages": messages}


async def _tg_tap(label: str, user_id: int = 111, timeout: float = 25.0) -> dict:
    session = await _get_session()
    # Get current responses to find the keyboard
    async with session.get(f"{_base_url}/test/responses", params={"user_id": user_id}) as r:
        messages = await r.json()

    # Find button by label
    callback_data = None
    message_id = 1
    for msg in reversed(messages):
        kb = msg.get("reply_markup")
        if kb and "inline_keyboard" in kb:
            for row in kb["inline_keyboard"]:
                for btn in row:
                    if label.lower() in btn["text"].lower():
                        callback_data = btn["callback_data"]
                        message_id = msg.get("message_id", 1)
                        break
                if callback_data:
                    break
        if callback_data:
            break

    if callback_data is None:
        all_buttons = [btn["text"] for msg in messages
                       for row in (msg.get("reply_markup") or {}).get("inline_keyboard", [])
                       for btn in row]
        return {"ok": False, "error": f"Button {label!r} not found. Available: {all_buttons}"}

    async with session.delete(f"{_base_url}/test/responses", params={"user_id": user_id}) as r:
        pass
    async with session.post(f"{_base_url}/test/callback",
                            json={"data": callback_data, "user_id": user_id, "message_id": message_id}) as r:
        resp = await r.json()
    after_seq = resp.get("after_seq", 0)
    async with session.get(f"{_base_url}/test/wait-response",
                           params={"user_id": user_id, "after_seq": after_seq, "timeout": timeout}) as r:
        result = await r.json()
    if not result.get("ok"):
        return {"ok": False, "reason": "timeout", "snapshot": "(timeout)"}
    async with session.get(f"{_base_url}/test/responses", params={"user_id": user_id}) as r:
        new_messages = await r.json()
    return {"ok": True, "snapshot": _snapshot_text(new_messages), "messages": new_messages}


async def _tg_snapshot(user_id: int = 111) -> dict:
    session = await _get_session()
    async with session.get(f"{_base_url}/test/responses", params={"user_id": user_id}) as r:
        messages = await r.json()
    return {"ok": True, "snapshot": _snapshot_text(messages), "messages": messages}


async def _tg_events(user_id: int = 111, type: str | None = None) -> dict:
    session = await _get_session()
    params: dict = {"user_id": user_id}
    if type:
        params["type"] = type
    async with session.get(f"{_base_url}/test/events", params=params) as r:
        events = await r.json()
    return {"ok": True, "events": events, "count": len(events)}


async def _tg_reset(user_id: int = 111) -> dict:
    session = await _get_session()
    async with session.post(f"{_base_url}/test/reset-user", params={"user_id": user_id}) as r:
        result = await r.json()
    return result


async def _tg_users() -> dict:
    session = await _get_session()
    async with session.get(f"{_base_url}/test/users") as r:
        users = await r.json()
    return {"ok": True, "users": users}


async def _tg_stop(timeout: float = 5.0) -> dict:
    global _mock, _bot_proc, _server_runner, _client_session

    if _bot_proc:
        _bot_proc.terminate()
        try:
            loop = asyncio.get_event_loop()
            await asyncio.wait_for(
                loop.run_in_executor(None, _bot_proc.wait), timeout=timeout
            )
        except asyncio.TimeoutError:
            _bot_proc.kill()
        _bot_proc = None

    if _server_runner:
        await _server_runner.cleanup()
        _server_runner = None
        _mock = None

    if _client_session and not _client_session.closed:
        await _client_session.close()
        _client_session = None

    return {"ok": True, "message": "Server and bot stopped"}


# ── MCP server entry point ────────────────────────────────────────────────────

def create_server() -> "Server":
    if not _MCP_AVAILABLE:
        raise ImportError("MCP SDK not installed. Run: pip install tgmock[mcp]")

    app = Server("tgmock")

    @app.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(name="tg_start", description="Start fake Telegram API server and bot subprocess",
                parameters={"type": "object", "properties": {
                    "bot_command": {"type": "string", "description": "Command to start the bot (e.g. 'python main.py')"},
                    "port": {"type": "integer", "default": 8999},
                    "ready_log": {"type": "string", "default": "bot starting", "description": "Substring in bot stdout that signals readiness"},
                    "env": {"type": "object", "description": "Extra env vars for the bot subprocess"},
                    "startup_timeout": {"type": "number", "default": 15.0},
                }, "required": ["bot_command"]}),
            types.Tool(name="tg_send", description="Send a text message as a test user, wait for bot response",
                parameters={"type": "object", "properties": {
                    "text": {"type": "string"},
                    "user_id": {"type": "integer", "default": 111},
                    "timeout": {"type": "number", "default": 25.0},
                }, "required": ["text"]}),
            types.Tool(name="tg_tap", description="Click an inline keyboard button by label (partial match)",
                parameters={"type": "object", "properties": {
                    "label": {"type": "string", "description": "Button label (partial, case-insensitive match)"},
                    "user_id": {"type": "integer", "default": 111},
                    "timeout": {"type": "number", "default": 25.0},
                }, "required": ["label"]}),
            types.Tool(name="tg_snapshot", description="Get current conversation state without sending anything",
                parameters={"type": "object", "properties": {
                    "user_id": {"type": "integer", "default": 111},
                }}),
            types.Tool(name="tg_events", description="Get custom events posted by the bot (e.g. tool calls)",
                parameters={"type": "object", "properties": {
                    "user_id": {"type": "integer", "default": 111},
                    "type": {"type": "string", "description": "Filter by event type (e.g. 'tool_call')"},
                }}),
            types.Tool(name="tg_reset", description="Reset user state: clear responses, events, trigger bot reset hook",
                parameters={"type": "object", "properties": {
                    "user_id": {"type": "integer", "default": 111},
                }}),
            types.Tool(name="tg_users", description="List active test users and their last message",
                parameters={"type": "object", "properties": {}}),
            types.Tool(name="tg_stop", description="Stop the mock server and bot subprocess",
                parameters={"type": "object", "properties": {
                    "timeout": {"type": "number", "default": 5.0},
                }}),
        ]

    @app.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
        if name == "tg_start":
            result = await _tg_start(**arguments)
        elif name == "tg_send":
            result = await _tg_send(**arguments)
        elif name == "tg_tap":
            result = await _tg_tap(**arguments)
        elif name == "tg_snapshot":
            result = await _tg_snapshot(**arguments)
        elif name == "tg_events":
            result = await _tg_events(**arguments)
        elif name == "tg_reset":
            result = await _tg_reset(**arguments)
        elif name == "tg_users":
            result = await _tg_users()
        elif name == "tg_stop":
            result = await _tg_stop(**arguments)
        else:
            result = {"error": f"Unknown tool: {name}"}
        return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]

    return app


async def main():
    if not _MCP_AVAILABLE:
        print("ERROR: MCP SDK not installed. Run: pip install tgmock[mcp]", file=sys.stderr)
        sys.exit(1)
    app = create_server()
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
