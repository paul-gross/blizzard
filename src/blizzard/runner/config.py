"""Runner runtime configuration — resolved from a runtime directory.

``blizzard runner init <dir>`` scaffolds a config file and a data directory; the
daemon and the offline ``migrate`` verb read it back. The store URL is the single
portability knob (``bzh:sql-portable``): sqlite (WAL, in-process) is the runner's
embedded default (D-023/D-028). The bind port falls back to the winter service
band's ``BZ_RUNNER_PORT`` (band +3).
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

CONFIG_FILENAME = "blizzard-runner.toml"
DATA_DIRNAME = "data"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8431

ENV_HOST = "BZ_RUNNER_HOST"
ENV_PORT = "BZ_RUNNER_PORT"


class ConfigError(RuntimeError):
    """A runtime directory is missing its config — it was never initialized."""


@dataclass(frozen=True)
class RunnerConfig:
    """Resolved runner runtime configuration."""

    root: Path
    db_url: str
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT

    @property
    def config_path(self) -> Path:
        return self.root / CONFIG_FILENAME

    @property
    def data_dir(self) -> Path:
        return self.root / DATA_DIRNAME

    @staticmethod
    def default_db_url(root: Path) -> str:
        return f"sqlite:///{(root / DATA_DIRNAME / 'runner.db').resolve()}"

    @classmethod
    def scaffold(cls, root: Path) -> RunnerConfig:
        """The default config for a fresh runtime root (used by ``init``)."""
        return cls(
            root=root,
            db_url=cls.default_db_url(root),
            host=os.environ.get(ENV_HOST, DEFAULT_HOST),
            port=int(os.environ.get(ENV_PORT, DEFAULT_PORT)),
        )

    def to_toml(self) -> str:
        return (
            "# blizzard-runner runtime configuration (blizzard runner init)\n"
            f'db_url = "{self.db_url}"\n'
            f'host = "{self.host}"\n'
            f"port = {self.port}\n"
        )

    @classmethod
    def load(cls, root: Path, *, host: str | None = None, port: int | None = None) -> RunnerConfig:
        """Read a runtime root's config file; overlay CLI host/port when given."""
        root = root.resolve()
        path = root / CONFIG_FILENAME
        if not path.exists():
            raise ConfigError(f"{root} is not an initialized runner runtime (run `blizzard runner init {root}`)")
        raw = tomllib.loads(path.read_text())
        return cls(
            root=root,
            db_url=str(raw["db_url"]),
            host=host or str(raw.get("host", DEFAULT_HOST)),
            port=port if port is not None else int(raw.get("port", DEFAULT_PORT)),
        )
