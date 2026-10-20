"""Repair the inspected 2026 history before the fixed UTC-5 collector starts."""

import argparse
import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor, execute_values

from s33mon.models import MODEM_TIMEZONE

HOUR = timedelta(hours=1)
GAP_START = datetime(2026, 3, 8, 7, tzinfo=UTC)
GAP_END = GAP_START + HOUR
HISTORY_START = datetime(2026, 1, 1, tzinfo=UTC)
HISTORY_END = datetime(2026, 11, 1, tzinfo=UTC)
LOGIN_TIME = re.compile(r" on (\d{2}/\d{2}/\d{2}) at\s+(\d{1,2}:\d{2} [AP]M)\.$")


def read_snapshot(cur):
    cur.execute("""
        SELECT ci.pull_id, p.pulled_at, ci.system_time_unix
        FROM customer_status_connection_info ci
        JOIN pull p ON p.id = ci.pull_id
        ORDER BY ci.pull_id
    """)
    status = [
        dict(row, pulled_at=row["pulled_at"].isoformat()) for row in cur.fetchall()
    ]
    cur.execute("""
        SELECT timestamp_at, level, message, first_observed_at
        FROM log_entry ORDER BY timestamp_at, level, message
    """)
    logs = [
        {
            key: value.isoformat() if isinstance(value, datetime) else value
            for key, value in row.items()
        }
        for row in cur.fetchall()
    ]
    return {"status": status, "logs": logs}


def candidates(stored):
    if GAP_START <= stored < GAP_END:
        return (stored, stored + HOUR)
    if GAP_END <= stored < HISTORY_END:
        return (stored + HOUR,)
    return (stored,)


def build_plan(snapshot):
    plan = {
        "version": 1,
        "snapshot_sha256": hashlib.sha256(
            json.dumps(snapshot, sort_keys=True).encode()
        ).hexdigest(),
        "status_updates": [],
        "log_updates": [],
        "ambiguous_logs": [],
        "invalid_status_pull_ids": [],
    }
    if not snapshot["status"]:
        raise ValueError("No status records exist.")
    for row in snapshot["status"]:
        observed = datetime.fromisoformat(row["pulled_at"])
        stored = datetime.fromtimestamp(row["system_time_unix"], UTC)
        if not HISTORY_START <= observed < HISTORY_END:
            raise ValueError(
                "This repair supports collection dates from January through October 2026 only."
            )
        if stored < HISTORY_START:
            plan["invalid_status_pull_ids"].append(row["pull_id"])
            continue
        valid = [
            value
            for value in candidates(stored)
            if timedelta(0) <= observed - value < timedelta(minutes=30)
        ]
        if len(valid) != 1:
            raise ValueError(
                f"Pull {row['pull_id']}: the clock differs from the inspected history or already uses fixed UTC-5."
            )
        corrected = int(valid[0].timestamp())
        if corrected != row["system_time_unix"]:
            plan["status_updates"].append(dict(row, corrected=corrected))

    keys = set()
    for row in snapshot["logs"]:
        stored = datetime.fromisoformat(row["timestamp_at"])
        if stored >= HISTORY_END:
            raise ValueError("An event date exceeds the repair scope.")
        options = candidates(stored)
        if len(options) == 2 and (match := LOGIN_TIME.search(row["message"])):
            embedded = (
                datetime.strptime(" ".join(match.groups()), "%y/%m/%d %I:%M %p")
                .replace(tzinfo=MODEM_TIMEZONE)
                .astimezone(UTC)
            )
            options = tuple(
                value
                for value in options
                if value.replace(second=0, microsecond=0) == embedded
            )
            if not options:
                raise ValueError(
                    f"The login message disagrees with its timestamp: {row['timestamp_at']}."
                )
        if len(options) == 2:
            plan["ambiguous_logs"].append(row)
            corrected = stored
        else:
            corrected = options[0]
        key = (corrected.isoformat(), row["level"], row["message"])
        if key in keys:
            raise ValueError(
                "Corrected event keys conflict. No automatic merge is permitted."
            )
        keys.add(key)
        if corrected != stored:
            plan["log_updates"].append(dict(row, corrected=corrected.isoformat()))
    return plan


def apply_plan(cur, plan):
    if rows := plan["status_updates"]:
        updated = execute_values(
            cur,
            """
            UPDATE customer_status_connection_info ci
            SET system_time_unix = v.corrected
            FROM (VALUES %s) AS v(pull_id, original, corrected)
            WHERE ci.pull_id = v.pull_id AND ci.system_time_unix = v.original
            RETURNING ci.pull_id
            """,
            [(r["pull_id"], r["system_time_unix"], r["corrected"]) for r in rows],
            fetch=True,
        )
        if len(updated) != len(rows):
            raise ValueError("The status update count differs from the plan.")
    if not (rows := plan["log_updates"]):
        return
    cur.execute("""
        CREATE TEMP TABLE timezone_repair_logs ON COMMIT DROP AS
        SELECT *, timestamp_at AS corrected FROM log_entry WITH NO DATA
    """)
    execute_values(
        cur,
        """INSERT INTO timezone_repair_logs
        (timestamp_at, level, message, first_observed_at, corrected) VALUES %s""",
        [
            (
                r["timestamp_at"],
                r["level"],
                r["message"],
                r["first_observed_at"],
                r["corrected"],
            )
            for r in rows
        ],
    )
    cur.execute("""
        DELETE FROM log_entry e USING timezone_repair_logs r
        WHERE (e.timestamp_at, e.level, e.message) = (r.timestamp_at, r.level, r.message)
    """)
    if cur.rowcount != len(rows):
        raise ValueError("The event delete count differs from the plan.")
    cur.execute("""
        INSERT INTO log_entry (timestamp_at, level, message, first_observed_at)
        SELECT corrected, level, message, first_observed_at FROM timezone_repair_logs
    """)
    if cur.rowcount != len(rows):
        raise ValueError("The event insert count differs from the plan.")


def run(path, apply=False):
    expected = json.loads(path.read_text()) if apply else None
    conn = psycopg2.connect(
        "",
        connect_timeout=10,
        options="-c timezone=UTC -c statement_timeout=120000 -c lock_timeout=5000",
    )
    conn.set_session(readonly=not apply, isolation_level="REPEATABLE READ")
    try:
        with conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            if apply:
                cur.execute(
                    "LOCK TABLE customer_status_connection_info, log_entry IN SHARE ROW EXCLUSIVE MODE"
                )
            plan = build_plan(read_snapshot(cur))
            if apply:
                if plan != expected:
                    raise ValueError(
                        "The database or plan differs from the preview. Stop the collector and create a new preview."
                    )
                apply_plan(cur, plan)
            else:
                with path.open("x") as output:
                    json.dump(plan, output, indent=2)
                    output.write("\n")
        print("Committed." if apply else "Read-only preview. No database changes.")
        for key in (
            "status_updates",
            "log_updates",
            "ambiguous_logs",
            "invalid_status_pull_ids",
        ):
            print(f"{key}: {len(plan[key])}")
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--plan",
        type=Path,
        help="Write a new preview file. Use standard PostgreSQL environment variables.",
    )
    mode.add_argument(
        "--apply",
        type=Path,
        help="Apply an unchanged preview in one transaction. Stop the collector first.",
    )
    args = parser.parse_args()
    try:
        run(args.apply or args.plan, apply=args.apply is not None)
    except (ValueError, OSError, psycopg2.Error) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
