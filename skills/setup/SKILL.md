---
name: tgmock:setup
description: Guide per-project setup for tgmock bot testing
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
```

**How to find TGMOCK_READY_LOG**: look at what your bot prints when it's ready to receive messages. For aiogram bots it's usually "Bot starting" or "Polling started". Run `python main.py` and look at the first log lines.

## Step 3: Add BOT_API_BASE support to your bot

tgmock injects `BOT_API_BASE` automatically — the bot must use it to redirect API calls to the mock server.

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
- **Bot responds with 404**: BOT_API_BASE not wired up, bot still calls real Telegram API.
- **Port in use**: another tgmock session running. Call `tg_stop` first.
