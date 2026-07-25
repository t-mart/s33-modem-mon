"""Persistence for modem stats. Requires the `monitor` extra (psycopg2)."""

from s33mon.store.timescale import connect, insert_pull, setup_schema

__all__ = ["connect", "insert_pull", "setup_schema"]
