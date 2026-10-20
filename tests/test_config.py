"""Precedence is the whole point of the config layer: flag, then environment,
then file, then default."""

import pytest

from s33mon.config import (
    DEFAULT_DB_PORT,
    DEFAULT_INTERVAL,
    DEFAULT_MODEM_HOST,
    ConfigError,
    build_config,
    default_config_path,
    insecure_permissions,
    load_file,
)

FILE_DATA = {
    "modem": {"host": "10.0.0.1", "username": "fileuser", "password": "filepass"},
    "db": {"host": "filedb", "port": 6543, "name": "filename"},
    "monitor": {"interval": 600},
}


def test_defaults_when_nothing_is_set() -> None:
    cfg = build_config(file_data={}, env={}, overrides={})
    assert cfg.modem.host == DEFAULT_MODEM_HOST
    assert cfg.modem.password is None
    assert cfg.db.port == DEFAULT_DB_PORT
    assert cfg.monitor.interval == DEFAULT_INTERVAL == 300


def test_file_beats_defaults() -> None:
    cfg = build_config(file_data=FILE_DATA, env={}, overrides={})
    assert cfg.modem.host == "10.0.0.1"
    assert cfg.modem.password == "filepass"
    assert cfg.db.port == 6543
    assert cfg.monitor.interval == 600
    # not in the file, so still the default
    assert cfg.db.user == "postgres"


def test_env_beats_file() -> None:
    cfg = build_config(
        file_data=FILE_DATA,
        env={"S33_MODEM_HOST": "envhost", "S33_INTERVAL": "60"},
        overrides={},
    )
    assert cfg.modem.host == "envhost"
    assert cfg.monitor.interval == 60
    assert cfg.modem.username == "fileuser"


def test_override_beats_env_and_file() -> None:
    cfg = build_config(
        file_data=FILE_DATA,
        env={"S33_MODEM_HOST": "envhost", "S33_DB_PASSWORD": "envpass"},
        overrides={"modem_host": "flaghost", "db_password": "flagpass"},
    )
    assert cfg.modem.host == "flaghost"
    assert cfg.db.password == "flagpass"


def test_unset_overrides_are_ignored() -> None:
    cfg = build_config(
        file_data=FILE_DATA,
        env={},
        overrides={"modem_host": None, "db_port": None, "interval": None},
    )
    assert cfg.modem.host == "10.0.0.1"
    assert cfg.db.port == 6543
    assert cfg.monitor.interval == 600


def test_numeric_strings_are_coerced() -> None:
    cfg = build_config(file_data={"db": {"port": "5555"}}, env={}, overrides={})
    assert cfg.db.port == 5555


def test_non_numeric_port_is_rejected() -> None:
    with pytest.raises(ConfigError, match="db.port"):
        build_config(file_data={"db": {"port": "not-a-port"}}, env={}, overrides={})


def test_non_mapping_section_is_rejected() -> None:
    with pytest.raises(ConfigError, match="'modem'"):
        build_config(file_data={"modem": "192.168.0.1"}, env={}, overrides={})


def test_default_path_prefers_explicit_env(tmp_path) -> None:
    path = default_config_path({"S33_CONFIG": str(tmp_path / "custom.yml")})
    assert path == tmp_path / "custom.yml"


def test_default_path_uses_xdg_config_home(tmp_path) -> None:
    assert (
        default_config_path({"XDG_CONFIG_HOME": str(tmp_path)}) == tmp_path / "s33.yml"
    )


def test_default_path_falls_back_to_dot_config() -> None:
    assert default_config_path({}).parts[-2:] == (".config", "s33.yml")


def test_load_file_reads_yaml(tmp_path) -> None:
    path = tmp_path / "s33.yml"
    path.write_text("modem:\n  host: 10.0.0.1\n  password: hunter2\n")
    assert load_file(path) == {"modem": {"host": "10.0.0.1", "password": "hunter2"}}


def test_load_file_tolerates_a_missing_optional_file(tmp_path) -> None:
    assert load_file(tmp_path / "nope.yml") == {}


def test_load_file_raises_when_required_and_missing(tmp_path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_file(tmp_path / "nope.yml", required=True)


def test_load_file_treats_empty_as_no_config(tmp_path) -> None:
    path = tmp_path / "s33.yml"
    path.write_text("")
    assert load_file(path) == {}


def test_load_file_rejects_a_non_mapping_document(tmp_path) -> None:
    path = tmp_path / "s33.yml"
    path.write_text("- one\n- two\n")
    with pytest.raises(ConfigError, match="mapping"):
        load_file(path)


def test_load_file_rejects_malformed_yaml(tmp_path) -> None:
    path = tmp_path / "s33.yml"
    path.write_text("modem: [unclosed\n")
    with pytest.raises(ConfigError, match="could not read"):
        load_file(path)


def test_insecure_permissions_detects_group_readable(tmp_path) -> None:
    path = tmp_path / "s33.yml"
    path.write_text("modem: {}\n")
    path.chmod(0o600)
    assert insecure_permissions(path) is False
    path.chmod(0o640)
    assert insecure_permissions(path) is True
