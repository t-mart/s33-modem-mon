import re
import sqlite3

import pytest

from s33mon.store.timescale import schema_sql


@pytest.fixture
def events():
    with sqlite3.connect(":memory:") as conn:
        table = re.search(
            r"CREATE TABLE IF NOT EXISTS log_entry \(.*?\n\);", schema_sql(), re.DOTALL
        )
        view = re.search(
            r"CREATE OR REPLACE VIEW modem_event AS.*?;", schema_sql(), re.DOTALL
        )
        assert table is not None and view is not None
        conn.execute(table[0])
        # SQLite executes the view expressions without a PostgreSQL server.
        conn.execute(view[0].replace("CREATE OR REPLACE VIEW", "CREATE VIEW"))
        yield conn


@pytest.mark.parametrize(
    ("message", "event_type"),
    [
        (
            "SYNC Timing Synchronization failure - Failed to acquire QAM/QPSK symbol timing;;CM-MAC=example;",
            "sync_failure",
        ),
        (
            "Started Unicast Maintenance Ranging - No Response received - T3 time-out;",
            "t3_timeout",
        ),
        ("No Ranging Response received - T3 time-out;", "t3_timeout"),
        (
            "Received Response to Broadcast Maintenance Request, But no Unicast Maintenance opportunities received - T4 time out;",
            "t4_timeout",
        ),
        ("T4 time-out;CM-MAC=example;", "t4_timeout"),
        ("MDD message timeout;CM-MAC=example;", "mdd_timeout"),
        ("DHCP failed - RS sent, no RA received;", "dhcp_failure"),
        ("DHCP WARNING - Non-critical field invalid in response ;", "dhcp_warning"),
        ("DS profile assignment change. DS Chan ID: 32;", "profile_change"),
        ("US profile assignment change. US Chan ID: 3;", "profile_change"),
        ("Successful LAN WebGUI login from 192.0.2.1", "collector_login"),
        ("mdd MESSAGE TIMEOUT;", "mdd_timeout"),
        ("Primary address failed, secondary active;", "other"),
        ("Unknown firmware event", "other"),
        ("", "other"),
    ],
)
def test_event_classification_preserves_source(events, message, event_type):
    row = ("2026-10-20 18:21:15+00", "2026-10-20 16:56:22+00", 3, message)
    events.execute("INSERT INTO log_entry VALUES (?, ?, ?, ?)", row)

    assert events.execute("SELECT * FROM modem_event").fetchone() == (*row, event_type)


def test_event_view_counts_deduplicated_records(events):
    for modem_time in ("2026-10-20 16:56:22+00", "2026-10-20 16:56:28+00"):
        row = ("2026-10-20 18:21:15+00", modem_time, 3, "MDD message timeout")
        for _ in range(2):
            events.execute(
                "INSERT INTO log_entry VALUES (?, ?, ?, ?) "
                "ON CONFLICT (timestamp_at, level, message) DO NOTHING",
                row,
            )

    assert events.execute(
        "SELECT event_type, COUNT(*) FROM modem_event GROUP BY event_type"
    ).fetchall() == [("mdd_timeout", 2)]
