"""Command line interface for the Arris S33.

`s33 stats` and `s33 reboot` talk to the modem; `s33 monitor` runs the polling
service and needs the `monitor` extra installed. The `s33mon` console script is
an alias for `s33 monitor`.
"""

import json
import logging
import signal
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

import click

from s33mon import config as config_mod
from s33mon.config import Config, ConfigError
from s33mon.hnap import HNAPClient, RebootOptions
from s33mon.models import HNAPAction, get_all_hnap_actions

CONFIG_HELP = (
    f"config file (default: $XDG_CONFIG_HOME/{config_mod.CONFIG_FILENAME}, "
    f"override with ${config_mod.CONFIG_PATH_ENV})"
)


def config_option(f: click.decorators.FC) -> click.decorators.FC:
    return click.option(
        "-c",
        "--config",
        "config_path",
        type=click.Path(path_type=Path),
        default=None,
        help=CONFIG_HELP,
    )(f)


def modem_options(f: click.decorators.FC) -> click.decorators.FC:
    f = click.option(
        "-u",
        "--username",
        "--modem-username",
        "modem_username",
        default=None,
        help=f"modem username (env: S33_MODEM_USERNAME, default: {config_mod.DEFAULT_MODEM_USERNAME})",
    )(f)
    f = click.option(
        "-p",
        "--password",
        "--modem-password",
        "modem_password",
        default=None,
        help="modem password or login key (env: S33_MODEM_PASSWORD)",
    )(f)
    f = click.option(
        "--host",
        "--modem-host",
        "modem_host",
        default=None,
        help=f"modem host or IP (env: S33_MODEM_HOST, default: {config_mod.DEFAULT_MODEM_HOST})",
    )(f)
    return config_option(f)


def db_options(f: click.decorators.FC) -> click.decorators.FC:
    f = click.option(
        "--db-name",
        default=None,
        help=f"database name (env: S33_DB_NAME, default: {config_mod.DEFAULT_DB_NAME})",
    )(f)
    f = click.option(
        "--db-password",
        default=None,
        help="database password (env: S33_DB_PASSWORD)",
    )(f)
    f = click.option(
        "--db-user",
        default=None,
        help=f"database user (env: S33_DB_USER, default: {config_mod.DEFAULT_DB_USER})",
    )(f)
    f = click.option(
        "--db-port",
        type=int,
        default=None,
        help=f"database port (env: S33_DB_PORT, default: {config_mod.DEFAULT_DB_PORT})",
    )(f)
    f = click.option(
        "--db-host",
        default=None,
        help=f"database host (env: S33_DB_HOST, default: {config_mod.DEFAULT_DB_HOST})",
    )(f)
    return f


@contextmanager
def usage_errors() -> Iterator[None]:
    """Report bad or incomplete configuration the way click reports bad flags."""
    try:
        yield
    except ConfigError as e:
        raise click.UsageError(str(e)) from e


def resolve_config(config_path: Path | None, **overrides) -> Config:
    """Layer flags over environment over config file. A config file that was
    named explicitly must exist; the default one is optional."""
    path = config_path or config_mod.default_config_path()
    with usage_errors():
        file_data = config_mod.load_file(path, required=config_path is not None)
        if file_data and config_mod.insecure_permissions(path):
            click.echo(
                f"Warning: {path} is readable by other users and it may hold passwords. "
                f"Consider: chmod 600 {path}",
                err=True,
            )
        return config_mod.build_config(file_data=file_data, overrides=overrides)


def login(cfg: Config) -> HNAPClient:
    with usage_errors():
        password = cfg.modem.require_password()
    client = HNAPClient(cfg.modem.host, cfg.modem.username, password)
    click.echo(
        f"Logging in to s33 modem {cfg.modem.username}:{'*' * len(password)} @ {cfg.modem.host}...",
        err=True,
        nl=False,
    )
    client.login()
    click.echo("OK", err=True)
    return client


@click.group()
@click.version_option(package_name="s33-modem-mon")
def main() -> None:
    """Arris S33 modem CLI."""


@main.command("stats")
@modem_options
@click.argument("actions", nargs=-1, metavar="ACTION")
def stats_cmd(config_path: Path | None, actions: tuple[str, ...], **overrides) -> None:
    """Fetch modem statistics. ACTION may be 'all' or a specific action name (default: all)."""
    all_actions = get_all_hnap_actions()

    if not actions or any(a.lower() == "all" for a in actions):
        selected = all_actions
    else:
        unknown = [a for a in actions if a not in set(all_actions)]
        if unknown:
            raise click.UsageError(
                "unknown action(s): "
                + ", ".join(unknown)
                + "\nvalid actions: all, "
                + ", ".join(all_actions)
            )
        selected = [HNAPAction(a) for a in actions]

    cfg = resolve_config(config_path, **overrides)
    with login(cfg) as client:
        click.echo(
            f"Fetching stats for actions: {', '.join(selected)}...", err=True, nl=False
        )
        result = client.get_stats(selected)
        click.echo("OK", err=True)

    click.echo(json.dumps({k: asdict(v) for k, v in result.items()}, indent=2))  # ty: ignore[invalid-argument-type]


@main.command("reboot")
@modem_options
@click.option(
    "--set-eee-enable/--no-set-eee-enable",
    default=False,
    show_default=True,
    help="enable Energy Efficient Ethernet",
)
@click.option(
    "--led-status/--no-led-status",
    default=True,
    show_default=True,
    help="LED status after reboot",
)
def reboot_cmd(
    config_path: Path | None,
    set_eee_enable: bool,
    led_status: bool,
    **overrides,
) -> None:
    """Reboot the modem."""
    cfg = resolve_config(config_path, **overrides)
    with login(cfg) as client:
        click.echo("Sending reboot command...", err=True, nl=False)
        client.reboot(
            RebootOptions(set_eee_enable=set_eee_enable, led_status=led_status)
        )
        click.echo("OK", err=True)


@main.command("monitor")
@modem_options
@db_options
@click.option(
    "--interval",
    type=int,
    default=None,
    metavar="SECONDS",
    help=f"poll interval in seconds (env: S33_INTERVAL, default: {config_mod.DEFAULT_INTERVAL})",
)
def monitor_cmd(config_path: Path | None, **overrides) -> None:
    """Poll the modem on an interval and store results in TimescaleDB."""
    cfg = resolve_config(config_path, **overrides)
    with usage_errors():
        cfg.modem.require_password()
        cfg.db.require_password()

    try:
        from s33mon import monitor
    except ImportError as e:
        raise click.UsageError(
            f"monitoring support is not installed ({e}). "
            "Install it with: pip install 's33-modem-mon[monitor]'"
        ) from e

    # Python as PID 1 in a container ignores SIGTERM by default.
    # Restore the default so docker stop / docker-compose down work promptly.
    def handle_sigterm(*_):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, handle_sigterm)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    monitor.run(cfg)


if __name__ == "__main__":
    main()
