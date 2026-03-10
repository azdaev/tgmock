# tgmock

Fake Telegram API server for testing bots — pytest plugin + Claude Code MCP server.

Works with any bot framework (aiogram, python-telegram-bot, Telegraf, go-telegram-bot-api, etc.).

**TL;DR** — add the marketplace, install the plugin, add 4 lines to your bot, and ask Claude to test it:

```bash
/plugin marketplace add azdaev/tgmock
/plugin install tgmock@tgmock
```

Claude will guide you through the rest with `/tgmock:setup` and `/tgmock:test`.

---

## Installation

### Claude Code plugin (recommended)

Installs the MCP server + skills automatically:

```bash
/plugin marketplace add azdaev/tgmock
/plugin install tgmock@tgmock
```

### MCP server only

Register manually without the plugin system:

```bash
claude mcp add tgmock --transport stdio -- python3 -m tgmock.mcp_server
```

Requires `pip install "tgmock[mcp]"` in the environment where Claude Code runs.

### pytest plugin only

```bash
pip install tgmock
```

The pytest plugin registers automatically via `pytest11` entry point.

## How it works

tgmock starts a local HTTP server that mimics the Telegram Bot API. Your bot talks to it instead of the real Telegram. You send messages and click buttons via test utilities; the bot responds to the fake server. No real Telegram account needed.

## Install

```bash
pip install tgmock          # pytest plugin only
pip install "tgmock[mcp]"   # + MCP server for Claude Code
```

## Configure your project

Add to your `.env`:

```env
TGMOCK_BOT_COMMAND=python main.py
TGMOCK_READY_LOG=Bot starting
```

For compiled languages (pre-build before starting):
```env
TGMOCK_BUILD_COMMAND=go build -o /tmp/mybot ./cmd/server
TGMOCK_BOT_COMMAND=/tmp/mybot
```

Or configure in `pyproject.toml`:
```toml
[tool.tgmock]
bot_command = "python main.py"
ready_log = "Bot starting"
port = 8999
startup_timeout = 15
```

Config priority: `TGMOCK_*` env vars > `TGMOCK_*` in `.env` file > `[tool.tgmock]` in `pyproject.toml` > defaults.

## Add BOT_API_BASE support to your bot

tgmock injects `BOT_API_BASE` automatically — your bot must use it.

**aiogram 3.x:**
```python
import os
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer

api_base = os.environ.get("BOT_API_BASE")
if api_base:
    session = AiohttpSession(api=TelegramAPIServer.from_base(api_base))
    bot = Bot(token=config.bot_token, session=session)
else:
    bot = Bot(token=config.bot_token)
```

**python-telegram-bot:**
```python
base_url = os.environ.get("BOT_API_BASE", "https://api.telegram.org/bot")
app = Application.builder().token(TOKEN).base_url(base_url).build()
```

**Telegraf (Node.js):**
```js
const bot = new Telegraf(token, {
  telegram: { apiRoot: process.env.BOT_API_BASE || 'https://api.telegram.org' }
})
```

**go-telegram-bot-api:**
```go
bot, _ := tgbotapi.NewBotAPIWithAPIEndpoint(token, os.Getenv("BOT_API_BASE")+"/bot%s/%s")
```

## pytest plugin

The pytest plugin is registered automatically after `pip install tgmock`.

```python
import pytest

@pytest.fixture(scope="session")
async def bot(tgmock_server, tgmock_bot):
    yield tgmock_bot

async def test_start(bot):
    await bot.send("/start")
    snap = await bot.snapshot()
    assert "Welcome" in snap
```

See `tests/` for examples.

## MCP server (Claude Code)

Register tgmock as a Claude Code MCP server:

```bash
claude mcp add tgmock --transport stdio -- python3 -m tgmock.mcp_server
```

Or install as a plugin:
```bash
claude plugin install /path/to/tgmock
```

### Available tools

| Tool | Description |
|------|-------------|
| `tg_start` | Start mock server + bot. All params optional if `.env` is configured. |
| `tg_send(text)` | Send message as test user, wait for bot response. |
| `tg_tap(label)` | Click inline keyboard button (partial label match). |
| `tg_snapshot` | Get current conversation state. |
| `tg_logs(tail=50)` | Get last N lines of bot stdout/stderr. |
| `tg_restart` | Restart bot + reset mock state (keeps server running). |
| `tg_reset` | Reset user state (clear responses/events). |
| `tg_events` | Get custom events posted by the bot. |
| `tg_users` | List active test users. |
| `tg_stop` | Stop everything. |

### Quick session

```
tg_start → tg_send("/start") → tg_snapshot → tg_tap("Button") → tg_stop
```

### Debugging

When the bot fails to start, the error includes the last 30 lines of bot output.
Use `tg_logs()` anytime to see what the bot is printing.

## Custom events

Your bot can post structured events to tgmock for assertion without parsing text:

```python
import aiohttp, os

async def post_event(type: str, data: dict):
    base = os.environ.get("BOT_API_BASE", "")
    if base:
        async with aiohttp.ClientSession() as s:
            await s.post(f"{base}/test/event", json={"type": type, **data})
```

Then assert with `tg_events(type="tool_call")`.

## License

MIT
