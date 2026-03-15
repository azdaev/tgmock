---
name: tgmock:test
description: Interactive Telegram bot testing workflow using tg_* MCP tools. Use this skill whenever the user wants to test, debug, or verify their Telegram bot — including when they say "test my bot", "try sending a message", "check if the bot works", "debug the bot", or want to verify a specific bot flow or feature. Also triggers when the user mentions tg_start, tg_send, tg_tap, or other tgmock tools.
---

You are an interactive Telegram bot tester. Use the tgmock MCP tools to test the bot.

## Tool Reference

| Tool | What it does |
|------|-------------|
| `tg_start` | Start mock Telegram API + bot subprocess. Reads `.env` automatically. Python bots are auto-patched — no code changes needed. |
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
1. tg_start()                    # start bot (reads .env config, auto-patches Python bots)
2. tg_send("/start")             # send a message
3. tg_snapshot()                 # inspect what the bot said
4. tg_tap("Button label")        # click a button
5. tg_send("some text")          # continue the conversation
6. tg_stop()                     # clean up
```

## Testing patterns

### Verify a command flow
Send a command and check the response contains what you expect:
```
tg_start()
tg_send("/start")
# Check snapshot for welcome message, buttons, expected text
tg_send("/help")
# Check snapshot for help text
tg_stop()
```

### Test button interactions
When the bot shows inline keyboard buttons, use `tg_tap` to click them by label.
`tg_tap` automatically looks up the callback_data from the button label — you don't need to know the raw data:
```
tg_send("/menu")
# Bot shows buttons — tap one by its visible label
tg_tap("Settings")
# Bot shows settings — tap deeper (works even if bot edits the message)
tg_tap("Language")
```

For **reply keyboard** buttons (the persistent buttons at the bottom of the chat), use `tg_send` with the button text instead of `tg_tap`:
```
tg_send("🌅 Утро")     # reply keyboard button
```

### Test multi-step flows
For flows that require several user inputs (onboarding, forms, quizzes):
```
tg_send("/register")
# Bot asks for name
tg_send("John")
# Bot asks for email
tg_send("john@example.com")
# Bot confirms registration
```

### Test error handling
Send unexpected input to verify the bot handles it gracefully:
```
tg_send("")               # empty message
tg_send("asdkjhasdkjh")   # gibberish
tg_send("/nonexistent")   # unknown command
```

### Multi-user testing
Use `user_id` parameter to simulate multiple users:
```
tg_send("hello", user_id=111)    # user 1
tg_send("hi", user_id=222)       # user 2 (independent session)
tg_snapshot(user_id=222)         # see user 2's conversation
```

### Assert side effects with events
If the bot posts custom events (DB writes, API calls), verify them:
```
tg_send("/delete_account")
tg_tap("Confirm")
tg_events(type="db_write")       # check that deletion was recorded
```

## Debugging failures

When something goes wrong:
1. `tg_logs()` — see what the bot printed (errors, stack traces)
2. `tg_snapshot()` — see the current conversation state
3. `tg_events()` — see any custom events/tool calls the bot posted
4. `tg_restart()` — restart fresh if the bot is in a bad state

## Tips

- `tg_send` waits up to 25s for a response — if timeout, check `tg_logs` for errors
- `tg_tap` does partial case-insensitive button match — "next" matches "Next step"
- Between test scenarios, use `tg_reset()` to clear user state without restarting
- Use `tg_restart()` to reset everything when bot state is corrupted between runs
- `tg_events` is useful for asserting side effects (DB writes, AI calls) without checking UI text
- Auto-patch handles Python bots automatically — no need to add BOT_API_BASE support to your code
