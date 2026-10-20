import json
import os
import re
from pathlib import Path

import psycopg2
import pytest


@pytest.fixture(scope="module")
def query():
    dsn = os.environ.get("S33_TEST_DSN")
    if not dsn:
        pytest.skip("Set S33_TEST_DSN to test dashboard SQL on TimescaleDB")
    conn = psycopg2.connect(
        dsn,
        connect_timeout=5,
        options="-c default_transaction_read_only=on -c statement_timeout=10000",
    )
    dashboard = json.loads(
        (Path(__file__).parents[1] / "dashboards/s33-modem.json").read_text()
    )
    queries = {
        panel["id"]: panel["targets"][0]["rawSql"]
        for panel in dashboard["panels"]
        if panel.get("targets")
    }
    queries.update(
        {
            annotation["name"]: annotation["target"]["rawSql"]
            for annotation in dashboard["annotations"]["list"]
            if "target" in annotation
        }
    )

    def execute(panel_id, fixtures, resolution="5m"):
        sql = queries[panel_id]
        start, end = "'2026-10-20 18:00:00+00'", "'2026-10-20 18:30:00+00'"
        sql = sql.replace("$resolution", resolution)
        sql = sql.replace("$__timeFrom()", start).replace("$__timeTo()", end)
        sql = re.sub(
            r"\$__timeFilter\(([^)]+)\)",
            lambda match: f"{match[1]} BETWEEN {start} AND {end}",
            sql,
        )
        sql = f"WITH {fixtures}" + (
            ", " + sql[5:] if sql.startswith("WITH ") else " " + sql
        )
        with conn.cursor() as cur:
            cur.execute(sql)
            return cur.fetchall()

    try:
        yield execute
    finally:
        conn.close()


@pytest.mark.parametrize(
    "panel_id,modulation", [(14, "QAM256"), (13, "QAM256"), (22, "OFDM PLC")]
)
def test_codeword_stacks_use_duration_weights(query, panel_id, modulation):
    fixtures = f"""
    downstream_channel AS (
      SELECT time::timestamptz AS pulled_at, channel_id, '{modulation}' AS modulation,
        counter AS corrected_count, counter AS uncorrectable_count
      FROM (VALUES
        ('2026-10-20 17:55:00+00', 1, 0),
        ('2026-10-20 18:05:00+00', 1, 100),
        ('2026-10-20 18:20:00+00', 1, 550),
        ('2026-10-20 17:55:00+00', 2, 0),
        ('2026-10-20 18:05:00+00', 2, 300),
        ('2026-10-20 18:20:00+00', 2, 450)
      ) samples(time, channel_id, counter)
    )
    """
    rows = query(panel_id, fixtures, resolution="30m")
    assert [(name, float(value)) for _, name, value in rows] == [
        ("Channel 1", 22.0),
        ("Channel 2", 18.0),
    ]
    assert float(sum(value for _, _, value in rows)) == pytest.approx(40)


@pytest.mark.parametrize(
    "panel_id,modulation", [(14, "QAM256"), (13, "QAM256"), (22, "OFDM PLC")]
)
def test_codeword_rates_exclude_counter_resets(query, panel_id, modulation):
    fixtures = f"""
    downstream_channel AS (
      SELECT time::timestamptz AS pulled_at, 1 AS channel_id, '{modulation}' AS modulation,
        counter AS corrected_count, counter AS uncorrectable_count
      FROM (VALUES
        ('2026-10-20 17:55:00+00', 100),
        ('2026-10-20 18:05:00+00', 50),
        ('2026-10-20 18:15:00+00', 70)
      ) samples(time, counter)
    )
    """
    rows = query(panel_id, fixtures, resolution="30m")
    assert len(rows) == 1
    assert float(rows[0][2]) == pytest.approx(2)


BONDING_FIXTURES = """
pull AS (
  SELECT id, '2026-10-20 18:01:00+00'::timestamptz + (id - 1) * interval '5 minutes' AS pulled_at
  FROM generate_series(1, 4) AS ids(id)
), downstream_channel AS (
  SELECT p.id AS pull_id, p.pulled_at, c.channel_id,
    CASE WHEN p.id = 3 AND c.channel_id = 1 THEN 'Unlocked' ELSE 'Locked' END AS lock_status
  FROM pull p CROSS JOIN generate_series(1, 33) AS c(channel_id)
  WHERE c.channel_id <= CASE p.id WHEN 2 THEN 31 WHEN 4 THEN 33 ELSE 32 END
), upstream_channel AS (
  SELECT p.id AS pull_id, p.pulled_at, c.channel_id, 'Locked' AS lock_status
  FROM pull p CROSS JOIN generate_series(1, 4) AS c(channel_id)
)
"""


def test_bonding_distinguishes_missing_unlocked_extra_and_unknown(query):
    rows = {
        (time.minute, name): value for time, name, value in query(17, BONDING_FIXTURES)
    }
    assert rows[0, "Downstream missing"] == 0
    assert rows[0, "Downstream unlocked"] == 0
    assert rows[5, "Downstream missing"] == 1
    assert rows[5, "Downstream unlocked"] == 0
    assert rows[10, "Downstream missing"] == 0
    assert rows[10, "Downstream unlocked"] == 1
    assert rows[15, "Downstream missing"] == -1
    assert rows[20, "Downstream missing"] is None
    assert rows[20, "Downstream unlocked"] is None
    assert rows[0, "Upstream missing"] == rows[0, "Upstream unlocked"] == 0


def test_bonding_preserves_brief_deficits_in_large_buckets(query):
    rows = {
        name: value
        for _, name, value in query(17, BONDING_FIXTURES, "30m")
        if value is not None
    }
    assert rows["Downstream missing"] == -1
    assert rows["Downstream unlocked"] == 1


def test_current_ip_does_not_reuse_an_old_success(query):
    rows = query(
        26,
        """
      pull(id, pulled_at) AS (VALUES
        (1, '2026-10-20 18:01:00+00'::timestamptz),
        (2, '2026-10-20 18:06:00+00'::timestamptz)
      ), own_ip(pull_id, ip) AS (VALUES (1, '192.0.2.1'))
    """,
    )
    assert len(rows) == 1 and rows[0][1] is None


@pytest.mark.parametrize("seconds", [4208, -32])
def test_clock_drift_preserves_sign_and_subminute_precision(query, seconds):
    fixtures = f"""
      pull(id, pulled_at) AS (VALUES (1, '2026-10-20 18:01:00+00'::timestamptz)),
      customer_status_connection_info AS (
        SELECT id AS pull_id, EXTRACT(EPOCH FROM pulled_at) - {seconds} AS system_time_unix FROM pull
      )
    """
    for panel_id in (25, 27):
        rows = query(panel_id, fixtures)
        assert len(rows) == 1
        assert float(rows[0][1]) == pytest.approx(seconds / 60)


@pytest.mark.parametrize(
    "samples,expected_minutes",
    [
        (
            [("17:55", 3600), ("18:00", 3902), ("18:05", 60), ("18:10", 358)],
            [4],
        ),
        ([("17:55", 60), ("18:10", 300)], [5]),
        ([("17:55", 3600), ("18:00", 10)], [59]),
        ([("18:00", 60), ("18:05", 358), ("18:10", 660)], []),
        ([("17:55", 3600), ("18:35", 60)], []),
    ],
    ids=[
        "uptime-reset",
        "reset-across-gap",
        "range-boundary",
        "jitter",
        "outside-range",
    ],
)
def test_reboots_use_uptime_and_include_previous_sample(
    query, samples, expected_minutes
):
    values = ", ".join(
        f"({index}, '2026-10-20 {time}:00+00'::timestamptz, {uptime})"
        for index, (time, uptime) in enumerate(samples)
    )
    rows = query(
        32,
        f"""
        observations(id, pulled_at, uptime) AS (VALUES {values}),
        pull AS (SELECT id, pulled_at FROM observations),
        customer_status_connection_info AS (
          SELECT id AS pull_id, uptime AS system_uptime_seconds FROM observations
        )
        """,
    )
    assert [estimated.minute for estimated, _ in rows] == expected_minutes


@pytest.mark.parametrize(
    "samples,expected_times",
    [
        ([("17:55", 3600), ("18:05", 60), ("18:10", 358)], ["18:04:00"]),
        ([("17:55", 60), ("18:10", 300)], ["18:05:00"]),
        ([("18:25", 3600), ("18:35", 360)], ["18:29:00"]),
        ([("17:55", 3600), ("18:00", 10)], []),
        ([("18:00", 60), ("18:05", 358)], []),
    ],
    ids=["reset", "reset-across-gap", "late-observation", "outside-range", "jitter"],
)
def test_reboot_markers_use_estimated_time(query, samples, expected_times):
    values = ", ".join(
        f"({index}, '2026-10-20 {time}:00+00'::timestamptz, {uptime})"
        for index, (time, uptime) in enumerate(samples)
    )
    rows = query(
        "Modem reboots",
        f"""
        observations(id, pulled_at, uptime) AS (VALUES {values}),
        pull AS (SELECT id, pulled_at FROM observations),
        customer_status_connection_info AS (
          SELECT id AS pull_id, uptime AS system_uptime_seconds FROM observations
        )
        """,
    )
    assert [(time.strftime("%H:%M:%S"), text, tags) for time, text, tags in rows] == [
        (time, "Estimated modem reboot", "reboot") for time in expected_times
    ]
