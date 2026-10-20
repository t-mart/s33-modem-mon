"""Dataclasses for every HNAP action the S33 exposes, plus their parsers.

Each stat class carries the HNAP action that produces it and knows how to build
itself from that action's response payload. ModemStats ties the whole set
together: adding a new action means writing one dataclass and adding one line to
ModemStats, and both the CLI and the database layer pick it up automatically.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import ClassVar, NewType, TypedDict

MODEM_TIMEZONE = timezone(timedelta(hours=-5))

HNAPAction = NewType("HNAPAction", str)


@dataclass(frozen=True)
class CustomerStatusStartupSequence:
    downstream_frequency_hertz: int
    downstream_comment: str
    connectivity_status: str
    connectivity_comment: str
    boot_status: str
    boot_comment: str
    config_file_status: str
    config_file_comment: str
    security_status: str
    security_comment: str

    HNAP_ACTION: ClassVar[HNAPAction] = HNAPAction("GetCustomerStatusStartupSequence")

    @classmethod
    def from_response(cls, data: dict) -> CustomerStatusStartupSequence:
        return cls(
            downstream_frequency_hertz=int(data["CustomerConnDSFreq"].split()[0]),
            downstream_comment=data["CustomerConnDSComment"],
            connectivity_status=data["CustomerConnConnectivityStatus"],
            connectivity_comment=data["CustomerConnConnectivityComment"],
            boot_status=data["CustomerConnBootStatus"],
            boot_comment=data["CustomerConnBootComment"],
            config_file_status=data["CustomerConnConfigurationFileStatus"],
            config_file_comment=data["CustomerConnConfigurationFileComment"],
            security_status=data["CustomerConnSecurityStatus"],
            security_comment=data["CustomerConnSecurityComment"],
        )


def parse_uptime_seconds(s: str) -> int:
    """Parse the modem's uptime format, e.g. "6 days 00h:52m:22s" or "00h:52m:22s"."""
    parts = s.split()
    h, m, sec = (int(x[:-1]) for x in parts[-1].split(":"))
    seconds = h * 3600 + m * 60 + sec
    if len(parts) > 1:
        seconds += int(parts[0]) * 24 * 3600
    return seconds


@dataclass(frozen=True)
class CustomerStatusConnectionInfo:
    system_uptime_seconds: int
    system_time_unix: int
    network_access: str

    HNAP_ACTION: ClassVar[HNAPAction] = HNAPAction("GetCustomerStatusConnectionInfo")

    @classmethod
    def from_response(cls, data: dict) -> CustomerStatusConnectionInfo:
        # example: "Wed Feb 18 01:04:01 2026"
        system_time_unix = int(
            datetime.strptime(data["CustomerCurSystemTime"], "%a %b %d %H:%M:%S %Y")
            .replace(tzinfo=MODEM_TIMEZONE)
            .timestamp()
        )
        return cls(
            system_uptime_seconds=parse_uptime_seconds(
                data["CustomerConnSystemUpTime"]
            ),
            system_time_unix=system_time_unix,
            network_access=data["CustomerConnNetworkAccess"],
        )


@dataclass(frozen=True)
class DownstreamChannel:
    channel_id: int
    lock_status: str
    modulation: str
    frequency_hz: int
    power_dbmv: float
    snr_db: float
    corrected_count: int
    uncorrectable_count: int

    @classmethod
    def from_string(cls, s: str) -> DownstreamChannel:
        parts = s.split("^")
        return cls(
            channel_id=int(parts[3]),
            lock_status=parts[1],
            modulation=parts[2],
            frequency_hz=int(parts[4]),
            power_dbmv=float(parts[5]),
            snr_db=float(parts[6]),
            corrected_count=int(parts[7]),
            uncorrectable_count=int(parts[8]),
        )


@dataclass(frozen=True)
class CustomerStatusDownstreamChannelInfo:
    channels: list[DownstreamChannel]

    HNAP_ACTION: ClassVar[HNAPAction] = HNAPAction(
        "GetCustomerStatusDownstreamChannelInfo"
    )

    @classmethod
    def from_response(cls, data: dict) -> CustomerStatusDownstreamChannelInfo:
        channel_strs = data["CustomerConnDownstreamChannel"].split("|+|")
        return cls(
            channels=[
                DownstreamChannel.from_string(s) for s in channel_strs if s.strip()
            ]
        )


@dataclass(frozen=True)
class UpstreamChannel:
    channel_id: int
    lock_status: str
    channel_type: str
    width_hz: int
    frequency_hz: int
    power_dbmv: float

    @classmethod
    def from_string(cls, s: str) -> UpstreamChannel:
        parts = s.split("^")
        return cls(
            channel_id=int(parts[3]),
            lock_status=parts[1],
            channel_type=parts[2],
            width_hz=int(parts[4]),
            frequency_hz=int(parts[5]),
            power_dbmv=float(parts[6]),
        )


@dataclass(frozen=True)
class CustomerStatusUpstreamChannelInfo:
    channels: list[UpstreamChannel]

    HNAP_ACTION: ClassVar[HNAPAction] = HNAPAction(
        "GetCustomerStatusUpstreamChannelInfo"
    )

    @classmethod
    def from_response(cls, data: dict) -> CustomerStatusUpstreamChannelInfo:
        channel_strs = data["CustomerConnUpstreamChannel"].split("|+|")
        return cls(
            channels=[UpstreamChannel.from_string(s) for s in channel_strs if s.strip()]
        )


@dataclass(frozen=True)
class LogEntry:
    timestamp_unix: int
    level: int
    message: str

    @classmethod
    def from_string(cls, s: str) -> LogEntry:
        # example: 0^20:20:27^17\/2\/2026^6^US profile assignment change. ...
        parts = s.split("^")
        timestamp_unix = int(
            datetime.strptime(f"{parts[2]} {parts[1]}", "%d/%m/%Y %H:%M:%S")
            .replace(tzinfo=MODEM_TIMEZONE)
            .timestamp()
        )
        return cls(
            timestamp_unix=timestamp_unix,
            level=int(parts[3]),
            message=parts[4],
        )


@dataclass(frozen=True)
class CustomerStatusLog:
    entries: list[LogEntry]

    HNAP_ACTION: ClassVar[HNAPAction] = HNAPAction("GetCustomerStatusLog")

    @classmethod
    def from_response(cls, data: dict) -> CustomerStatusLog:
        entry_strs = data["CustomerStatusLogList"].split("}-{")
        return cls(entries=[LogEntry.from_string(s) for s in entry_strs if s.strip()])


@dataclass(frozen=True)
class CustomerStatusLogXXX:
    # Probably useless, but let's parse it anyway
    xxx: str

    HNAP_ACTION: ClassVar[HNAPAction] = HNAPAction("GetCustomerStatusLogXXX")

    @classmethod
    def from_response(cls, data: dict) -> CustomerStatusLogXXX:
        return cls(xxx=data["XXX"])


@dataclass(frozen=True)
class CustomerStatusSoftware:
    spec_version: str
    hard_version: str
    soft_version: str
    mac_address: str
    serial_number: str
    certificate_status: str
    customer_version: str

    HNAP_ACTION: ClassVar[HNAPAction] = HNAPAction("GetCustomerStatusSoftware")

    @classmethod
    def from_response(cls, data: dict) -> CustomerStatusSoftware:
        return cls(
            spec_version=data["StatusSoftwareSpecVer"],
            hard_version=data["StatusSoftwareHdVer"],
            soft_version=data["StatusSoftwareSfVer"],
            mac_address=data["StatusSoftwareMac"],
            serial_number=data["StatusSoftwareSerialNum"],
            certificate_status=data["StatusSoftwareCertificate"],
            customer_version=data["StatusSoftwareCustomerVer"],
        )


@dataclass(frozen=True)
class CustomerStatusXXX:
    # Probably useless, but let's parse it anyway
    xxx: str

    HNAP_ACTION: ClassVar[HNAPAction] = HNAPAction("GetCustomerStatusXXX")

    @classmethod
    def from_response(cls, data: dict) -> CustomerStatusXXX:
        return cls(xxx=data["XXX"])


@dataclass(frozen=True)
class ArrisDeviceStatus:
    firmware_version: str
    internet_connection_status: str
    downstream_frequency_hz: int
    downstream_signal_power_dbmv: float
    downstream_signal_snr_db: float

    HNAP_ACTION: ClassVar[HNAPAction] = HNAPAction("GetArrisDeviceStatus")

    @classmethod
    def from_response(cls, data: dict) -> ArrisDeviceStatus:
        return cls(
            firmware_version=data["FirmwareVersion"],
            internet_connection_status=data["InternetConnection"],
            downstream_frequency_hz=int(data["DownstreamFrequency"].split()[0]),
            downstream_signal_power_dbmv=float(
                data["DownstreamSignalPower"].split()[0]
            ),
            downstream_signal_snr_db=float(data["DownstreamSignalSnr"].split()[0]),
        )


@dataclass(frozen=True)
class ArrisRegisterInfo:
    mac_address: str
    serial_number: str
    model_name: str

    HNAP_ACTION: ClassVar[HNAPAction] = HNAPAction("GetArrisRegisterInfo")

    @classmethod
    def from_response(cls, data: dict) -> ArrisRegisterInfo:
        return cls(
            mac_address=data["MacAddress"],
            serial_number=data["SerialNumber"],
            model_name=data["ModelName"],
        )


@dataclass(frozen=True)
class ArrisRegisterStatus:
    ask_me_later: bool
    never_ask: bool

    HNAP_ACTION: ClassVar[HNAPAction] = HNAPAction("GetArrisRegisterStatus")

    @classmethod
    def from_response(cls, data: dict) -> ArrisRegisterStatus:
        return cls(
            ask_me_later=data["AskMeLater"] == "1",
            never_ask=data["NeverAsk"] == "1",
        )


@dataclass(frozen=True)
class ArrisConfigurationInfo:
    downstream_frequency_hz: int
    upstream_channel_id: int
    downstream_plan: str
    energy_efficient_ethernet: bool
    led_status: bool

    HNAP_ACTION: ClassVar[HNAPAction] = HNAPAction("GetArrisConfigurationInfo")

    @classmethod
    def from_response(cls, data: dict) -> ArrisConfigurationInfo:
        return cls(
            downstream_frequency_hz=int(data["DownstreamFrequency"]),
            upstream_channel_id=int(data["UpstreamChannelId"]),
            downstream_plan=data["DownstreamPlan"],
            energy_efficient_ethernet=data["ethSWEthEEE"] == "1",
            led_status=data["LedStatus"] == "1",
        )


@dataclass(frozen=True)
class ArrisXXX:
    # Probably useless, but let's parse it anyway
    xxx: str

    HNAP_ACTION: ClassVar[HNAPAction] = HNAPAction("GetArrisXXX")

    @classmethod
    def from_response(cls, data: dict) -> ArrisXXX:
        return cls(xxx=data["XXX"])


class ModemStats(TypedDict, total=False):
    customer_status_startup_sequence: CustomerStatusStartupSequence
    customer_status_connection_info: CustomerStatusConnectionInfo
    customer_status_downstream_channel_info: CustomerStatusDownstreamChannelInfo
    customer_status_upstream_channel_info: CustomerStatusUpstreamChannelInfo
    customer_status_log: CustomerStatusLog
    customer_status_log_xxx: CustomerStatusLogXXX
    customer_status_software: CustomerStatusSoftware
    customer_status_xxx: CustomerStatusXXX
    arris_device_status: ArrisDeviceStatus
    arris_register_info: ArrisRegisterInfo
    arris_register_status: ArrisRegisterStatus
    arris_configuration_info: ArrisConfigurationInfo
    arris_xxx: ArrisXXX


def get_all_hnap_actions() -> list[HNAPAction]:
    return [cls.HNAP_ACTION for cls in ModemStats.__annotations__.values()]


def parse_modem_response(data: dict) -> ModemStats:
    stats: ModemStats = {}
    for key, cls in ModemStats.__annotations__.items():
        response_key = cls.HNAP_ACTION + "Response"
        if response_key in data:
            stats[key] = cls.from_response(data[response_key])  # ty: ignore[invalid-key]
    return stats
