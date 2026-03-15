"""
Auto-patch: redirect Telegram Bot API calls to the mock server without code changes.

When enabled, tgmock writes a temporary ``sitecustomize.py`` that monkey-patches
popular HTTP clients (aiohttp, httpx) so any request to ``api.telegram.org`` is
transparently rerouted to the local mock server.

The temp directory is prepended to PYTHONPATH *before* the bot process starts,
so patches load before the bot imports anything.  No tgmock dependency is needed
in the bot's own virtualenv — the generated file is fully self-contained.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

# Self-contained Python source injected via sitecustomize.py.
# Placeholders {mock_base} and {mock_host}:{mock_port} are filled at generation time.
_SITECUSTOMIZE_TEMPLATE = '''\
"""tgmock autopatch — redirects api.telegram.org to the local mock server."""
import importlib
import sys

_TELEGRAM_HOST = "api.telegram.org"
_MOCK_BASE = "{mock_base}"
_MOCK_HOST = "{mock_host}"
_MOCK_PORT = {mock_port}


# ── aiohttp (used by aiogram) ───────────────────────────────────────────────

def _patch_aiohttp():
    import aiohttp
    from yarl import URL as _URL

    _orig = aiohttp.ClientSession._request

    async def _patched(self, method, url, **kw):
        url = _URL(str(url))
        if url.host == _TELEGRAM_HOST:
            url = url.with_scheme("http").with_host(_MOCK_HOST).with_port(_MOCK_PORT)
            kw.pop("ssl", None)
        return await _orig(self, method, url, **kw)

    aiohttp.ClientSession._request = _patched


# ── httpx (used by python-telegram-bot v20+) ─────────────────────────────────

def _patch_httpx():
    import httpx

    _orig_async = httpx.AsyncClient.send
    _orig_sync = httpx.Client.send

    def _rewrite(request):
        if request.url.host == _TELEGRAM_HOST:
            raw = str(request.url)
            raw = raw.replace("https://" + _TELEGRAM_HOST, _MOCK_BASE, 1)
            raw = raw.replace("http://" + _TELEGRAM_HOST, _MOCK_BASE, 1)
            request.url = httpx.URL(raw)
            request.headers["host"] = _TELEGRAM_HOST  # keep Host header

    async def _async_send(self, request, **kw):
        _rewrite(request)
        return await _orig_async(self, request, **kw)

    def _sync_send(self, request, **kw):
        _rewrite(request)
        return _orig_sync(self, request, **kw)

    httpx.AsyncClient.send = _async_send
    httpx.Client.send = _sync_send


# ── apply patches ────────────────────────────────────────────────────────────

for _name, _patcher in [("aiohttp", _patch_aiohttp), ("httpx", _patch_httpx)]:
    try:
        _patcher()
    except ImportError:
        pass
    except Exception as _exc:
        import sys as _sys
        print(f"[tgmock] warning: failed to patch {{_name}}: {{_exc}}", file=_sys.stderr)
'''


def prepare_autopatch(mock_base_url: str) -> tuple[str, dict[str, str]]:
    """
    Write a temporary ``sitecustomize.py`` and return env overrides.

    Returns:
        (tmpdir, env_patch)  where *env_patch* contains the PYTHONPATH addition.
        The caller should merge *env_patch* into the subprocess environment.
        The tmpdir is cleaned up automatically when the process exits.
    """
    from urllib.parse import urlparse

    parsed = urlparse(mock_base_url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 8999

    code = _SITECUSTOMIZE_TEMPLATE.format(
        mock_base=mock_base_url.rstrip("/"),
        mock_host=host,
        mock_port=port,
    )

    tmpdir = tempfile.mkdtemp(prefix="tgmock_patch_")
    site_file = Path(tmpdir) / "sitecustomize.py"
    site_file.write_text(code)

    # Prepend to PYTHONPATH so sitecustomize.py is found first.
    existing = os.environ.get("PYTHONPATH", "")
    new_path = f"{tmpdir}:{existing}" if existing else tmpdir

    return tmpdir, {"PYTHONPATH": new_path}


def is_python_command(cmd: str) -> bool:
    """Heuristic: does the bot command start a Python interpreter?"""
    first = cmd.strip().split()[0] if cmd.strip() else ""
    return first in ("python", "python3", "python3.11", "python3.12", "python3.13")
