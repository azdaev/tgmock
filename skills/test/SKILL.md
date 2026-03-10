---
name: tgmock:test
description: Interactive Telegram bot testing workflow using tg_* MCP tools
---

You are an interactive Telegram bot tester. Use the tgmock MCP tools to test the bot.

## Tool Reference

| Tool | What it does |
|------|-------------|
| `tg_start` | Start mock Telegram API + bot subprocess. Reads `.env` automatically. |
| `tg_send(text)` | Send a message as a test user, wait for bot response. |
| `tg_tap(label)` | Click an inline keyboard button by label (partial match). |
| `tg_snapshot` | Get the current conversation state without sending anything. |
| `tg_logs(tail=50)` | Get last N lines of bot stdout/stderr. |
| `tg_restart` | Restart the bot + reset mock state (keeps server running). |
| `tg_reset` | Reset a user's state: clear responses, events, trigger bot reset hook. |
| `tg_events` | Get custom events posted by the bot (e.g. tool calls, DB writes). |
| `tg_users` | List active test users. |
| `tg_stop` | Stop everything. |

## Basic workflow

```
1. tg_start()                    # start bot (reads .env config)
2. tg_send("/start")             # send a message
3. tg_snapshot()                 # inspect what the bot said
4. tg_tap("Button label")        # click a button
5. tg_send("some text")          # continue the conversation
6. tg_stop()                     # clean up
```

## Multi-user testing

Use `user_id` parameter to simulate multiple users:
```
tg_send("hello", user_id=111)    # user 1
tg_send("hi", user_id=222)       # user 2 (independent session)
tg_snapshot(user_id=222)         # see user 2's conversation
```

## Debugging failures

When something goes wrong:
1. `tg_logs()` — see what the bot printed (errors, stack traces)
2. `tg_snapshot()` — see the current conversation state
3. `tg_events()` — see any custom events/tool calls the bot posted

## Test pattern: verify a flow

```python
# Example: test that /start triggers onboarding
tg_start()
tg_send("/start")
snap = tg_snapshot()
assert "Welcome" in snap["snapshot"]   # or inspect snap["messages"] directly
tg_stop()
```

## Tips

- `tg_send` waits up to 25s for a response — if timeout, check `tg_logs` for errors
- `tg_tap` does partial case-insensitive button match — "next" matches "Next step"
- Between test scenarios, use `tg_reset()` to clear user state without restarting
- Use `tg_restart()` to reset everything when bot state is corrupted between runs
- `tg_events` is useful for asserting side effects (DB writes, AI calls) without checking UI text
