from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TgmockConfig:
    bot_command: str = "python main.py"
    port: int = 8999
    token: str = "test:token"
    settle_ms: int = 400
    ready_log: str = "bot starting"
    startup_timeout: float = 15.0
    default_timeout: float = 25.0
    env: dict[str, str] = field(default_factory=dict)


def load_config(rootdir: Path) -> TgmockConfig:
    """Load [tool.tgmock] section from pyproject.toml. Returns defaults if not found."""
    pyproject = rootdir / "pyproject.toml"
    if not pyproject.exists():
        return TgmockConfig()
    with open(pyproject, "rb") as f:
        data = tomllib.load(f)
    raw: dict = data.get("tool", {}).get("tgmock", {})
    env = raw.pop("env", {})
    known = {k: v for k, v in raw.items() if k in TgmockConfig.__dataclass_fields__}
    return TgmockConfig(**known, env=env)
