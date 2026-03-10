from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TgmockConfig:
    bot_command: str = "python main.py"
    port: int = 8999
    settle_ms: int = 400
    ready_log: str = "bot starting"
    startup_timeout: float = 15.0
    default_timeout: float = 25.0
    env_file: str = ".env"
    build_command: str = ""  # run before bot start (e.g. "go build -o /tmp/bot ./cmd/server")
    env: dict[str, str] = field(default_factory=dict)


def load_config(rootdir: Path) -> TgmockConfig:
    """
    Load tgmock config from TGMOCK_* env vars, falling back to pyproject.toml.

    Priority (highest to lowest):
      1. TGMOCK_* environment variables (already in process env)
      2. TGMOCK_* keys inside the project's .env file
      3. [tool.tgmock] section in pyproject.toml
      4. Built-in defaults
    """
    cfg = TgmockConfig()

    # 3. pyproject.toml (lowest priority of the three sources)
    pyproject = rootdir / "pyproject.toml"
    if pyproject.exists():
        try:
            import tomllib
            with open(pyproject, "rb") as f:
                data = tomllib.load(f)
            raw: dict = data.get("tool", {}).get("tgmock", {})
            for key in ("bot_command", "port", "settle_ms", "ready_log",
                        "startup_timeout", "default_timeout", "env_file"):
                if key in raw:
                    setattr(cfg, key, raw[key])
            cfg.env.update(raw.get("env", {}))
        except Exception:
            pass

    # 2. TGMOCK_* keys in the .env file
    env_file = rootdir / cfg.env_file
    if env_file.exists():
        try:
            import dotenv
            file_vars = dotenv.dotenv_values(env_file)
            _apply_tgmock_vars(cfg, file_vars)
        except Exception:
            pass

    # 1. TGMOCK_* from process environment (highest priority)
    _apply_tgmock_vars(cfg, os.environ)

    return cfg


def _apply_tgmock_vars(cfg: TgmockConfig, mapping: dict) -> None:
    """Apply TGMOCK_* keys from a dict onto cfg."""
    str_keys = {"bot_command", "ready_log", "env_file", "build_command"}
    int_keys = {"port", "settle_ms"}
    float_keys = {"startup_timeout", "default_timeout"}
    for key in str_keys | int_keys | float_keys:
        val = mapping.get(f"TGMOCK_{key.upper()}")
        if val is not None:
            if key in int_keys:
                setattr(cfg, key, int(val))
            elif key in float_keys:
                setattr(cfg, key, float(val))
            else:
                setattr(cfg, key, val)
