import json
import os
import re
from pathlib import Path

import psycopg2
import pytest


def panels_in(panels):
    for panel in panels:
        yield panel
        yield from panels_in(panel.get("panels", []))


@pytest.fixture(scope="module")
def dashboard():
    return json.loads(
        (Path(__file__).parents[1] / "dashboards/s33-modem.json").read_text()
    )


@pytest.fixture(scope="module")
def query(dashboard):
    dsn = os.environ.get("S33_TEST_DSN")
    if not dsn:
        pytest.skip("Set S33_TEST_DSN to test dashboard SQL on TimescaleDB")
    conn = psycopg2.connect(
        dsn,
        connect_timeout=5,
        options="-c default_transaction_read_only=on -c statement_timeout=10000",
    )
    queries = {
        panel["id"]: panel["targets"][0]["rawSql"]
        for panel in panels_in(dashboard["panels"])
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


@pytest.mark.parametrize(
    "panel_id,table,column,values,expected",
    [
        (
            18,
            "downstream_channel",
            "frequency_hz",
            [600000000, 606000000, 612000000, 615125000],
            [606, 612, 615.125],
        ),
        (
            8,
            "upstream_channel",
            "width_hz",
            [1600000, 3200000, 6400000, 2400000],
            [3.2, 6.4, 2.4],
        ),
        (
            19,
            "downstream_channel",
            "modulation",
            ["'QAM16'", "'QAM256'", "'OFDM PLC'", "'QAM64'"],
            ["QAM256", "OFDM PLC", "QAM64"],
        ),
    ],
)
def test_channel_changes_preserve_brief_changes_and_range_baselines(
    query, panel_id, table, column, values, expected
):
    earlier, baseline, other, changed = values
    rows = query(
        panel_id,
        f"""
        {table} AS (
          SELECT time::timestamptz AS pulled_at, channel_id, value AS {column}
          FROM (VALUES
            ('2026-10-20 17:50:00+00', 1, {earlier}),
            ('2026-10-20 17:55:00+00', 1, {baseline}),
            ('2026-10-20 17:55:00+00', 2, {other}),
            ('2026-10-20 18:00:00+00', 1, {changed}),
            ('2026-10-20 18:05:00+00', 1, {baseline}),
            ('2026-10-20 18:10:00+00', 1, {baseline}),
            ('2026-10-20 18:10:00+00', 2, {changed}),
            ('2026-10-20 18:10:00+00', 3, {changed}),
            ('2026-10-20 18:35:00+00', 1, {changed})
          ) samples(time, channel_id, value)
        )
        """,
        resolution="30m",
    )
    baseline_value, other_value, changed_value = expected
    assert [
        (
            time.minute,
            channel,
            old if isinstance(old, str) else float(old),
            new if isinstance(new, str) else float(new),
        )
        for time, channel, old, new in rows
    ] == [
        (10, "2", other_value, changed_value),
        (5, "1", changed_value, baseline_value),
        (0, "1", baseline_value, changed_value),
    ]


@pytest.mark.parametrize(
    "panel_id,table,column,value,label",
    [
        (18, "downstream_channel", "frequency_hz", "606000000", "frequency"),
        (8, "upstream_channel", "width_hz", "6400000", "width"),
        (19, "downstream_channel", "modulation", "'QAM256'::text", "modulation"),
    ],
)
@pytest.mark.parametrize("minutes", [[0, 5, 10], [-5, 35], []])
def test_channel_changes_distinguish_stable_data_from_absent_data(
    query, panel_id, table, column, value, label, minutes
):
    rows = query(
        panel_id,
        f"""
        {table} AS (
          SELECT '2026-10-20 18:00:00+00'::timestamptz + minute * interval '1 minute' AS pulled_at,
            1 AS channel_id, {value} AS {column}
          FROM unnest(ARRAY{minutes}::integer[]) AS offsets(minute)
        )
        """,
    )
    message = (
        f"No {label} changes in this range"
        if minutes == [0, 5, 10]
        else "No samples in this range"
    )
    assert rows == [(None, message, None, None)]


SNAPSHOT_FIXTURES = """
pull AS (
  SELECT id, time::timestamptz AS pulled_at FROM (VALUES
    (1, '2026-10-20 17:55:00+00'),
    (2, '2026-10-20 18:05:00+00'),
    (3, '2026-10-20 18:25:00+00'),
    (4, '2026-10-20 18:35:00+00')
  ) pulls(id, time)
), downstream_channel AS (
  SELECT c.*, p.pulled_at FROM pull p JOIN (VALUES
    (1, 1, 600000000, 'QAM256'), (1, 2, 620000000, 'OFDM PLC'),
    (2, 1, 606000000, 'QAM64'), (2, 2, 620000000, 'OFDM PLC'), (2, 3, 632000000, 'QAM256'),
    (3, 1, 612000000, 'QAM256'), (3, 2, 620000000, 'OFDM PLC'),
    (4, 1, 630000000, 'QAM16')
  ) c(pull_id, channel_id, frequency_hz, modulation) ON c.pull_id = p.id
), upstream_channel AS (
  SELECT c.*, p.pulled_at FROM pull p JOIN (VALUES
    (1, 5, 6400000), (1, 6, 6400000),
    (2, 5, 3200000), (2, 6, 6400000),
    (3, 5, 6400000), (3, 8, 1600000),
    (4, 5, 12800000)
  ) c(pull_id, channel_id, width_hz) ON c.pull_id = p.id
)
"""


def test_configuration_overviews_use_one_pull_inside_the_selected_range(query):
    frequency = query(33, SNAPSHOT_FIXTURES)
    assert [
        (float(mhz), position, color, channel, modulation)
        for mhz, position, color, channel, modulation, _ in frequency
    ] == [(612, 0, 1, "1", "QAM256"), (620, 0, 2, "2", "OFDM PLC")]
    assert all(observed.minute == 25 for *_, observed in frequency)
    assert query(34, SNAPSHOT_FIXTURES) == [("OFDM PLC", 1, "2"), ("QAM256", 1, "1")]
    assert [
        (channel, float(width)) for channel, width in query(35, SNAPSHOT_FIXTURES)
    ] == [("Channel 5", 6.4), ("Channel 8", 1.6)]


@pytest.mark.parametrize("panel_id", [36, 37])
def test_configuration_summaries_include_baseline_and_return_changes(query, panel_id):
    [(observed, snapshot, *histories)] = query(panel_id, SNAPSHOT_FIXTURES)
    assert observed.minute == 25
    assert snapshot == "2 channels"
    assert histories == ["2 changes across 1 channel"] * (2 if panel_id == 36 else 1)


@pytest.mark.parametrize("panel_id", [33, 34, 35, 36, 37])
def test_configuration_overviews_do_not_reuse_channels_from_an_older_pull(
    query, panel_id
):
    fixtures = SNAPSHOT_FIXTURES.replace(
        "(4, '2026-10-20 18:35:00+00')",
        "(4, '2026-10-20 18:35:00+00'), (5, '2026-10-20 18:29:00+00')",
    )
    rows = query(panel_id, fixtures)
    if panel_id < 36:
        assert rows == []
    else:
        assert rows[0][0].minute == 29
        assert rows[0][1] == "No channel samples at last pull"


@pytest.mark.parametrize("panel_id", [33, 34, 35, 36, 37])
def test_configuration_overviews_distinguish_absent_samples(query, panel_id):
    rows = query(panel_id, SNAPSHOT_FIXTURES.replace("2026-10-20", "2026-10-21"))
    if panel_id < 36:
        assert rows == []
    else:
        assert rows[0][:2] == (None, "No pulls in this range")
        assert all(value == "No samples in this range" for value in rows[0][2:])


@pytest.mark.parametrize("panel_id", [36, 37])
def test_configuration_history_totals_are_not_limited_to_the_detail_table(
    query, panel_id
):
    fixtures = """
      pull AS (
        SELECT id, '2026-10-20 18:00:00+00'::timestamptz + id * interval '10 seconds' AS pulled_at
        FROM generate_series(0, 125) ids(id)
      ), downstream_channel AS (
        SELECT id AS pull_id, pulled_at, 1 AS channel_id,
          600000000 + (id % 2) * 6000000 AS frequency_hz, 'QAM256' AS modulation FROM pull
      ), upstream_channel AS (
        SELECT id AS pull_id, pulled_at, 1 AS channel_id,
          3200000 + (id % 2) * 3200000 AS width_hz FROM pull
      )
    """
    [row] = query(panel_id, fixtures)
    assert row[2] == "125 changes across 1 channel"
    if panel_id == 36:
        assert row[3] == "No changes observed"


def test_configuration_details_are_collapsed_and_layout_does_not_overlap(dashboard):
    panels = list(panels_in(dashboard["panels"]))
    assert len({p["id"] for p in panels}) == len(panels)
    for panel_id, fields in [
        (36, ["Observed", "Snapshot", "Frequency history", "Modulation history"]),
        (37, ["Observed", "Snapshot", "Width history"]),
    ]:
        panel = next(p for p in panels if p["id"] == panel_id)
        matcher = panel["options"]["reduceOptions"]["fields"]
        assert matcher and all(re.fullmatch(matcher, name) for name in fields)
    for row_id, children in [(39, {18, 19}), (40, {8})]:
        row = next(p for p in panels if p["id"] == row_id)
        assert row["collapsed"]
        assert {p["id"] for p in row["panels"]} == children
    for group in [
        dashboard["panels"],
        *(p["panels"] for p in panels if p.get("panels")),
    ]:
        for index, panel in enumerate(group):
            a = panel["gridPos"]
            assert 0 <= a["x"] < a["x"] + a["w"] <= 24
            for other in group[index + 1 :]:
                b = other["gridPos"]
                assert (
                    a["x"] >= b["x"] + b["w"]
                    or b["x"] >= a["x"] + a["w"]
                    or a["y"] >= b["y"] + b["h"]
                    or b["y"] >= a["y"] + a["h"]
                )
