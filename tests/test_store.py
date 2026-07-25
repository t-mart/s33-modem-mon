"""The insert layer derives column names from dataclass fields, so schema.sql
and s33mon.models have to agree. These tests are that agreement, checked without
needing a database."""

import re
from dataclasses import fields

import pytest

from s33mon.ip import OwnIp
from s33mon.models import (
    DownstreamChannel,
    ModemStats,
    UpstreamChannel,
)
from s33mon.store.timescale import (
    CHANNEL_STATS,
    HYPERTABLES,
    SINGLE_ROW_STATS,
    row_values,
    schema_sql,
)

CREATE_TABLE = re.compile(
    r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);", re.DOTALL | re.IGNORECASE
)


def schema_columns() -> dict[str, list[str]]:
    """Table name to column names, as declared in schema.sql."""
    tables = {}
    for name, body in CREATE_TABLE.findall(schema_sql()):
        columns = []
        for line in body.splitlines():
            line = line.strip()
            if not line or line.startswith("--") or line.upper().startswith("PRIMARY "):
                continue
            columns.append(line.split()[0])
        tables[name] = columns
    return tables


@pytest.fixture(scope="module")
def tables() -> dict[str, list[str]]:
    return schema_columns()


def test_schema_parses(tables) -> None:
    assert tables["pull"] == ["id", "pulled_at"]
    assert tables["own_ip"] == ["pull_id", "ip"]


@pytest.mark.parametrize("key", SINGLE_ROW_STATS)
def test_single_row_tables_match_their_dataclass(key: str, tables) -> None:
    stat_cls = ModemStats.__annotations__[key]
    assert key in tables, f"schema.sql has no table for {key}"
    assert tables[key] == ["pull_id", *(f.name for f in fields(stat_cls))]


@pytest.mark.parametrize(
    ("table", "row_cls"),
    [("downstream_channel", DownstreamChannel), ("upstream_channel", UpstreamChannel)],
)
def test_channel_tables_match_their_dataclass(table, row_cls, tables) -> None:
    expected = ["pulled_at", "pull_id", *(f.name for f in fields(row_cls))]
    assert tables[table] == expected


def test_channel_stats_reference_real_stats_and_tables(tables) -> None:
    for key, table in CHANNEL_STATS:
        assert key in ModemStats.__annotations__
        assert table in tables


def test_hypertable_time_columns_exist(tables) -> None:
    for table, time_column in HYPERTABLES:
        assert time_column in tables[table]


def test_every_schema_table_is_written_by_something(tables) -> None:
    written = (
        {"pull", "log_entry", "own_ip"}
        | set(SINGLE_ROW_STATS)
        | {table for _, table in CHANNEL_STATS}
    )
    assert set(tables) == written


def test_row_values_puts_extras_first_then_fields() -> None:
    channel = DownstreamChannel(
        channel_id=31,
        lock_status="Locked",
        modulation="QAM256",
        frequency_hz=741000000,
        power_dbmv=11.0,
        snr_db=37.0,
        corrected_count=0,
        uncorrectable_count=2,
    )
    values = row_values(channel, pulled_at="ts", pull_id=7)
    assert list(values)[:2] == ["pulled_at", "pull_id"]
    assert list(values.values())[:3] == ["ts", 7, 31]
    assert values["uncorrectable_count"] == 2


def test_row_values_on_a_stat_without_extras() -> None:
    assert row_values(OwnIp(ip="203.0.113.7"), pull_id=1) == {
        "pull_id": 1,
        "ip": "203.0.113.7",
    }
