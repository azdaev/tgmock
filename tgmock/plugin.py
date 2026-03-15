"""
pytest plugin — provides tg_server, tg_bot, tg_client, tg_client_factory fixtures.

Auto-registered via entry_points["pytest11"] = "tgmock = tgmock.plugin".
No manual import needed in conftest.py.

Configuration in pyproject.toml:
    [tool.tgmock]
    bot_command = "python main.py"
    port = 8999
    token = "test:token"
    settle_ms = 400
    ready_log = "bot starting"
    startup_timeout = 15
    default_timeout = 25

    [tool.tgmock.env]
    DATABASE_URL = "postgres://..."
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncGenerator, Callable
from pathlib import Path

import pytest
import pytest_asyncio

from tgmock._config import TgmockConfig, load_config
from tgmock._user_id import next_user_id
from tgmock.server import TelegramMockServer
from tgmock.client import BotTestClient


# ── pytest hooks ─────────────────────────────────────────────────────────────

def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "tgmock: marks tests that use the tgmock bot fixtures"
    )
    # Set asyncio_mode=auto if not already set by user.
    try:
        if not config.option.__dict__.get("asyncio_mode"):
            config.option.asyncio_mode = "auto"
    except AttributeError:
        pass


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("tgmock")
    group.addoption("--tgmock-port", default=None, type=int,
                    help="Override [tool.tgmock] port")
    group.addoption("--tgmock-command", default=None,
                    help="Override [tool.tgmock] bot_command")


# ── config fixture (session, sync) ───────────────────────────────────────────

@pytest.fixture(scope="session")
def tgmock_config(request: pytest.FixtureRequest) -> TgmockConfig:
    """Loads [tool.tgmock] from pyproject.toml. CLI flags override file values."""
    cfg = load_config(Path(request.config.rootdir))
    if (p := request.config.getoption("--tgmock-port", default=None)):
        cfg.port = p
    if (c := request.config.getoption("--tgmock-command", default=None)):
        cfg.bot_command = c
    return cfg


# ── tg_server: session-scoped mock Telegram API server ───────────────────────

@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def tg_server(tgmock_config: TgmockConfig) -> AsyncGenerator[TelegramMockServer, None]:
    """
    Starts the aiohttp fake Telegram API server once per test session.
    Yields the TelegramMockServer instance.
    """
    mock = TelegramMockServer(token=tgmock_config.token, port=tgmock_config.port)
    runner = await mock.start()
    try:
        yield mock
    finally:
        await runner.cleanup()


# ── tg_bot: session-scoped subprocess bot ────────────────────────────────────

@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def tg_bot(
    tg_server: TelegramMockServer,
    tgmock_config: TgmockConfig,
) -> AsyncGenerator[subprocess.Popen, None]:
    """
    Launches the bot as a subprocess, waits for `ready_log` in stdout, yields Popen.

    The bot is configured via environment variables:
      BOT_API_BASE  — points at the mock server (always injected)
      BOT_TOKEN     — fake token matching the mock server
      + everything in [tool.tgmock.env]
    """
    base_url = f"http://localhost:{tgmock_config.port}"
    env = {
        **os.environ,
        "BOT_API_BASE": base_url,
        "BOT_TOKEN": tgmock_config.token,
        **tgmock_config.env,
    }

    # Auto-patch: monkey-patch HTTP clients so the bot needs no code changes
    autopatch_tmpdir = None
    if tgmock_config.auto_patch:
        from tgmock._autopatch import prepare_autopatch, is_python_command
        if is_python_command(tgmock_config.bot_command):
            autopatch_tmpdir, patch_env = prepare_autopatch(base_url)
            env.update(patch_env)

    cmd = tgmock_config.bot_command.split()
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
    )

    ready_log = tgmock_config.ready_log.lower()
    startup_timeout = tgmock_config.startup_timeout

    async def _wait_ready() -> None:
        loop = asyncio.get_event_loop()
        while True:
            line = await loop.run_in_executor(None, proc.stdout.readline)
            if not line:
                raise RuntimeError(
                    f"tgmock: bot process exited before printing ready_log={ready_log!r}"
                )
            sys.stdout.write(f"[BOT] {line}")
            if ready_log in line.lower():
                return

    try:
        await asyncio.wait_for(_wait_ready(), timeout=startup_timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError(
            f"tgmock: bot did not print {ready_log!r} within {startup_timeout}s"
        )

    # Drain stdout in background to prevent pipe deadlock.
    async def _drain() -> None:
        loop = asyncio.get_event_loop()
        while proc.poll() is None:
            line = await loop.run_in_executor(None, proc.stdout.readline)
            if line:
                sys.stdout.write(f"[BOT] {line}")

    drain_task = asyncio.create_task(_drain())

    try:
        yield proc
    finally:
        proc.terminate()
        try:
            await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(None, proc.wait),
                timeout=5.0,
            )
        except asyncio.TimeoutError:
            proc.kill()
        drain_task.cancel()
        await asyncio.gather(drain_task, return_exceptions=True)
        if autopatch_tmpdir:
            import shutil
            shutil.rmtree(autopatch_tmpdir, ignore_errors=True)


# ── tg_client: function-scoped client with unique user_id ────────────────────

@pytest_asyncio.fixture(scope="function")
async def tg_client(
    tg_server: TelegramMockServer,
    tg_bot: subprocess.Popen,
    tgmock_config: TgmockConfig,
) -> AsyncGenerator[BotTestClient, None]:
    """
    Returns a BotTestClient with a unique user_id per test.
    Clears state on setup and teardown.
    """
    uid = next_user_id()
    base_url = f"http://localhost:{tgmock_config.port}"
    client = BotTestClient(
        base_url=base_url,
        user_id=uid,
        default_timeout=tgmock_config.default_timeout,
    )
    await client.start()
    await client.clear()
    try:
        yield client
    finally:
        await client.clear()
        await client.stop()


# ── tg_client_factory: function-scoped factory for multi-user tests ──────────

@pytest_asyncio.fixture(scope="function")
async def tg_client_factory(
    tg_server: TelegramMockServer,
    tg_bot: subprocess.Popen,
    tgmock_config: TgmockConfig,
) -> AsyncGenerator[Callable[[], "asyncio.coroutine"], None]:
    """
    Returns an async factory that creates multiple BotTestClient instances,
    each with a distinct user_id.

    Usage:
        async def test_two_users(tg_client_factory):
            alice = await tg_client_factory()
            bob   = await tg_client_factory()
    """
    base_url = f"http://localhost:{tgmock_config.port}"
    default_to = tgmock_config.default_timeout
    _created: list[BotTestClient] = []

    async def _make() -> BotTestClient:
        uid = next_user_id()
        client = BotTestClient(base_url=base_url, user_id=uid, default_timeout=default_to)
        _created.append(client)
        await client.start()
        await client.clear()
        return client

    try:
        yield _make
    finally:
        for c in _created:
            await c.clear()
            await c.stop()
