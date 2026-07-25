from click.testing import CliRunner

from s33mon.cli import main
from s33mon.monitor import MIN_RETRY_SECONDS, retry_delay


def run(*args, env=None):
    return CliRunner().invoke(main, args, env=env or {})


def test_stats_rejects_unknown_actions(tmp_path) -> None:
    result = run("stats", "GetNonsense", "--config", str(tmp_path / "none.yml"))
    assert result.exit_code != 0
    assert "unknown action(s): GetNonsense" in result.output


def test_missing_password_is_a_usage_error(tmp_path) -> None:
    config = tmp_path / "s33.yml"
    config.write_text("modem:\n  host: 10.0.0.1\n")
    result = run("stats", "--config", str(config))
    assert result.exit_code != 0
    assert "no modem password" in result.output


def test_explicit_missing_config_file_is_an_error(tmp_path) -> None:
    result = run("stats", "--config", str(tmp_path / "absent.yml"))
    assert result.exit_code != 0
    assert "config file not found" in result.output


def test_monitor_requires_a_database_password(tmp_path) -> None:
    config = tmp_path / "s33.yml"
    config.write_text("modem:\n  password: hunter2\n")
    result = run("monitor", "--config", str(config))
    assert result.exit_code != 0
    assert "no database password" in result.output


def test_commands_are_registered() -> None:
    result = run("--help")
    assert result.exit_code == 0
    for command in ("stats", "reboot", "monitor"):
        assert command in result.output


def test_retry_delay_backs_off_less_after_failure() -> None:
    assert retry_delay(900, failed=False) == 900
    assert retry_delay(900, failed=True) == MIN_RETRY_SECONDS
    # a poll interval shorter than the cap is already fast enough
    assert retry_delay(30, failed=True) == 30
