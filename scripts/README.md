# Timestamp repair

`repair_modem_timezone.py` repairs the inspected January through October 2026 history.
It uses the existing Python environment with the `monitor` extra.
Connection parameters come from standard PostgreSQL environment variables or `.pgpass`.

1. Stop the collector before the updated version starts.
2. Back up the database with `pg_dump`.
3. Run `python scripts/repair_modem_timezone.py --plan .timezone-repair/plan.json`.
4. Review the preview counts and the JSON file.
5. Run `python scripts/repair_modem_timezone.py --apply .timezone-repair/plan.json`.
6. Start the updated collector.

Create `.timezone-repair` before the preview. Git ignores this directory.
Keep the backup and preview file until you verify the repaired data.
The preview file retains original values for each change.

The preview uses a read-only transaction. Apply uses one transaction and locks both affected tables against concurrent writes.
Apply rejects a changed snapshot, a modified plan, or unexpected clock offsets. A repeated apply cannot add another hour.
If the snapshot changes before apply, stop the collector and create a new preview file.
The repair refuses data that already uses the corrected parser.

The repair advances affected modem timestamps by one hour. Collection times and first-observation times remain unchanged.
It uses collection times to resolve status samples within the March transition hour.
It uses embedded login times to resolve login events within that hour.
The other 12 ambiguous events retain their stored timestamps and appear in `ambiguous_logs` for manual review.
Invalid modem dates remain unchanged. The script aborts on event-key conflicts instead of merging records.
