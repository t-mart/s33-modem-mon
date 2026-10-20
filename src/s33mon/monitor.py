"""Poll loop: read stats from the modem on an interval and store them."""

import contextlib
import logging
import time

import httpx

from s33mon.config import Config
from s33mon.hnap import HNAPClient
from s33mon.ip import get_own_ip
from s33mon.models import get_all_hnap_actions
from s33mon.store import connect, insert_pull, setup_schema

logger = logging.getLogger(__name__)

# Cap on how soon we retry after a failed pull, so a broken modem or database
# doesn't turn into a hot loop.
MIN_RETRY_SECONDS = 60


def do_pull(conn, cfg: Config) -> int:
    """Login to the modem, fetch all stats, persist them. Returns the pull id."""
    password = cfg.modem.require_password()
    with HNAPClient(cfg.modem.host, cfg.modem.username, password) as client:
        client.login()
        stats = client.get_stats(get_all_hnap_actions())

    try:
        own_ip = get_own_ip()
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        logger.warning(
            "Public IP lookup failed; store modem data without an IP: %s", exc
        )
        own_ip = None

    return insert_pull(conn, stats, own_ip)


def retry_delay(interval: int, failed: bool) -> int:
    """Sleep until the next pull. Failures back off less, to recover faster."""
    return min(MIN_RETRY_SECONDS, interval) if failed else interval


def run(cfg: Config) -> None:
    """Connect, prepare the schema, then poll forever."""
    logger.info(
        "Connecting to %s@%s:%d/%s ...",
        cfg.db.user,
        cfg.db.host,
        cfg.db.port,
        cfg.db.name,
    )
    conn = connect(cfg.db)
    setup_schema(conn)

    logger.info(
        "Starting poll loop, modem: %s, interval: %ds",
        cfg.modem.host,
        cfg.monitor.interval,
    )
    while True:
        try:
            pull_id = do_pull(conn, cfg)
            logger.info("Pull #%d stored successfully.", pull_id)
            failed = False
        except Exception:
            logger.exception("Error during pull, will retry next interval.")
            failed = True
            with contextlib.suppress(Exception):
                conn.close()
            try:
                conn = connect(cfg.db)
            except Exception:
                logger.exception("Failed to reconnect to database.")

        sleep_time = retry_delay(cfg.monitor.interval, failed)
        logger.info("Sleeping %ds ...", sleep_time)
        time.sleep(sleep_time)
