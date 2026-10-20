"""The modem returns delimited strings for almost everything, so the parsers
are where bugs hide. These fixtures are shaped like real responses."""

from datetime import datetime

import pytest

from s33mon.models import (
    ArrisConfigurationInfo,
    CustomerStatusConnectionInfo,
    CustomerStatusDownstreamChannelInfo,
    CustomerStatusLog,
    CustomerStatusStartupSequence,
    CustomerStatusUpstreamChannelInfo,
    DownstreamChannel,
    LogEntry,
    ModemStats,
    UpstreamChannel,
    get_all_hnap_actions,
    parse_modem_response,
    parse_uptime_seconds,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("00h:52m:22s", 52 * 60 + 22),
        ("6 days 00h:52m:22s", 6 * 86400 + 52 * 60 + 22),
        ("1 days 01h:01m:01s", 86400 + 3661),
        ("00h:00m:00s", 0),
        ("365 days 23h:59m:59s", 365 * 86400 + 86399),
    ],
)
def test_parse_uptime_seconds(raw: str, expected: int) -> None:
    assert parse_uptime_seconds(raw) == expected


def test_connection_info_from_response() -> None:
    info = CustomerStatusConnectionInfo.from_response(
        {
            "CustomerConnSystemUpTime": "6 days 00h:52m:22s",
            "CustomerCurSystemTime": "Wed Feb 18 01:04:01 2026",
            "CustomerConnNetworkAccess": "Allowed",
        }
    )
    assert info.system_uptime_seconds == 6 * 86400 + 52 * 60 + 22
    assert info.network_access == "Allowed"
    # 2026-02-18 01:04:01 at UTC-5.
    assert info.system_time_unix == 1771394641


@pytest.mark.parametrize(
    "wall_time,utc_time",
    [
        ("2026-02-18 01:04:01", "2026-02-18T06:04:01+00:00"),
        ("2026-03-08 01:59:59", "2026-03-08T06:59:59+00:00"),
        ("2026-03-08 02:30:00", "2026-03-08T07:30:00+00:00"),
        ("2026-03-08 03:00:00", "2026-03-08T08:00:00+00:00"),
        ("2026-10-20 16:32:00", "2026-10-20T21:32:00+00:00"),
        ("2026-11-01 01:30:00", "2026-11-01T06:30:00+00:00"),
        ("2026-11-01 02:00:00", "2026-11-01T07:00:00+00:00"),
    ],
)
def test_modem_timestamps_use_fixed_utc_minus_five(wall_time, utc_time):
    wall = datetime.fromisoformat(wall_time)
    expected = int(datetime.fromisoformat(utc_time).timestamp())
    info = CustomerStatusConnectionInfo.from_response(
        {
            "CustomerConnSystemUpTime": "00h:01m:00s",
            "CustomerCurSystemTime": wall.strftime("%a %b %d %H:%M:%S %Y"),
            "CustomerConnNetworkAccess": "Allowed",
        }
    )
    entry = LogEntry.from_string(wall.strftime("0^%H:%M:%S^%d/%m/%Y^6^event"))
    assert info.system_time_unix == expected
    assert entry.timestamp_unix == expected


def test_startup_sequence_strips_frequency_units() -> None:
    seq = CustomerStatusStartupSequence.from_response(
        {
            "CustomerConnDSFreq": "741000000 Hz",
            "CustomerConnDSComment": "Locked",
            "CustomerConnConnectivityStatus": "OK",
            "CustomerConnConnectivityComment": "Operational",
            "CustomerConnBootStatus": "OK",
            "CustomerConnBootComment": "Operational",
            "CustomerConnConfigurationFileStatus": "OK",
            "CustomerConnConfigurationFileComment": "",
            "CustomerConnSecurityStatus": "Enabled",
            "CustomerConnSecurityComment": "BPI+",
        }
    )
    assert seq.downstream_frequency_hertz == 741000000


def test_downstream_channel_from_string() -> None:
    ch = DownstreamChannel.from_string("1^Locked^QAM256^31^741000000^11.0^37.0^0^0")
    assert ch == DownstreamChannel(
        channel_id=31,
        lock_status="Locked",
        modulation="QAM256",
        frequency_hz=741000000,
        power_dbmv=11.0,
        snr_db=37.0,
        corrected_count=0,
        uncorrectable_count=0,
    )


def test_upstream_channel_from_string() -> None:
    ch = UpstreamChannel.from_string("1^Locked^SC-QAM^8^6400000^35600000^44.5")
    assert ch == UpstreamChannel(
        channel_id=8,
        lock_status="Locked",
        channel_type="SC-QAM",
        width_hz=6400000,
        frequency_hz=35600000,
        power_dbmv=44.5,
    )


def test_downstream_channel_info_splits_and_ignores_blanks() -> None:
    info = CustomerStatusDownstreamChannelInfo.from_response(
        {
            "CustomerConnDownstreamChannel": (
                "1^Locked^QAM256^31^741000000^11.0^37.0^0^0|+|"
                "2^Locked^QAM256^32^747000000^10.5^38.0^1^2|+|"
            )
        }
    )
    assert [c.channel_id for c in info.channels] == [31, 32]
    assert info.channels[1].uncorrectable_count == 2


def test_upstream_channel_info_handles_single_channel() -> None:
    info = CustomerStatusUpstreamChannelInfo.from_response(
        {"CustomerConnUpstreamChannel": "1^Locked^SC-QAM^8^6400000^35600000^44.5"}
    )
    assert len(info.channels) == 1
    assert info.channels[0].power_dbmv == 44.5


def test_log_entry_parses_day_first_dates() -> None:
    entry = LogEntry.from_string("0^20:20:27^17/2/2026^6^US profile assignment change.")
    assert entry.level == 6
    assert entry.message == "US profile assignment change."
    # 17 Feb 2026 20:20:27 at UTC-5.
    assert entry.timestamp_unix == 1771377627


def test_log_splits_on_delimiter() -> None:
    log = CustomerStatusLog.from_response(
        {
            "CustomerStatusLogList": (
                "0^20:20:27^17/2/2026^6^first}-{0^20:21:00^17/2/2026^3^second}-{"
            )
        }
    )
    assert [e.message for e in log.entries] == ["first", "second"]
    assert [e.level for e in log.entries] == [6, 3]


def test_configuration_info_coerces_flags() -> None:
    info = ArrisConfigurationInfo.from_response(
        {
            "DownstreamFrequency": "741000000",
            "UpstreamChannelId": "8",
            "DownstreamPlan": "North America",
            "ethSWEthEEE": "0",
            "LedStatus": "1",
        }
    )
    assert info.energy_efficient_ethernet is False
    assert info.led_status is True


def test_get_all_hnap_actions_covers_every_stat() -> None:
    actions = get_all_hnap_actions()
    assert len(actions) == len(ModemStats.__annotations__)
    assert len(set(actions)) == len(actions)
    assert "GetCustomerStatusStartupSequence" in actions


def test_parse_modem_response_skips_absent_actions() -> None:
    stats = parse_modem_response(
        {
            "GetCustomerStatusConnectionInfoResponse": {
                "CustomerConnSystemUpTime": "00h:01m:00s",
                "CustomerCurSystemTime": "Wed Feb 18 01:04:01 2026",
                "CustomerConnNetworkAccess": "Allowed",
            },
            "GetArrisXXXResponse": {"XXX": "whatever"},
        }
    )
    assert set(stats) == {"customer_status_connection_info", "arris_xxx"}
    assert stats["customer_status_connection_info"].system_uptime_seconds == 60
