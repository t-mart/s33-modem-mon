from unittest.mock import MagicMock, Mock

import httpx
import pytest

from s33mon import monitor
from s33mon.config import Config, ModemConfig
from s33mon.ip import OwnIp
from s33mon.models import CustomerStatusLog, LogEntry


@pytest.fixture
def pull(monkeypatch):
    client = MagicMock()
    client.__enter__.return_value = client
    stats = {
        "customer_status_log": CustomerStatusLog(
            entries=[LogEntry(0, 3, "MDD message timeout")]
        )
    }
    client.get_stats.return_value = stats
    monkeypatch.setattr(monitor, "HNAPClient", Mock(return_value=client))
    insert = Mock(return_value=42)
    monkeypatch.setattr(monitor, "insert_pull", insert)
    cfg = Config(modem=ModemConfig(password="test"))
    return client, stats, insert, cfg


@pytest.mark.parametrize(
    "result",
    [
        httpx.ConnectError("unreachable"),
        httpx.ReadTimeout("timeout"),
        httpx.Response(503, request=httpx.Request("GET", "https://example.org")),
        httpx.Response(200, text="invalid json"),
        httpx.Response(200, json={}),
        httpx.Response(200, json=[]),
        httpx.Response(200, json={"ip": None}),
        httpx.Response(200, json={"ip": "invalid address"}),
    ],
)
def test_ip_lookup_failure_preserves_modem_events(pull, monkeypatch, caplog, result):
    if isinstance(result, Exception):
        get = Mock(side_effect=result)
    else:
        result.request = httpx.Request("GET", "https://example.org")
        get = Mock(return_value=result)
    monkeypatch.setattr("httpx.get", get)
    _, stats, insert, cfg = pull
    conn = object()

    assert monitor.do_pull(conn, cfg) == 42

    insert.assert_called_once_with(conn, stats, None)
    assert "Public IP lookup failed" in caplog.text


def test_successful_pull_keeps_public_ip(pull, monkeypatch):
    response = httpx.Response(
        200,
        json={"ip": "203.0.113.7"},
        request=httpx.Request("GET", "https://example.org"),
    )
    monkeypatch.setattr("httpx.get", Mock(return_value=response))
    _, stats, insert, cfg = pull
    conn = object()

    assert monitor.do_pull(conn, cfg) == 42

    insert.assert_called_once_with(conn, stats, OwnIp("203.0.113.7"))


def test_modem_failure_still_fails_the_pull(pull, monkeypatch):
    client, _, insert, cfg = pull
    client.get_stats.side_effect = httpx.ReadTimeout("modem timeout")
    lookup = Mock()
    monkeypatch.setattr(monitor, "get_own_ip", lookup)

    with pytest.raises(httpx.ReadTimeout, match="modem timeout"):
        monitor.do_pull(object(), cfg)

    insert.assert_not_called()
    lookup.assert_not_called()


def test_database_failure_still_fails_the_pull(pull, monkeypatch):
    _, _, insert, cfg = pull
    insert.side_effect = RuntimeError("database unavailable")
    monkeypatch.setattr(monitor, "get_own_ip", Mock(return_value=OwnIp("203.0.113.7")))

    with pytest.raises(RuntimeError, match="database unavailable"):
        monitor.do_pull(object(), cfg)
