"""Layered configuration: command-line flags, then environment, then a YAML
file, then built-in defaults.

The file lives at $XDG_CONFIG_HOME/s33.yml (falling back to ~/.config/s33.yml)
and looks like:

    modem:
      host: 192.168.0.1
      username: admin
      password: secret
    db:
      host: localhost
      port: 5432
      user: postgres
      password: secret
      name: s33mon
    monitor:
      interval: 300

Every key is optional. Because it can hold passwords, load_file warns when the
file is readable by anyone but its owner.
"""

import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

CONFIG_FILENAME = "s33.yml"
CONFIG_PATH_ENV = "S33_CONFIG"

DEFAULT_MODEM_HOST = "192.168.0.1"
DEFAULT_MODEM_USERNAME = "admin"
DEFAULT_DB_HOST = "localhost"
DEFAULT_DB_PORT = 5432
DEFAULT_DB_USER = "postgres"
DEFAULT_DB_NAME = "s33mon"
DEFAULT_INTERVAL = 300


class ConfigError(Exception):
    """Raised when a config file is unreadable, malformed, or has bad values."""


def _missing_password(label: str, flag: str, env: str, key: str) -> ConfigError:
    return ConfigError(
        f"no {label} password: pass {flag}, set {env}, or add {key} to your config file"
    )


@dataclass(frozen=True)
class ModemConfig:
    host: str = DEFAULT_MODEM_HOST
    username: str = DEFAULT_MODEM_USERNAME
    password: str | None = None

    def require_password(self) -> str:
        if not self.password:
            raise _missing_password(
                "modem", "--password", "S33_MODEM_PASSWORD", "modem.password"
            )
        return self.password


@dataclass(frozen=True)
class DBConfig:
    host: str = DEFAULT_DB_HOST
    port: int = DEFAULT_DB_PORT
    user: str = DEFAULT_DB_USER
    password: str | None = None
    name: str = DEFAULT_DB_NAME

    def require_password(self) -> str:
        if not self.password:
            raise _missing_password(
                "database", "--db-password", "S33_DB_PASSWORD", "db.password"
            )
        return self.password


@dataclass(frozen=True)
class MonitorConfig:
    interval: int = DEFAULT_INTERVAL


@dataclass(frozen=True)
class Config:
    modem: ModemConfig = field(default_factory=ModemConfig)
    db: DBConfig = field(default_factory=DBConfig)
    monitor: MonitorConfig = field(default_factory=MonitorConfig)


def default_config_path(env: Mapping[str, str] | None = None) -> Path:
    """Where we look for the config file when none is given explicitly."""
    env = os.environ if env is None else env
    if explicit := env.get(CONFIG_PATH_ENV):
        return Path(explicit)
    base = env.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / CONFIG_FILENAME


def insecure_permissions(path: Path) -> bool:
    """True if the file is readable by group or other. Windows always reports False."""
    if os.name != "posix":
        return False
    mode = path.stat().st_mode
    return bool(mode & (stat.S_IRWXG | stat.S_IRWXO))


def load_file(path: Path, required: bool = False) -> dict[str, Any]:
    """Read the YAML config file. Returns {} when an optional file is absent."""
    if not path.exists():
        if required:
            raise ConfigError(f"config file not found: {path}")
        return {}

    try:
        raw = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as e:
        raise ConfigError(f"could not read config file {path}: {e}") from e

    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(f"config file {path} must contain a mapping at the top level")
    return raw


def _first(*values: Any, default: Any) -> Any:
    """First value that was actually supplied, else the default."""
    for value in values:
        if value is not None:
            return value
    return default


def _section(data: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    section = data.get(name)
    if section is None:
        return {}
    if not isinstance(section, Mapping):
        raise ConfigError(f"config section {name!r} must be a mapping")
    return section


def _as_int(value: Any, label: str) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as e:
        raise ConfigError(f"{label} must be an integer, got {value!r}") from e


def build_config(
    file_data: Mapping[str, Any] | None = None,
    env: Mapping[str, str] | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> Config:
    """Merge the three sources into a Config. Pure: nothing is read from disk here.

    `overrides` holds command-line values keyed like modem_host, db_port,
    interval; a None value means the flag was not given.
    """
    file_data = file_data or {}
    env = os.environ if env is None else env
    overrides = overrides or {}

    modem_file = _section(file_data, "modem")
    db_file = _section(file_data, "db")
    monitor_file = _section(file_data, "monitor")

    modem = ModemConfig(
        host=_first(
            overrides.get("modem_host"),
            env.get("S33_MODEM_HOST"),
            modem_file.get("host"),
            default=DEFAULT_MODEM_HOST,
        ),
        username=_first(
            overrides.get("modem_username"),
            env.get("S33_MODEM_USERNAME"),
            modem_file.get("username"),
            default=DEFAULT_MODEM_USERNAME,
        ),
        password=_first(
            overrides.get("modem_password"),
            env.get("S33_MODEM_PASSWORD"),
            modem_file.get("password"),
            default=None,
        ),
    )

    db = DBConfig(
        host=_first(
            overrides.get("db_host"),
            env.get("S33_DB_HOST"),
            db_file.get("host"),
            default=DEFAULT_DB_HOST,
        ),
        port=_first(
            _as_int(overrides.get("db_port"), "--db-port"),
            _as_int(env.get("S33_DB_PORT"), "S33_DB_PORT"),
            _as_int(db_file.get("port"), "db.port"),
            default=DEFAULT_DB_PORT,
        ),
        user=_first(
            overrides.get("db_user"),
            env.get("S33_DB_USER"),
            db_file.get("user"),
            default=DEFAULT_DB_USER,
        ),
        password=_first(
            overrides.get("db_password"),
            env.get("S33_DB_PASSWORD"),
            db_file.get("password"),
            default=None,
        ),
        name=_first(
            overrides.get("db_name"),
            env.get("S33_DB_NAME"),
            db_file.get("name"),
            default=DEFAULT_DB_NAME,
        ),
    )

    monitor = MonitorConfig(
        interval=_first(
            _as_int(overrides.get("interval"), "--interval"),
            _as_int(env.get("S33_INTERVAL"), "S33_INTERVAL"),
            _as_int(monitor_file.get("interval"), "monitor.interval"),
            default=DEFAULT_INTERVAL,
        )
    )

    return Config(modem=modem, db=db, monitor=monitor)
