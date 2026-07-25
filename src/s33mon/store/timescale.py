"""TimescaleDB persistence for modem stats.

Insert statements are generated from the dataclass fields in s33mon.models
rather than transcribed by hand, so a new field on a stat class only needs a
matching column in schema.sql.
"""

import logging
from dataclasses import fields
from datetime import UTC, datetime
from importlib import resources
from typing import Any

import psycopg2
import psycopg2.extras
from psycopg2 import sql

from s33mon.config import DBConfig
from s33mon.ip import OwnIp
from s33mon.models import ModemStats

logger = logging.getLogger(__name__)

# Stats stored as a single row keyed by pull_id. The ModemStats key doubles as
# the table name, and dataclass field names double as column names.
SINGLE_ROW_STATS = (
    "customer_status_startup_sequence",
    "customer_status_connection_info",
    "customer_status_software",
    "arris_device_status",
    "arris_register_info",
    "arris_register_status",
    "arris_configuration_info",
)

# Stats holding a list of per-channel rows, as (ModemStats key, table name).
CHANNEL_STATS = (
    ("customer_status_downstream_channel_info", "downstream_channel"),
    ("customer_status_upstream_channel_info", "upstream_channel"),
)

HYPERTABLES = (
    ("pull", "pulled_at"),
    ("log_entry", "timestamp_at"),
    ("downstream_channel", "pulled_at"),
    ("upstream_channel", "pulled_at"),
)


def schema_sql() -> str:
    return resources.files("s33mon.store").joinpath("schema.sql").read_text()


def _connect(cfg: DBConfig, dbname: str):
    return psycopg2.connect(
        host=cfg.host,
        port=cfg.port,
        user=cfg.user,
        password=cfg.password,
        dbname=dbname,
    )


def connect(cfg: DBConfig):
    """Connect to the target database, creating it first if it doesn't exist."""
    try:
        return _connect(cfg, cfg.name)
    except psycopg2.OperationalError as e:
        if "does not exist" not in str(e):
            raise
        logger.info("Database %r not found, creating it.", cfg.name)
        # Must connect to a different DB to issue CREATE DATABASE
        bootstrap = _connect(cfg, "postgres")
        bootstrap.autocommit = True
        with bootstrap.cursor() as cur:
            cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(cfg.name)))
        bootstrap.close()
        logger.info("Database %r created.", cfg.name)
        return _connect(cfg, cfg.name)


def setup_schema(conn) -> None:
    """Create tables and TimescaleDB hypertables. Idempotent."""
    with conn.cursor() as cur:
        cur.execute(schema_sql())

        for table, time_column in HYPERTABLES:
            cur.execute(
                "SELECT create_hypertable(%s, %s, if_not_exists => TRUE);",
                (table, time_column),
            )

        # Log deduplication. Must include the partition column (timestamp_at).
        # insert_pull's ON CONFLICT references this index.
        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS log_entry_dedup
                ON log_entry (timestamp_at, level, message);
        """)

    conn.commit()
    logger.info("Schema is ready.")


def row_values(obj: Any, **extra: Any) -> dict[str, Any]:
    """Column name to value for one dataclass instance, extras first."""
    return extra | {f.name: getattr(obj, f.name) for f in fields(obj)}


def _insert_row(cur, table: str, obj: Any, **extra: Any) -> None:
    """Insert one row, taking column names from the dataclass fields of obj."""
    values = row_values(obj, **extra)
    statement = sql.SQL("INSERT INTO {table} ({cols}) VALUES ({vals})").format(
        table=sql.Identifier(table),
        cols=sql.SQL(", ").join(sql.Identifier(c) for c in values),
        vals=sql.SQL(", ").join(sql.Placeholder() for _ in values),
    )
    cur.execute(statement, tuple(values.values()))


def _insert_rows(cur, table: str, objs: list, **extra: Any) -> None:
    """Bulk-insert rows sharing the same dataclass type. No-op on an empty list."""
    if not objs:
        return
    columns = list(row_values(objs[0], **extra))
    statement = sql.SQL("INSERT INTO {table} ({cols}) VALUES %s").format(
        table=sql.Identifier(table),
        cols=sql.SQL(", ").join(sql.Identifier(c) for c in columns),
    )
    psycopg2.extras.execute_values(
        cur,
        statement.as_string(cur),
        [tuple(row_values(o, **extra).values()) for o in objs],
    )


def _insert_log_entries(cur, entries: list) -> None:
    if not entries:
        return
    psycopg2.extras.execute_values(
        cur,
        """
        INSERT INTO log_entry (timestamp_at, level, message)
        VALUES %s
        ON CONFLICT (timestamp_at, level, message) DO NOTHING
        """,
        [
            (
                datetime.fromtimestamp(e.timestamp_unix, tz=UTC),
                e.level,
                e.message,
            )
            for e in entries
        ],
    )


def insert_pull(conn, stats: ModemStats, own_ip: OwnIp | None = None) -> int:
    """Insert one pull and all associated stat rows. Returns the pull id."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO pull (pulled_at) VALUES (NOW()) RETURNING id, pulled_at;"
        )
        pull_id, pulled_at = cur.fetchone()

        for key in SINGLE_ROW_STATS:
            if stat := stats.get(key):
                _insert_row(cur, key, stat, pull_id=pull_id)

        for key, table in CHANNEL_STATS:
            if stat := stats.get(key):
                _insert_rows(
                    cur, table, stat.channels, pulled_at=pulled_at, pull_id=pull_id
                )

        if log := stats.get("customer_status_log"):
            _insert_log_entries(cur, log.entries)

        if own_ip:
            _insert_row(cur, "own_ip", own_ip, pull_id=pull_id)

    conn.commit()
    return pull_id
