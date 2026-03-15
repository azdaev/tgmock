---
name: tgmock:setup
description: Guide per-project setup for tgmock bot testing. Use this skill whenever a user wants to set up, configure, or install tgmock for their Telegram bot project — even if they just say "I want to test my bot" or "set up testing" without mentioning tgmock by name. Also use when they hit setup-related errors like "Bot exited before ready" or "port in use".
---

You are helping the user configure tgmock for their Telegram bot project.

## Step 1: Check if tgmock is installed

```bash
which tgmock && tgmock mcp --help > /dev/null 2>&1 && echo "ok"
```

If not installed:
```bash
pipx install "tgmock[mcp]"
```

## Step 2: Configure the project

Add these to the project's `.env` file:

```env
# Required
TGMOCK_BOT_COMMAND=python main.py        # command to start your bot
TGMOCK_READY_LOG=Bot starting            # substring in bot output that means "ready"

# For compiled languages (Go, Rust) — pre-build before starting
# TGMOCK_BUILD_COMMAND=go build -o /tmp/mybot ./cmd/server
# TGMOCK_BOT_COMMAND=/tmp/mybot

# Optional overrides
# TGMOCK_PORT=8999
# TGMOCK_STARTUP_TIMEOUT=30
# TGMOCK_AUTO_PATCH=true              # enabled by default for Python bots
```

Or configure in `pyproject.toml`:
```toml
[tool.tgmock]
bot_command = "python main.py"
ready_log = "Bot starting"
```

**How to find TGMOCK_READY_LOG**: look at what your bot prints when it's ready to receive messages. For aiogram bots it's usually "Bot starting" or "Polling started". Run the bot command and look at the first log lines.

## Step 3: Auto-patch (Python bots — no code changes needed!)

For **Python bots** (aiogram, python-telegram-bot, etc.), tgmock automatically patches HTTP clients (aiohttp, httpx) so your bot talks to the mock server without any code changes. This is enabled by default — just configure `.env` and go.

To disable auto-patching (e.g. if you already have `BOT_API_BASE` support):
```env
TGMOCK_AUTO_PATCH=false
```

## Step 3b: Manual setup (non-Python bots or auto_patch=false)

If auto-patch is disabled or you're using a non-Python bot, add `BOT_API_BASE` support manually. tgmock injects `BOT_API_BASE` automatically — the bot must use it to redirect API calls to the mock server.

**For aiogram 3.x** — add these lines to `main.py`:
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

**For python-telegram-bot**:
```python
import os
base_url = os.environ.get("BOT_API_BASE", "https://api.telegram.org/bot")
application = Application.builder().token(TOKEN).base_url(base_url).build()
```

**For Go (telegram-bot-api)**:
```go
bot, err := tgbotapi.NewBotAPIWithAPIEndpoint(token, os.Getenv("BOT_API_BASE")+"/bot%s/%s")
```

**For Node.js (telegraf)**:
```js
const bot = new Telegraf(token, {
  telegram: { apiRoot: process.env.BOT_API_BASE || 'https://api.telegram.org' }
})
```

## Step 4: Verify setup

Use `tg_start` to test. If it fails, `tg_logs` will show what the bot printed.

```
tg_start → tg_send("hello") → tg_snapshot → tg_stop
```

Common issues:
- **"Bot exited before ready"**: wrong TGMOCK_READY_LOG, or missing env vars. Check `tg_logs`.
- **Bot responds with 404**: BOT_API_BASE not wired up and auto-patch not active. Check if your bot uses aiohttp or httpx (auto-patch supports these).
- **Port in use**: another tgmock session running. Call `tg_stop` first.
- **Auto-patch not working**: make sure TGMOCK_BOT_COMMAND starts with `python` or `python3`. For virtual envs, use the full path: `TGMOCK_BOT_COMMAND=.venv/bin/python main.py`.
