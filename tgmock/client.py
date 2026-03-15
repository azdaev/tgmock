"""
BotTestClient — Playwright-style test client for the Telegram mock server.

Usage:
    resp = await client.send("📋 Today")
    assert "No tasks" in resp.text

    resp = await client.send("add task buy milk")
    assert resp.has_button("✅ Done")

    resp = await client.tap("✅ Done", resp)
    assert "Marked done" in resp.text

    calls = await client.events(type="tool_call")
    assert any(e["data"]["tool"] == "add_task" for e in calls)
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import aiohttp


@dataclass
class BotResponse:
    """One or more messages the bot sent in response to a single user action."""
    messages: list[dict]

    @property
    def text(self) -> str:
        """Text of the last message sent."""
        return self.messages[-1]["text"] if self.messages else ""

    @property
    def all_text(self) -> str:
        """All message texts joined with newlines."""
        return "\n".join(m.get("text", "") for m in self.messages)

    @property
    def keyboard(self) -> dict | None:
        """Inline keyboard of the last message that has one."""
        for msg in reversed(self.messages):
            mk = msg.get("reply_markup")
            if mk and "inline_keyboard" in mk:
                return mk
        return None

    @property
    def buttons(self) -> list[dict]:
        """Flat list of all inline buttons across all rows."""
        kb = self.keyboard
        if not kb:
            return []
        return [btn for row in kb["inline_keyboard"] for btn in row]

    def button_data(self, label: str) -> str | None:
        """Return callback_data for the first button whose text contains `label`."""
        label_lower = label.lower()
        for btn in self.buttons:
            if label_lower in btn["text"].lower():
                return btn["callback_data"]
        return None

    def message_id_with_keyboard(self) -> int | None:
        """message_id of the last message that has an inline keyboard."""
        for msg in reversed(self.messages):
            mk = msg.get("reply_markup")
            if mk and "inline_keyboard" in mk:
                return msg.get("message_id")
        return None

    def has_button(self, label: str) -> bool:
        return self.button_data(label) is not None

    def __repr__(self) -> str:
        preview = self.text[:80].replace("\n", "↵")
        btns = [b["text"] for b in self.buttons]
        return f"BotResponse({preview!r}, buttons={btns})"


class BotTestClient:
    """
    HTTP client for the fake Telegram mock server.

    Each method clears previous responses, injects an update, then
    polls until the bot replies (or times out).
    """

    def __init__(self, base_url: str, user_id: int, default_timeout: float = 25.0):
        self.base_url = base_url.rstrip("/")
        self.user_id = user_id
        self.default_timeout = default_timeout
        self._session: aiohttp.ClientSession | None = None

    async def start(self):
        self._session = aiohttp.ClientSession()

    async def stop(self):
        if self._session:
            await self._session.close()

    # ── public API ────────────────────────────────────────────────────────────

    async def send(self, text: str, timeout: float | None = None) -> BotResponse:
        """Send a text message. Returns bot response."""
        await self._clear()
        async with self._session.post(
            f"{self.base_url}/test/send",
            json={"text": text, "user_id": self.user_id},
        ) as r:
            r.raise_for_status()
            data = await r.json()
        after_seq = data.get("after_seq", 0)
        return await self._wait(timeout or self.default_timeout, after_seq=after_seq)

    async def tap(
        self,
        label_or_data: str,
        prev: BotResponse | None = None,
        timeout: float | None = None,
    ) -> BotResponse:
        """
        Click an inline button.

        `label_or_data`: button label (partial match) OR raw callback_data.
        `prev`: the BotResponse that contained the keyboard.
               If omitted, searches the server's current responses for the button.
        """
        if prev is not None:
            data = prev.button_data(label_or_data)
            if data is None:
                raise ValueError(
                    f"Button {label_or_data!r} not found in keyboard. "
                    f"Available: {[b['text'] for b in prev.buttons]}"
                )
            msg_id = prev.message_id_with_keyboard() or 1
        else:
            # Search server's stored responses for the button
            data, msg_id = await self._find_button(label_or_data)

        await self._clear()
        async with self._session.post(
            f"{self.base_url}/test/callback",
            json={"data": data, "user_id": self.user_id, "message_id": msg_id},
        ) as r:
            r.raise_for_status()
            resp = await r.json()
        after_seq = resp.get("after_seq", 0)
        return await self._wait(timeout or self.default_timeout, after_seq=after_seq)

    async def tap_silent(
        self,
        label_or_data: str,
        prev: BotResponse | None = None,
    ) -> None:
        """
        Inject a button callback without waiting for a bot reply.
        Used for actions that only edit existing messages (e.g. toggling selection).
        """
        if prev is not None:
            data = prev.button_data(label_or_data)
            if data is None:
                data = label_or_data
            msg_id = prev.message_id_with_keyboard() or 1
        else:
            data, msg_id = await self._find_button(label_or_data)

        async with self._session.post(
            f"{self.base_url}/test/callback",
            json={"data": data, "user_id": self.user_id, "message_id": msg_id},
        ) as r:
            r.raise_for_status()

    async def clear(self):
        """Manually clear captured responses and events."""
        await self._clear()

    async def responses(self) -> list[dict]:
        """Return all currently captured responses (without clearing)."""
        return await self._get_responses()

    async def events(self, type: str | None = None) -> list[dict]:
        """
        Return custom events posted by the bot for this user.

        client.events()                   → all events
        client.events(type="tool_call")   → only tool calls
        """
        params: dict = {"user_id": self.user_id}
        if type is not None:
            params["type"] = type
        async with self._session.get(
            f"{self.base_url}/test/events", params=params
        ) as r:
            return await r.json()

    async def reset(self) -> None:
        """
        Full user reset: clears server-side responses + events + seq counters,
        then triggers the bot's registered reset hook (if any).
        """
        async with self._session.post(
            f"{self.base_url}/test/reset-user",
            params={"user_id": self.user_id},
        ) as r:
            r.raise_for_status()

    async def get_tool_calls(self) -> list[dict]:
        """Compatibility shim — use client.events(type='tool_call') instead."""
        raw = await self.events(type="tool_call")
        return [e["data"] for e in raw]

    # ── internals ─────────────────────────────────────────────────────────────

    async def _find_button(self, label: str) -> tuple[str, int]:
        """Search server's stored responses for a button by label (partial match).
        Returns (callback_data, message_id)."""
        messages = await self._get_responses()
        for msg in reversed(messages):
            kb = msg.get("reply_markup")
            if kb and "inline_keyboard" in kb:
                for row in kb["inline_keyboard"]:
                    for btn in row:
                        if label.lower() in btn["text"].lower():
                            return btn["callback_data"], msg.get("message_id", 1)
        all_buttons = [
            btn["text"] for msg in messages
            for row in (msg.get("reply_markup") or {}).get("inline_keyboard", [])
            for btn in row
        ]
        raise ValueError(
            f"Button {label!r} not found in responses. Available: {all_buttons}"
        )

    async def _clear(self):
        async with self._session.delete(
            f"{self.base_url}/test/responses", params={"user_id": self.user_id}
        ) as r:
            r.raise_for_status()

    async def _get_responses(self) -> list[dict]:
        async with self._session.get(
            f"{self.base_url}/test/responses", params={"user_id": self.user_id}
        ) as r:
            return await r.json()

    async def _wait(self, timeout: float, after_seq: int = 0) -> BotResponse:
        """Block on the server's wait-response endpoint (event-driven, no polling)."""
        async with self._session.get(
            f"{self.base_url}/test/wait-response",
            params={"user_id": self.user_id, "after_seq": after_seq, "timeout": timeout},
        ) as r:
            result = await r.json()
        if not result.get("ok"):
            raise TimeoutError(
                f"Bot did not respond within {timeout}s (reason: {result.get('reason')})"
            )
        return BotResponse(await self._get_responses())
