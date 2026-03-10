"""
Self-tests for tgmock — runs against a tiny in-process echo bot.

The echo bot:
- Replies "echo: <text>" to any text message with two inline buttons
- Replies "you pressed: <data>" to any callback query

Run: cd /Users/amady/Code/tgmock && .venv/bin/python3 -m pytest tests/ -v
"""
from __future__ import annotations

import asyncio
import json

import pytest
import pytest_asyncio

from tgmock.server import TelegramMockServer
from tgmock.client import BotTestClient, BotResponse
from tgmock._config import TgmockConfig, load_config
from tgmock._user_id import next_user_id


# ── Minimal in-process echo bot ───────────────────────────────────────────────

async def _run_echo_bot(mock: TelegramMockServer, stop_event: asyncio.Event):
    """Polls the mock server and echoes messages back with inline buttons."""
    import aiohttp
    base = f"http://localhost:{mock.port}"
    token = mock.token
    offset = 0
    async with aiohttp.ClientSession() as session:
        while not stop_event.is_set():
            try:
                async with session.post(
                    f"{base}/bot{token}/getUpdates",
                    data={"offset": offset, "timeout": 1},
                ) as r:
                    data = await r.json()
                for update in data.get("result", []):
                    offset = update["update_id"] + 1
                    if "message" in update:
                        msg = update["message"]
                        chat_id = msg["chat"]["id"]
                        text = msg.get("text", "")
                        await session.post(f"{base}/bot{token}/sendMessage", data={
                            "chat_id": chat_id,
                            "text": f"echo: {text}",
                            "reply_markup": json.dumps({
                                "inline_keyboard": [[
                                    {"text": "Button A", "callback_data": "btn_a"},
                                    {"text": "Button B", "callback_data": "btn_b"},
                                ]]
                            }),
                        })
                    elif "callback_query" in update:
                        cq = update["callback_query"]
                        chat_id = cq["message"]["chat"]["id"]
                        await session.post(f"{base}/bot{token}/sendMessage", data={
                            "chat_id": chat_id,
                            "text": f"you pressed: {cq['data']}",
                        })
                        await session.post(f"{base}/bot{token}/answerCallbackQuery",
                                           data={"callback_query_id": cq["id"]})
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(0.05)


# ── Fixtures (all session-scoped so they share one event loop) ────────────────

@pytest_asyncio.fixture(scope="session")
async def mock_server():
    """Start TelegramMockServer + in-process echo bot, shared for entire session."""
    mock = TelegramMockServer(token="test:token", port=18999)
    runner = await mock.start()
    stop = asyncio.Event()
    bot_task = asyncio.create_task(_run_echo_bot(mock, stop))
    # Give the echo bot a moment to start its poll loop
    await asyncio.sleep(0.1)
    yield mock
    stop.set()
    bot_task.cancel()
    await asyncio.gather(bot_task, return_exceptions=True)
    await runner.cleanup()


@pytest_asyncio.fixture
async def client(mock_server: TelegramMockServer):
    """Fresh BotTestClient with unique user_id per test."""
    uid = next_user_id()
    c = BotTestClient("http://localhost:18999", user_id=uid, default_timeout=5.0)
    await c.start()
    await c.clear()
    yield c
    await c.clear()
    await c.stop()


# ── Tests: imports ────────────────────────────────────────────────────────────

async def test_imports():
    from tgmock import TelegramMockServer, BotTestClient, BotResponse
    assert TelegramMockServer
    assert BotTestClient
    assert BotResponse


# ── Tests: send / tap ─────────────────────────────────────────────────────────

async def test_send_message(client: BotTestClient):
    resp = await client.send("hello")
    assert "echo: hello" in resp.text


async def test_tap_button(client: BotTestClient):
    resp = await client.send("show buttons")
    assert resp.has_button("Button A")
    assert resp.has_button("Button B")
    resp2 = await client.tap("Button A", resp)
    assert "btn_a" in resp2.text


async def test_tap_partial_label(client: BotTestClient):
    resp = await client.send("hi")
    resp2 = await client.tap("button b", resp)  # lowercase partial match
    assert "btn_b" in resp2.text


async def test_tap_missing_button_raises(client: BotTestClient):
    resp = await client.send("hi")
    with pytest.raises(ValueError, match="not found"):
        await client.tap("nonexistent", resp)


async def test_bot_response_properties(client: BotTestClient):
    resp = await client.send("test")
    assert isinstance(resp.text, str)
    assert isinstance(resp.all_text, str)
    assert isinstance(resp.buttons, list)
    assert len(resp.buttons) == 2
    assert repr(resp)


# ── Tests: multi-user isolation ───────────────────────────────────────────────

async def test_multiple_users_isolated(mock_server: TelegramMockServer):
    u1 = BotTestClient("http://localhost:18999", user_id=next_user_id(), default_timeout=5.0)
    u2 = BotTestClient("http://localhost:18999", user_id=next_user_id(), default_timeout=5.0)
    await u1.start()
    await u2.start()
    try:
        r1, r2 = await asyncio.gather(
            u1.send("user1 message"),
            u2.send("user2 message"),
        )
        assert "user1 message" in r1.text
        assert "user2 message" in r2.text
        assert r1.text != r2.text
    finally:
        await u1.stop()
        await u2.stop()


# ── Tests: events ─────────────────────────────────────────────────────────────

async def test_events_empty_by_default(client: BotTestClient):
    events = await client.events()
    assert events == []


async def test_events_post_and_retrieve(client: BotTestClient):
    import aiohttp
    async with aiohttp.ClientSession() as s:
        await s.post("http://localhost:18999/test/event", json={
            "user_id": client.user_id,
            "type": "tool_call",
            "data": {"tool": "add_task", "args": {"title": "buy milk"}},
        })

    events = await client.events()
    assert len(events) == 1
    assert events[0]["type"] == "tool_call"
    assert events[0]["data"]["tool"] == "add_task"

    filtered = await client.events(type="tool_call")
    assert len(filtered) == 1

    empty = await client.events(type="other_type")
    assert empty == []


async def test_get_tool_calls_compat_shim(client: BotTestClient):
    import aiohttp
    async with aiohttp.ClientSession() as s:
        await s.post("http://localhost:18999/test/event", json={
            "user_id": client.user_id,
            "type": "tool_call",
            "data": {"tool": "delete_task"},
        })
    calls = await client.get_tool_calls()
    assert calls == [{"tool": "delete_task"}]


# ── Tests: reset ──────────────────────────────────────────────────────────────

async def test_reset_clears_state(client: BotTestClient):
    await client.send("hello")
    import aiohttp
    async with aiohttp.ClientSession() as s:
        await s.post("http://localhost:18999/test/event", json={
            "user_id": client.user_id, "type": "x", "data": {}
        })
    await client.reset()
    assert await client.responses() == []
    assert await client.events() == []


# ── Tests: users endpoint ─────────────────────────────────────────────────────

async def test_users_endpoint(mock_server: TelegramMockServer, client: BotTestClient):
    await client.send("ping")
    import aiohttp
    async with aiohttp.ClientSession() as s:
        async with s.get("http://localhost:18999/test/users") as r:
            users = await r.json()
    user_ids = [u["user_id"] for u in users]
    assert client.user_id in user_ids


# ── Tests: tap_silent ─────────────────────────────────────────────────────────

async def test_tap_silent(client: BotTestClient):
    resp = await client.send("hi")
    await client.tap_silent("Button A", resp)  # should not raise


# ── Tests: config ─────────────────────────────────────────────────────────────

def test_config_defaults():
    cfg = TgmockConfig()
    assert cfg.port == 8999
    assert cfg.token == "test:token"
    assert cfg.settle_ms == 400
    assert cfg.env == {}


def test_config_load_missing_file(tmp_path):
    cfg = load_config(tmp_path)
    assert cfg.port == 8999


def test_config_load_from_toml(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.tgmock]\nport = 1234\nready_log = "ready"\n\n[tool.tgmock.env]\nFOO = "bar"\n'
    )
    cfg = load_config(tmp_path)
    assert cfg.port == 1234
    assert cfg.ready_log == "ready"
    assert cfg.env == {"FOO": "bar"}


# ── Tests: user_id ────────────────────────────────────────────────────────────

def test_user_id_unique():
    ids = [next_user_id() for _ in range(100)]
    assert len(set(ids)) == 100


def test_user_id_monotonic():
    a = next_user_id()
    b = next_user_id()
    assert b > a
