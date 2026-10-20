import json
import os
from datetime import UTC, datetime
from unittest.mock import Mock

import psycopg2
import pytest
from psycopg2.extras import RealDictCursor, execute_values

from scripts import repair_modem_timezone as repair


def status(pull_id, observed, stored):
    return {
        "pull_id": pull_id,
        "pulled_at": datetime.fromisoformat(observed).isoformat(),
        "system_time_unix": int(datetime.fromisoformat(stored).timestamp()),
    }


def event(stored, message="event", observed=None):
    return {
        "timestamp_at": datetime.fromisoformat(stored).isoformat(),
        "level": 6,
        "message": message,
        "first_observed_at": observed,
    }


@pytest.fixture
def snapshot():
    return {
        "status": [
            status(1, "2026-02-20T07:10:00+00:00", "2026-02-20T07:00:00+00:00"),
            status(2, "2026-03-08T07:40:00+00:00", "2026-03-08T07:30:00+00:00"),
            status(3, "2026-03-08T08:40:00+00:00", "2026-03-08T07:30:00+00:00"),
            status(4, "2026-10-20T21:42:00+00:00", "2026-10-20T20:32:00+00:00"),
            status(5, "2026-10-20T21:50:00+00:00", "1970-01-01T05:00:00+00:00"),
        ],
        "logs": [
            event("2026-02-20T07:00:00+00:00"),
            event(
                "2026-03-08T07:11:43+00:00",
                "Successful LAN WebGUI login on 26/03/08 at  2:11 AM.",
            ),
            event(
                "2026-03-08T07:12:09+00:00",
                "Successful LAN WebGUI login on 26/03/08 at  3:12 AM.",
            ),
            event("2026-03-08T07:30:00+00:00"),
            event("2026-10-20T19:00:00+00:00", observed="2026-10-20T21:00:00+00:00"),
            event("2026-10-20T20:00:00+00:00"),
        ],
    }


def test_plan_preserves_spring_gap_status_and_ambiguous_events(snapshot):
    plan = repair.build_plan(snapshot)
    assert [r["pull_id"] for r in plan["status_updates"]] == [3, 4]
    assert all(
        r["corrected"] == r["system_time_unix"] + 3600 for r in plan["status_updates"]
    )
    assert [r["corrected"] for r in plan["log_updates"]] == [
        "2026-03-08T08:12:09+00:00",
        "2026-10-20T20:00:00+00:00",
        "2026-10-20T21:00:00+00:00",
    ]
    assert plan["ambiguous_logs"] == [snapshot["logs"][3]]
    assert plan["invalid_status_pull_ids"] == [5]
    assert snapshot["status"][3]["system_time_unix"] == int(
        datetime(2026, 10, 20, 20, 32, tzinfo=UTC).timestamp()
    )


@pytest.mark.parametrize(
    "observed,stored",
    [
        ("2026-10-20T21:42:00+00:00", "2026-10-20T21:32:00+00:00"),
        ("2026-10-20T21:42:00+00:00", "2026-10-20T19:32:00+00:00"),
        ("2026-11-01T21:42:00+00:00", "2026-11-01T20:32:00+00:00"),
    ],
)
def test_plan_refuses_corrected_unexpected_and_out_of_scope_history(observed, stored):
    with pytest.raises(ValueError):
        repair.build_plan({"status": [status(1, observed, stored)], "logs": []})


def test_plan_refuses_conflicting_final_event_keys(snapshot):
    snapshot["logs"] = [event("2026-03-08T07:30:00+00:00") for _ in range(2)]
    with pytest.raises(ValueError, match="keys conflict"):
        repair.build_plan(snapshot)


def test_apply_refuses_changed_snapshot_before_any_updates(
    snapshot, tmp_path, monkeypatch
):
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(repair.build_plan(snapshot)))
    snapshot["logs"].append(event("2026-10-20T22:00:00+00:00"))
    conn = Mock()
    conn.__enter__ = Mock(return_value=conn)
    conn.__exit__ = Mock(return_value=False)
    cur = Mock()
    cur.__enter__ = Mock(return_value=cur)
    cur.__exit__ = Mock(return_value=False)
    conn.cursor.return_value = cur
    monkeypatch.setattr(repair.psycopg2, "connect", Mock(return_value=conn))
    monkeypatch.setattr(repair, "read_snapshot", lambda _: snapshot)
    apply = Mock()
    monkeypatch.setattr(repair, "apply_plan", apply)
    with pytest.raises(ValueError, match="differs from the preview"):
        repair.run(path, apply=True)
    apply.assert_not_called()
    conn.close.assert_called_once()


@pytest.fixture
def database(snapshot):
    dsn = os.environ.get("S33_REPAIR_TEST_DSN")
    if not dsn:
        pytest.skip("Set S33_REPAIR_TEST_DSN to a disposable local PostgreSQL database")
    conn = psycopg2.connect(dsn, options="-c timezone=UTC")
    with conn, conn.cursor() as cur:
        cur.execute("""
            CREATE TEMP TABLE pull (id bigint PRIMARY KEY, pulled_at timestamptz);
            CREATE TEMP TABLE customer_status_connection_info
                (pull_id bigint PRIMARY KEY, system_time_unix bigint);
            CREATE TEMP TABLE log_entry
                (timestamp_at timestamptz, level integer, message text, first_observed_at timestamptz,
                 PRIMARY KEY (timestamp_at, level, message));
        """)
        execute_values(
            cur,
            "INSERT INTO pull VALUES %s",
            [(r["pull_id"], r["pulled_at"]) for r in snapshot["status"]],
        )
        execute_values(
            cur,
            "INSERT INTO customer_status_connection_info VALUES %s",
            [(r["pull_id"], r["system_time_unix"]) for r in snapshot["status"]],
        )
        execute_values(
            cur,
            "INSERT INTO log_entry VALUES %s",
            [
                (r["timestamp_at"], r["level"], r["message"], r["first_observed_at"])
                for r in snapshot["logs"]
            ],
        )
    try:
        yield conn
    finally:
        conn.close()


def test_apply_preserves_rows_observations_and_transient_key_conflicts(
    database, snapshot
):
    plan = repair.build_plan(snapshot)
    with database, database.cursor(cursor_factory=RealDictCursor) as cur:
        repair.apply_plan(cur, plan)
        actual = repair.read_snapshot(cur)
    assert len(actual["logs"]) == len(snapshot["logs"])
    assert actual["logs"][:2] == snapshot["logs"][:2]
    assert snapshot["logs"][3] in actual["logs"]
    assert actual["logs"][-2]["timestamp_at"] == "2026-10-20T20:00:00+00:00"
    assert (
        actual["logs"][-2]["first_observed_at"]
        == snapshot["logs"][-2]["first_observed_at"]
    )
    assert actual["logs"][-1]["timestamp_at"] == "2026-10-20T21:00:00+00:00"
    assert [r["pulled_at"] for r in actual["status"]] == [
        r["pulled_at"] for r in snapshot["status"]
    ]
    assert [
        r["system_time_unix"] - old["system_time_unix"]
        for r, old in zip(actual["status"], snapshot["status"], strict=True)
    ] == [0, 0, 3600, 3600, 0]
    with pytest.raises(ValueError, match="already uses fixed"):
        repair.build_plan(actual)


def test_event_failure_rolls_back_status_updates(database, snapshot):
    plan = repair.build_plan(snapshot)
    with database, database.cursor() as cur:
        cur.execute(
            "INSERT INTO log_entry VALUES ('2026-10-20 21:00:00+00', 6, 'event', NULL)"
        )
    with (
        pytest.raises(psycopg2.IntegrityError, match="duplicate key"),
        database,
        database.cursor() as cur,
    ):
        repair.apply_plan(cur, plan)
    with database.cursor(cursor_factory=RealDictCursor) as cur:
        actual = repair.read_snapshot(cur)
    assert actual["status"] == snapshot["status"]
    assert len(actual["logs"]) == len(snapshot["logs"]) + 1
