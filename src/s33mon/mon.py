import logging
import os
import signal
import time
import argparse
from dataclasses import dataclass
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras

from s33mon.s33 import HNAPClient, ModemStats, get_all_hnap_actions

logger = logging.getLogger(__name__)


@dataclass
class DBConfig:
    host: str
    port: int
    user: str
    password: str
    dbname: str


@dataclass
class ModemConfig:
    host: str
    username: str
    password: str


# Tables are created in dependency order.
# No FK constraints to pull because TimescaleDB hypertables can't be FK targets.
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS pull (
    id      BIGSERIAL,
    pulled_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (id, pulled_at)
);

CREATE TABLE IF NOT EXISTS customer_status_startup_sequence (
    pull_id                     BIGINT NOT NULL PRIMARY KEY,
    downstream_frequency_hertz  BIGINT NOT NULL,
    downstream_comment          TEXT   NOT NULL,
    connectivity_status         TEXT   NOT NULL,
    connectivity_comment        TEXT   NOT NULL,
    boot_status                 TEXT   NOT NULL,
    boot_comment                TEXT   NOT NULL,
    config_file_status          TEXT   NOT NULL,
    config_file_comment         TEXT   NOT NULL,
    security_status             TEXT   NOT NULL,
    security_comment            TEXT   NOT NULL
);

CREATE TABLE IF NOT EXISTS customer_status_connection_info (
    pull_id               BIGINT NOT NULL PRIMARY KEY,
    system_uptime_seconds BIGINT NOT NULL,
    system_time_unix      BIGINT NOT NULL,
    network_access        TEXT   NOT NULL
);

CREATE TABLE IF NOT EXISTS downstream_channel (
    pulled_at          TIMESTAMPTZ      NOT NULL,
    pull_id            BIGINT           NOT NULL,
    channel_id         INT              NOT NULL,
    lock_status        TEXT             NOT NULL,
    modulation         TEXT             NOT NULL,
    frequency_hz       BIGINT           NOT NULL,
    power_dbmv         DOUBLE PRECISION NOT NULL,
    snr_db             DOUBLE PRECISION NOT NULL,
    corrected_count    BIGINT           NOT NULL,
    uncorrectable_count BIGINT          NOT NULL,
    PRIMARY KEY (pulled_at, pull_id, channel_id)
);

CREATE TABLE IF NOT EXISTS upstream_channel (
    pulled_at    TIMESTAMPTZ      NOT NULL,
    pull_id      BIGINT           NOT NULL,
    channel_id   INT              NOT NULL,
    lock_status  TEXT             NOT NULL,
    channel_type TEXT             NOT NULL,
    width_hz     BIGINT           NOT NULL,
    frequency_hz BIGINT           NOT NULL,
    power_dbmv   DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (pulled_at, pull_id, channel_id)
);

-- No pull_id. Deduplicated by (timestamp_at, level, message).
-- Becomes a hypertable; unique index created after hypertable conversion.
CREATE TABLE IF NOT EXISTS log_entry (
    timestamp_at TIMESTAMPTZ NOT NULL,
    level        INT         NOT NULL,
    message      TEXT        NOT NULL
);

CREATE TABLE IF NOT EXISTS customer_status_software (
    pull_id             BIGINT NOT NULL PRIMARY KEY,
    spec_version        TEXT   NOT NULL,
    hard_version        TEXT   NOT NULL,
    soft_version        TEXT   NOT NULL,
    mac_address         TEXT   NOT NULL,
    serial_number       TEXT   NOT NULL,
    certificate_status  TEXT   NOT NULL,
    customer_version    TEXT   NOT NULL
);

CREATE TABLE IF NOT EXISTS arris_device_status (
    pull_id                       BIGINT           NOT NULL PRIMARY KEY,
    firmware_version              TEXT             NOT NULL,
    internet_connection_status    TEXT             NOT NULL,
    downstream_frequency_hz       BIGINT           NOT NULL,
    downstream_signal_power_dbmv  DOUBLE PRECISION NOT NULL,
    downstream_signal_snr_db      DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS arris_register_info (
    pull_id       BIGINT NOT NULL PRIMARY KEY,
    mac_address   TEXT   NOT NULL,
    serial_number TEXT   NOT NULL,
    model_name    TEXT   NOT NULL
);

CREATE TABLE IF NOT EXISTS arris_register_status (
    pull_id       BIGINT  NOT NULL PRIMARY KEY,
    ask_me_later  BOOLEAN NOT NULL,
    never_ask     BOOLEAN NOT NULL
);

CREATE TABLE IF NOT EXISTS arris_configuration_info (
    pull_id                   BIGINT  NOT NULL PRIMARY KEY,
    downstream_frequency_hz   BIGINT  NOT NULL,
    upstream_channel_id       INT     NOT NULL,
    downstream_plan           TEXT    NOT NULL,
    energy_efficient_ethernet BOOLEAN NOT NULL,
    led_status                BOOLEAN NOT NULL
);
"""


def connect(cfg: DBConfig):
    """Connect to the target database, creating it first if it doesn't exist."""
    try:
        return psycopg2.connect(
            host=cfg.host,
            port=cfg.port,
            user=cfg.user,
            password=cfg.password,
            dbname=cfg.dbname,
        )
    except psycopg2.OperationalError as e:
        if "does not exist" not in str(e):
            raise
        logger.info("Database %r not found — creating it.", cfg.dbname)
        # Must connect to a different DB to issue CREATE DATABASE
        bootstrap = psycopg2.connect(
            host=cfg.host,
            port=cfg.port,
            user=cfg.user,
            password=cfg.password,
            dbname="postgres",
        )
        bootstrap.autocommit = True
        with bootstrap.cursor() as cur:
            cur.execute(f"CREATE DATABASE {cfg.dbname};")
        bootstrap.close()
        logger.info("Database %r created.", cfg.dbname)
        return psycopg2.connect(
            host=cfg.host,
            port=cfg.port,
            user=cfg.user,
            password=cfg.password,
            dbname=cfg.dbname,
        )


def setup_schema(conn) -> None:
    """Create tables and TimescaleDB hypertables. Idempotent."""
    with conn.cursor() as cur:
        cur.execute(SCHEMA_SQL)

        # Hypertable for the primary time-series anchor
        cur.execute(
            "SELECT create_hypertable('pull', 'pulled_at', if_not_exists => TRUE);"
        )

        # Hypertable for log entries (queried independently by time)
        cur.execute(
            "SELECT create_hypertable('log_entry', 'timestamp_at', if_not_exists => TRUE);"
        )

        # Hypertables for channel signal data (highest-volume, most query-valuable tables)
        cur.execute(
            "SELECT create_hypertable('downstream_channel', 'pulled_at', if_not_exists => TRUE);"
        )
        cur.execute(
            "SELECT create_hypertable('upstream_channel', 'pulled_at', if_not_exists => TRUE);"
        )

        # Unique index for log deduplication. Must include partition column (timestamp_at).
        # ON CONFLICT in insert_pull references this index.
        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS log_entry_dedup
                ON log_entry (timestamp_at, level, message);
        """)

    conn.commit()
    logger.info("Schema is ready.")


def insert_pull(conn, stats: ModemStats) -> int:
    """Insert one pull and all associated stat rows. Returns the pull id."""
    with conn.cursor() as cur:
        cur.execute("INSERT INTO pull (pulled_at) VALUES (NOW()) RETURNING id, pulled_at;")
        pull_id, pulled_at = cur.fetchone()

        if ss := stats.get("customer_status_startup_sequence"):
            cur.execute(
                """
                INSERT INTO customer_status_startup_sequence (
                    pull_id, downstream_frequency_hertz, downstream_comment,
                    connectivity_status, connectivity_comment, boot_status,
                    boot_comment, config_file_status, config_file_comment,
                    security_status, security_comment
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    pull_id,
                    ss.downstream_frequency_hertz,
                    ss.downstream_comment,
                    ss.connectivity_status,
                    ss.connectivity_comment,
                    ss.boot_status,
                    ss.boot_comment,
                    ss.config_file_status,
                    ss.config_file_comment,
                    ss.security_status,
                    ss.security_comment,
                ),
            )

        if ci := stats.get("customer_status_connection_info"):
            cur.execute(
                """
                INSERT INTO customer_status_connection_info (
                    pull_id, system_uptime_seconds, system_time_unix, network_access
                ) VALUES (%s, %s, %s, %s)
                """,
                (
                    pull_id,
                    ci.system_uptime_seconds,
                    ci.system_time_unix,
                    ci.network_access,
                ),
            )

        if dci := stats.get("customer_status_downstream_channel_info"):
            if dci.channels:
                psycopg2.extras.execute_values(
                    cur,
                    """
                    INSERT INTO downstream_channel (
                        pulled_at, pull_id, channel_id, lock_status, modulation, frequency_hz,
                        power_dbmv, snr_db, corrected_count, uncorrectable_count
                    ) VALUES %s
                    """,
                    [
                        (
                            pulled_at,
                            pull_id,
                            ch.channel_id,
                            ch.lock_status,
                            ch.modulation,
                            ch.frequency_hz,
                            ch.power_dbmv,
                            ch.snr_db,
                            ch.corrected_count,
                            ch.uncorrectable_count,
                        )
                        for ch in dci.channels
                    ],
                )

        if uci := stats.get("customer_status_upstream_channel_info"):
            if uci.channels:
                psycopg2.extras.execute_values(
                    cur,
                    """
                    INSERT INTO upstream_channel (
                        pulled_at, pull_id, channel_id, lock_status, channel_type,
                        width_hz, frequency_hz, power_dbmv
                    ) VALUES %s
                    """,
                    [
                        (
                            pulled_at,
                            pull_id,
                            ch.channel_id,
                            ch.lock_status,
                            ch.channel_type,
                            ch.width_hz,
                            ch.frequency_hz,
                            ch.power_dbmv,
                        )
                        for ch in uci.channels
                    ],
                )

        if log := stats.get("customer_status_log"):
            if log.entries:
                psycopg2.extras.execute_values(
                    cur,
                    """
                    INSERT INTO log_entry (timestamp_at, level, message)
                    VALUES %s
                    ON CONFLICT (timestamp_at, level, message) DO NOTHING
                    """,
                    [
                        (
                            datetime.fromtimestamp(e.timestamp_unix, tz=timezone.utc),
                            e.level,
                            e.message,
                        )
                        for e in log.entries
                    ],
                )

        if sw := stats.get("customer_status_software"):
            cur.execute(
                """
                INSERT INTO customer_status_software (
                    pull_id, spec_version, hard_version, soft_version, mac_address,
                    serial_number, certificate_status, customer_version
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    pull_id,
                    sw.spec_version,
                    sw.hard_version,
                    sw.soft_version,
                    sw.mac_address,
                    sw.serial_number,
                    sw.certificate_status,
                    sw.customer_version,
                ),
            )

        if ads := stats.get("arris_device_status"):
            cur.execute(
                """
                INSERT INTO arris_device_status (
                    pull_id, firmware_version, internet_connection_status,
                    downstream_frequency_hz, downstream_signal_power_dbmv,
                    downstream_signal_snr_db
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    pull_id,
                    ads.firmware_version,
                    ads.internet_connection_status,
                    ads.downstream_frequency_hz,
                    ads.downstream_signal_power_dbmv,
                    ads.downstream_signal_snr_db,
                ),
            )

        if ari := stats.get("arris_register_info"):
            cur.execute(
                """
                INSERT INTO arris_register_info (pull_id, mac_address, serial_number, model_name)
                VALUES (%s, %s, %s, %s)
                """,
                (pull_id, ari.mac_address, ari.serial_number, ari.model_name),
            )

        if ars := stats.get("arris_register_status"):
            cur.execute(
                """
                INSERT INTO arris_register_status (pull_id, ask_me_later, never_ask)
                VALUES (%s, %s, %s)
                """,
                (pull_id, ars.ask_me_later, ars.never_ask),
            )

        if aci := stats.get("arris_configuration_info"):
            cur.execute(
                """
                INSERT INTO arris_configuration_info (
                    pull_id, downstream_frequency_hz, upstream_channel_id,
                    downstream_plan, energy_efficient_ethernet, led_status
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    pull_id,
                    aci.downstream_frequency_hz,
                    aci.upstream_channel_id,
                    aci.downstream_plan,
                    aci.energy_efficient_ethernet,
                    aci.led_status,
                ),
            )

    conn.commit()
    return pull_id


def do_pull(conn, modem_cfg: ModemConfig) -> int:
    """Login to modem, fetch all stats, persist to DB. Returns pull id."""
    client = HNAPClient(modem_cfg.host, modem_cfg.username, modem_cfg.password)
    try:
        client.login()
        stats = client.get_stats(get_all_hnap_actions())
        return insert_pull(conn, stats)
    finally:
        client.close()


def main() -> None:
    # Python as PID 1 in a container ignores SIGTERM by default.
    # Restore the default so docker stop / docker-compose down work promptly.
    def _handle_sigterm(*_):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _handle_sigterm)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Monitor Arris S33 modem and store data in TimescaleDB."
    )

    # Modem options
    parser.add_argument(
        "--modem-host",
        default=os.environ.get("S33_MODEM_HOST") or "192.168.0.1",
        help="modem host or IP (env: S33_MODEM_HOST, default: 192.168.0.1)",
    )
    parser.add_argument(
        "--modem-username",
        default=os.environ.get("S33_MODEM_USERNAME") or "admin",
        help="modem username (env: S33_MODEM_USERNAME, default: admin)",
    )
    parser.add_argument(
        "--modem-password",
        default=os.environ.get("S33_MODEM_PASSWORD"),
        help="modem password (env: S33_MODEM_PASSWORD)",
    )

    # DB options (S33_DB_ prefix)
    parser.add_argument(
        "--db-host",
        default=os.environ.get("S33_DB_HOST") or "localhost",
        help="database host (env: S33_DB_HOST, default: localhost)",
    )
    parser.add_argument(
        "--db-port",
        type=int,
        default=int(os.environ.get("S33_DB_PORT") or 5432),
        help="database port (env: S33_DB_PORT, default: 5432)",
    )
    parser.add_argument(
        "--db-user",
        default=os.environ.get("S33_DB_USER") or "postgres",
        help="database user (env: S33_DB_USER, default: postgres)",
    )
    parser.add_argument(
        "--db-password",
        default=os.environ.get("S33_DB_PASSWORD"),
        help="database password (env: S33_DB_PASSWORD)",
    )
    parser.add_argument(
        "--db-name",
        default=os.environ.get("S33_DB_NAME") or "s33mon",
        help="database name (env: S33_DB_NAME, default: s33mon)",
    )

    # Poll interval
    parser.add_argument(
        "--interval",
        type=int,
        default=int(os.environ.get("S33_INTERVAL") or 900),
        metavar="SECONDS",
        help="poll interval in seconds (env: S33_INTERVAL, default: 900 = 15 min)",
    )

    args = parser.parse_args()

    if not args.modem_password:
        parser.error("missing --modem-password (or S33_MODEM_PASSWORD)")
    if not args.db_password:
        parser.error("missing --db-password (or S33_DB_PASSWORD)")

    modem_cfg = ModemConfig(
        host=args.modem_host,
        username=args.modem_username,
        password=args.modem_password,
    )
    db_cfg = DBConfig(
        host=args.db_host,
        port=args.db_port,
        user=args.db_user,
        password=args.db_password,
        dbname=args.db_name,
    )

    logger.info(
        "Connecting to %s@%s:%d/%s ...",
        db_cfg.user,
        db_cfg.host,
        db_cfg.port,
        db_cfg.dbname,
    )
    conn = connect(db_cfg)
    setup_schema(conn)

    logger.info(
        "Starting poll loop — modem: %s, interval: %ds",
        modem_cfg.host,
        args.interval,
    )
    while True:
        try:
            pull_id = do_pull(conn, modem_cfg)
            logger.info("Pull #%d stored successfully.", pull_id)
            raised = False
        except Exception:
            logger.exception("Error during pull — will retry next interval.")
            raised = True
            try:
                conn.close()
            except Exception:
                pass
            try:
                conn = connect(db_cfg)
            except Exception:
                logger.exception("Failed to reconnect to database.")

        # try again sooner if we had an error, to recover faster
        sleep_time = args.interval if not raised else min(60, args.interval)
        logger.info("Sleeping %ds ...", sleep_time)
        time.sleep(sleep_time)


if __name__ == "__main__":
    main()
