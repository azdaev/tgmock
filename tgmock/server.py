"""
Fake Telegram Bot API server for end-to-end bot testing.

Runs as a local aiohttp server. The bot points at it via BOT_API_BASE env var.

Fake Telegram API endpoints (aiogram / python-telegram-bot / etc. poll these):
  POST /bot{token}/getUpdates
  POST /bot{token}/sendMessage
  POST /bot{token}/editMessageText
  POST /bot{token}/answerCallbackQuery
  POST /bot{token}/sendChatAction
  POST /bot{token}/getMe
  POST /bot{token}/deleteMessage
  POST /bot{token}/editMessageReplyMarkup
  POST /bot{token}/sendPhoto
  POST /bot{token}/sendDocument
  POST /bot{token}/sendVoice
  POST /bot{token}/sendAudio
  POST /bot{token}/sendVideo
  POST /bot{token}/forwardMessage
  POST /bot{token}/copyMessage
  POST /bot{token}/sendLocation
  POST /bot{token}/sendContact
  POST /bot{token}/sendPoll
  POST /bot{token}/stopPoll
  POST /bot{token}/sendDice
  POST /bot{token}/pinChatMessage
  POST /bot{token}/unpinChatMessage
  POST /bot{token}/setMyCommands
  POST /bot{token}/getMyCommands
  POST /bot{token}/setWebhook
  POST /bot{token}/deleteWebhook

Test control endpoints:
  POST   /test/send              {"text": "...", "user_id": 123}
  POST   /test/callback          {"data": "...", "user_id": 123, "message_id": 1}
  GET    /test/responses         → list of captured bot messages (optionally ?user_id=X)
  DELETE /test/responses         → clear captured list (optionally ?user_id=X)
  POST   /test/event             {"user_id": 123, "type": "tool_call", "data": {...}}
  GET    /test/events            → list of custom events (?user_id=X[&type=tool_call])
  DELETE /test/events            → clear events (?user_id=X)
  POST   /test/register-reset    {"url": "http://bot/internal/reset"}
  POST   /test/reset-user        clear server state for user, call registered reset hook
  GET    /test/wait-response     event-driven wait until bot settles
  GET    /test/users             list active user IDs
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from aiohttp import web

log = logging.getLogger(__name__)

TEST_USER = {
    "id": 111,
    "is_bot": False,
    "first_name": "TestUser",
    "username": "testuser",
    "language_code": "ru",
}


class TelegramMockServer:
    def __init__(self, token: str, port: int = 8999):
        self.token = token
        self.port = port
        self._update_id = 1
        self._msg_id = 1
        # All updates ever created; getUpdates filters by offset
        self._updates: list[dict] = []
        self._new_update = asyncio.Event()
        # All sendMessage / editMessageText calls the bot made
        self._responses: dict[int, list[dict]] = {}
        self._response_seq: dict[int, int] = {}
        self._last_response_at: dict[int, float] = {}
        self._response_event: asyncio.Event = asyncio.Event()
        # Custom events posted by the bot (tool calls, state changes, etc.)
        self._events: dict[int, list[dict]] = {}
        # Optional reset hook: bot registers its callback URL here
        self._reset_url: str | None = None

    # ── helpers ───────────────────────────────────────────────────────────────

    def _next_update_id(self) -> int:
        uid = self._update_id
        self._update_id += 1
        return uid

    def _next_msg_id(self) -> int:
        mid = self._msg_id
        self._msg_id += 1
        return mid

    def _fake_message(self, text: str, user_id: int, msg_id: int | None = None) -> dict:
        mid = msg_id or self._next_msg_id()
        return {
            "message_id": mid,
            "from": {**TEST_USER, "id": user_id},
            "chat": {"id": user_id, "type": "private"},
            "date": int(time.time()),
            "text": text,
        }

    def _push_update(self, update: dict):
        self._updates.append(update)
        self._new_update.set()

    def _record_response(self, chat_id: int, record: dict):
        """Store a bot response and wake up any waiters."""
        self._responses.setdefault(chat_id, []).append(record)
        self._response_seq[chat_id] = self._response_seq.get(chat_id, 0) + 1
        self._last_response_at[chat_id] = asyncio.get_event_loop().time()
        self._response_event.set()

    # ── Telegram API: getUpdates (long-poll) ──────────────────────────────────

    async def handle_get_updates(self, request: web.Request) -> web.Response:
        data = await request.post()
        offset = int(data.get("offset") or request.rel_url.query.get("offset", 0))
        timeout = int(data.get("timeout") or request.rel_url.query.get("timeout", 0))
        deadline = asyncio.get_event_loop().time() + min(timeout, 2)

        while True:
            pending = [u for u in self._updates if u["update_id"] >= offset]
            if pending:
                return web.json_response({"ok": True, "result": pending})

            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                return web.json_response({"ok": True, "result": []})

            self._new_update.clear()
            try:
                await asyncio.wait_for(
                    asyncio.shield(self._new_update.wait()),
                    timeout=min(remaining, 0.3),
                )
            except asyncio.TimeoutError:
                pass

    # ── Telegram API: sendMessage ─────────────────────────────────────────────

    async def handle_send_message(self, request: web.Request) -> web.Response:
        data = await request.post()
        mid = self._next_msg_id()
        chat_id = int(data.get("chat_id", 0))
        raw_markup = data.get("reply_markup")
        record = {
            "method": "sendMessage",
            "chat_id": chat_id,
            "text": data.get("text", ""),
            "parse_mode": data.get("parse_mode"),
            "reply_markup": json.loads(raw_markup) if raw_markup else None,
            "message_id": mid,
        }
        self._record_response(chat_id, record)
        log.info(f"[BOT→USER] {record['text'][:120]}")
        return web.json_response({
            "ok": True,
            "result": self._fake_message(data.get("text", ""), chat_id, mid),
        })

    # ── Telegram API: editMessageText ─────────────────────────────────────────

    async def handle_edit_message_text(self, request: web.Request) -> web.Response:
        data = await request.post()
        chat_id = int(data.get("chat_id", 0))
        msg_id = int(data.get("message_id", 0))
        raw_markup = data.get("reply_markup")
        record = {
            "method": "editMessageText",
            "chat_id": chat_id,
            "message_id": msg_id,
            "text": data.get("text", ""),
            "parse_mode": data.get("parse_mode"),
            "reply_markup": json.loads(raw_markup) if raw_markup else None,
        }
        self._record_response(chat_id, record)
        log.info(f"[BOT→EDIT] {record['text'][:120]}")
        return web.json_response({
            "ok": True,
            "result": {
                "message_id": msg_id,
                "chat": {"id": chat_id, "type": "private"},
                "date": int(time.time()),
                "text": data.get("text", ""),
            },
        })

    # ── Telegram API: getMe ───────────────────────────────────────────────────

    async def handle_get_me(self, request: web.Request) -> web.Response:
        return web.json_response({
            "ok": True,
            "result": {
                "id": 999999,
                "is_bot": True,
                "first_name": "MockBot",
                "username": "mockbot",
            },
        })

    # ── Telegram API: no-ops ──────────────────────────────────────────────────

    async def handle_answer_callback_query(self, request: web.Request) -> web.Response:
        await request.post()
        return web.json_response({"ok": True, "result": True})

    async def handle_send_chat_action(self, request: web.Request) -> web.Response:
        await request.post()
        return web.json_response({"ok": True, "result": True})

    async def handle_delete_message(self, request: web.Request) -> web.Response:
        await request.post()
        return web.json_response({"ok": True, "result": True})

    async def handle_edit_message_reply_markup(self, request: web.Request) -> web.Response:
        data = await request.post()
        chat_id = int(data.get("chat_id", 0))
        msg_id = int(data.get("message_id", 0))
        raw_markup = data.get("reply_markup")
        self._responses.setdefault(chat_id, []).append({
            "method": "editMessageReplyMarkup",
            "chat_id": chat_id,
            "message_id": msg_id,
            "reply_markup": json.loads(raw_markup) if raw_markup else None,
        })
        return web.json_response({
            "ok": True,
            "result": {
                "message_id": msg_id,
                "chat": {"id": chat_id, "type": "private"},
                "date": int(time.time()),
                "text": "",
            },
        })

    # ── Telegram API: media stubs (HIGH priority) ─────────────────────────────

    async def _handle_send_media(self, request: web.Request, method: str, media_key: str) -> web.Response:
        """Generic handler for sendPhoto, sendDocument, sendVoice, etc."""
        data = await request.post()
        chat_id = int(data.get("chat_id", 0))
        mid = self._next_msg_id()
        record = {
            "method": method,
            "chat_id": chat_id,
            "message_id": mid,
            "caption": data.get("caption", ""),
            media_key: {"file_id": f"mock_{media_key}_{mid}"},
        }
        self._record_response(chat_id, record)
        return web.json_response({
            "ok": True,
            "result": {
                "message_id": mid,
                "chat": {"id": chat_id, "type": "private"},
                "date": int(time.time()),
                media_key: {"file_id": f"mock_{media_key}_{mid}"},
            },
        })

    async def handle_send_photo(self, request: web.Request) -> web.Response:
        return await self._handle_send_media(request, "sendPhoto", "photo")

    async def handle_send_document(self, request: web.Request) -> web.Response:
        return await self._handle_send_media(request, "sendDocument", "document")

    async def handle_send_voice(self, request: web.Request) -> web.Response:
        return await self._handle_send_media(request, "sendVoice", "voice")

    async def handle_send_audio(self, request: web.Request) -> web.Response:
        return await self._handle_send_media(request, "sendAudio", "audio")

    async def handle_send_video(self, request: web.Request) -> web.Response:
        return await self._handle_send_media(request, "sendVideo", "video")

    async def handle_forward_message(self, request: web.Request) -> web.Response:
        data = await request.post()
        chat_id = int(data.get("chat_id", 0))
        mid = self._next_msg_id()
        return web.json_response({"ok": True, "result": self._fake_message("", chat_id, mid)})

    async def handle_copy_message(self, request: web.Request) -> web.Response:
        data = await request.post()
        mid = self._next_msg_id()
        return web.json_response({"ok": True, "result": {"message_id": mid}})

    async def handle_send_location(self, request: web.Request) -> web.Response:
        data = await request.post()
        chat_id = int(data.get("chat_id", 0))
        mid = self._next_msg_id()
        return web.json_response({
            "ok": True,
            "result": {
                "message_id": mid,
                "chat": {"id": chat_id, "type": "private"},
                "date": int(time.time()),
                "location": {"latitude": 0.0, "longitude": 0.0},
            },
        })

    async def handle_send_contact(self, request: web.Request) -> web.Response:
        data = await request.post()
        chat_id = int(data.get("chat_id", 0))
        mid = self._next_msg_id()
        return web.json_response({"ok": True, "result": self._fake_message("", chat_id, mid)})

    async def handle_send_poll(self, request: web.Request) -> web.Response:
        data = await request.post()
        chat_id = int(data.get("chat_id", 0))
        mid = self._next_msg_id()
        return web.json_response({
            "ok": True,
            "result": {
                "message_id": mid,
                "chat": {"id": chat_id, "type": "private"},
                "date": int(time.time()),
                "poll": {"id": str(mid), "question": data.get("question", ""), "options": [], "is_closed": False},
            },
        })

    async def handle_stop_poll(self, request: web.Request) -> web.Response:
        await request.post()
        return web.json_response({"ok": True, "result": {"id": "0", "question": "", "options": [], "is_closed": True}})

    async def handle_send_dice(self, request: web.Request) -> web.Response:
        data = await request.post()
        chat_id = int(data.get("chat_id", 0))
        mid = self._next_msg_id()
        return web.json_response({
            "ok": True,
            "result": {
                "message_id": mid,
                "chat": {"id": chat_id, "type": "private"},
                "date": int(time.time()),
                "dice": {"emoji": "🎲", "value": 1},
            },
        })

    async def _noop(self, request: web.Request) -> web.Response:
        await request.post()
        return web.json_response({"ok": True, "result": True})

    # ── Test control: inject a user message ───────────────────────────────────

    async def test_send(self, request: web.Request) -> web.Response:
        """POST /test/send  {"text": "привет", "user_id": 111}"""
        data = await request.json()
        text = data.get("text", "")
        user_id = int(data.get("user_id", TEST_USER["id"]))
        msg_id = self._next_msg_id()
        update = {
            "update_id": self._next_update_id(),
            "message": self._fake_message(text, user_id, msg_id),
        }
        self._push_update(update)
        log.info(f"[USER→BOT] {text!r}")
        return web.json_response({
            "ok": True,
            "update_id": update["update_id"],
            "after_seq": self._response_seq.get(user_id, 0),
        })

    # ── Test control: inject a callback query (button click) ──────────────────

    async def test_callback(self, request: web.Request) -> web.Response:
        """POST /test/callback  {"data": "choice:yes", "user_id": 111, "message_id": 5}"""
        data = await request.json()
        user_id = int(data.get("user_id", TEST_USER["id"]))
        callback_data = data.get("data", "")
        message_id = int(data.get("message_id", 1))
        update = {
            "update_id": self._next_update_id(),
            "callback_query": {
                "id": str(self._next_update_id()),
                "from": {**TEST_USER, "id": user_id},
                "message": {
                    "message_id": message_id,
                    "chat": {"id": user_id, "type": "private"},
                    "date": int(time.time()),
                    "text": "",
                    "from": {"id": 999999, "is_bot": True, "first_name": "MockBot"},
                },
                "chat_instance": "mock",
                "data": callback_data,
            },
        }
        self._push_update(update)
        log.info(f"[USER→BUTTON] {callback_data!r}")
        return web.json_response({
            "ok": True,
            "update_id": update["update_id"],
            "after_seq": self._response_seq.get(user_id, 0),
        })

    # ── Test control: read captured responses ─────────────────────────────────

    async def test_responses(self, request: web.Request) -> web.Response:
        """GET /test/responses[?user_id=X]"""
        user_id = request.rel_url.query.get("user_id")
        if user_id is not None:
            return web.json_response(self._responses.get(int(user_id), []))
        all_msgs = [msg for msgs in self._responses.values() for msg in msgs]
        return web.json_response(all_msgs)

    async def test_clear(self, request: web.Request) -> web.Response:
        """DELETE /test/responses[?user_id=X]"""
        user_id = request.rel_url.query.get("user_id")
        if user_id is not None:
            self._responses.pop(int(user_id), None)
        else:
            self._responses.clear()
        return web.json_response({"ok": True})

    # ── Test control: custom event bus ────────────────────────────────────────

    async def test_post_event(self, request: web.Request) -> web.Response:
        """POST /test/event  {"user_id": 123, "type": "tool_call", "data": {...}}"""
        body = await request.json()
        uid = int(body["user_id"])
        entry = {
            "type": body["type"],
            "data": body.get("data", {}),
            "ts": asyncio.get_event_loop().time(),
        }
        self._events.setdefault(uid, []).append(entry)
        return web.json_response({"ok": True})

    async def test_get_events(self, request: web.Request) -> web.Response:
        """GET /test/events?user_id=X[&type=tool_call]"""
        uid = int(request.rel_url.query.get("user_id", 0))
        type_filter = request.rel_url.query.get("type")
        events = self._events.get(uid, [])
        if type_filter:
            events = [e for e in events if e["type"] == type_filter]
        return web.json_response(events)

    async def test_clear_events(self, request: web.Request) -> web.Response:
        """DELETE /test/events[?user_id=X]"""
        uid_str = request.rel_url.query.get("user_id")
        if uid_str is not None:
            self._events.pop(int(uid_str), None)
        else:
            self._events.clear()
        return web.json_response({"ok": True})

    # ── Test control: reset hook registration ────────────────────────────────

    async def test_register_reset(self, request: web.Request) -> web.Response:
        """POST /test/register-reset  {"url": "http://bot-host/internal/reset"}"""
        body = await request.json()
        self._reset_url = body["url"]
        log.info(f"[TGMOCK] reset hook registered: {self._reset_url}")
        return web.json_response({"ok": True})

    async def test_reset_user(self, request: web.Request) -> web.Response:
        """POST /test/reset-user?user_id=X
        Clears server-side state for this user, then calls the bot's reset hook if registered.
        """
        uid_str = request.rel_url.query.get("user_id")
        uid = int(uid_str) if uid_str else None

        if uid is not None:
            self._responses.pop(uid, None)
            self._events.pop(uid, None)
            self._response_seq.pop(uid, None)
            self._last_response_at.pop(uid, None)
        else:
            self._responses.clear()
            self._events.clear()
            self._response_seq.clear()
            self._last_response_at.clear()

        if self._reset_url:
            import aiohttp as _aiohttp
            try:
                async with _aiohttp.ClientSession() as s:
                    await s.post(
                        self._reset_url,
                        json={"user_id": uid},
                        timeout=_aiohttp.ClientTimeout(total=3.0),
                    )
            except Exception as e:
                log.warning(f"[TGMOCK] reset hook call failed: {e}")

        return web.json_response({"ok": True})

    # ── Test control: list active users ──────────────────────────────────────

    async def test_users(self, request: web.Request) -> web.Response:
        """GET /test/users — list all user IDs with response count and last message preview."""
        result = []
        for uid, msgs in self._responses.items():
            last_text = ""
            if msgs:
                last_text = msgs[-1].get("text", "")[:60]
            result.append({
                "user_id": uid,
                "response_count": len(msgs),
                "last_message": last_text,
            })
        return web.json_response(result)

    # ── Test control: event-driven wait ───────────────────────────────────────

    async def test_wait_response(self, request: web.Request) -> web.Response:
        """GET /test/wait-response?user_id=X&after_seq=N&settle_ms=400&timeout=30
        Blocks until the bot has sent at least one response after after_seq for this user,
        then waits for a settle period with no new responses.
        """
        user_id = int(request.rel_url.query.get("user_id", 0))
        after_seq = int(request.rel_url.query.get("after_seq", 0))
        settle_ms = float(request.rel_url.query.get("settle_ms", 400)) / 1000
        timeout = float(request.rel_url.query.get("timeout", 30))

        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout

        # Phase 1: wait for at least one new response after after_seq
        while self._response_seq.get(user_id, 0) <= after_seq:
            if loop.time() >= deadline:
                return web.json_response({"ok": False, "reason": "timeout"})
            self._response_event.clear()
            if self._response_seq.get(user_id, 0) > after_seq:
                break
            try:
                remaining = deadline - loop.time()
                await asyncio.wait_for(
                    asyncio.shield(self._response_event.wait()),
                    timeout=min(remaining, 0.3),
                )
            except asyncio.TimeoutError:
                pass

        # Phase 2: settle — wait until no new response for settle_ms
        while True:
            last = self._last_response_at.get(user_id, 0.0)
            since = loop.time() - last
            if since >= settle_ms:
                break
            if loop.time() >= deadline:
                break
            await asyncio.sleep(min(settle_ms - since + 0.01, 0.1))

        return web.json_response({"ok": True})

    # ── server setup ──────────────────────────────────────────────────────────

    def build_app(self) -> web.Application:
        app = web.Application()
        t = self.token

        # Telegram Bot API endpoints
        app.router.add_post(f"/bot{t}/getUpdates", self.handle_get_updates)
        app.router.add_post(f"/bot{t}/sendMessage", self.handle_send_message)
        app.router.add_post(f"/bot{t}/editMessageText", self.handle_edit_message_text)
        app.router.add_post(f"/bot{t}/getMe", self.handle_get_me)
        app.router.add_post(f"/bot{t}/answerCallbackQuery", self.handle_answer_callback_query)
        app.router.add_post(f"/bot{t}/sendChatAction", self.handle_send_chat_action)
        app.router.add_post(f"/bot{t}/deleteMessage", self.handle_delete_message)
        app.router.add_post(f"/bot{t}/editMessageReplyMarkup", self.handle_edit_message_reply_markup)
        # Media
        app.router.add_post(f"/bot{t}/sendPhoto", self.handle_send_photo)
        app.router.add_post(f"/bot{t}/sendDocument", self.handle_send_document)
        app.router.add_post(f"/bot{t}/sendVoice", self.handle_send_voice)
        app.router.add_post(f"/bot{t}/sendAudio", self.handle_send_audio)
        app.router.add_post(f"/bot{t}/sendVideo", self.handle_send_video)
        app.router.add_post(f"/bot{t}/forwardMessage", self.handle_forward_message)
        app.router.add_post(f"/bot{t}/copyMessage", self.handle_copy_message)
        app.router.add_post(f"/bot{t}/sendLocation", self.handle_send_location)
        app.router.add_post(f"/bot{t}/sendContact", self.handle_send_contact)
        app.router.add_post(f"/bot{t}/sendPoll", self.handle_send_poll)
        app.router.add_post(f"/bot{t}/stopPoll", self.handle_stop_poll)
        app.router.add_post(f"/bot{t}/sendDice", self.handle_send_dice)
        # No-ops
        app.router.add_post(f"/bot{t}/pinChatMessage", self._noop)
        app.router.add_post(f"/bot{t}/unpinChatMessage", self._noop)
        app.router.add_post(f"/bot{t}/setMyCommands", self._noop)
        app.router.add_post(f"/bot{t}/getMyCommands", self._noop)
        app.router.add_post(f"/bot{t}/setWebhook", self._noop)
        app.router.add_post(f"/bot{t}/deleteWebhook", self._noop)

        # Test control endpoints
        app.router.add_post("/test/send", self.test_send)
        app.router.add_post("/test/callback", self.test_callback)
        app.router.add_get("/test/responses", self.test_responses)
        app.router.add_delete("/test/responses", self.test_clear)
        app.router.add_post("/test/event", self.test_post_event)
        app.router.add_get("/test/events", self.test_get_events)
        app.router.add_delete("/test/events", self.test_clear_events)
        app.router.add_post("/test/register-reset", self.test_register_reset)
        app.router.add_post("/test/reset-user", self.test_reset_user)
        app.router.add_get("/test/wait-response", self.test_wait_response)
        app.router.add_get("/test/users", self.test_users)
        return app

    async def start(self):
        app = self.build_app()
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "localhost", self.port)
        await site.start()
        log.info(f"Telegram mock server running on http://localhost:{self.port}")
        return runner
