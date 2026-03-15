import hashlib
import hmac
import json
import time
from dataclasses import dataclass, asdict
from typing import TypedDict, ClassVar, NewType
from datetime import datetime
from zoneinfo import ZoneInfo

import click
import httpx

EASTERN = ZoneInfo("America/New_York")

HNAPAction = NewType("HNAPAction", str)


@dataclass
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
        ds_hz = int(data["CustomerConnDSFreq"].split()[0])
        return cls(
            downstream_frequency_hertz=ds_hz,
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


@dataclass
class CustomerStatusConnectionInfo:
    system_uptime_seconds: int
    system_time_unix: int
    network_access: str

    HNAP_ACTION: ClassVar[HNAPAction] = HNAPAction("GetCustomerStatusConnectionInfo")

    @classmethod
    def from_response(cls, data: dict) -> CustomerStatusConnectionInfo:
        # examples:
        # - "6 days 00h:52m:22s"
        # - "00h:52m:22s"
        seconds = 0

        parts = data["CustomerConnSystemUpTime"].split()
        hms_part = parts[-1]  # always the last part
        h, m, s = [int(x[:-1]) for x in hms_part.split(":")]
        seconds += h * 3600 + m * 60 + s
        if len(parts) > 1:  # has days
            days = int(parts[0])
            seconds += days * 24 * 3600

        # example: "Wed Feb 18 01:04:01 2026"
        system_time_unix = int(
            datetime.strptime(data["CustomerCurSystemTime"], "%a %b %d %H:%M:%S %Y")
            .replace(tzinfo=EASTERN)
            .timestamp()
        )

        return cls(
            system_uptime_seconds=seconds,
            system_time_unix=system_time_unix,
            network_access=data["CustomerConnNetworkAccess"],
        )


@dataclass
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


@dataclass
class CustomerStatusDownstreamChannelInfo:
    channels: list[DownstreamChannel]

    HNAP_ACTION: ClassVar[HNAPAction] = HNAPAction(
        "GetCustomerStatusDownstreamChannelInfo"
    )

    @classmethod
    def from_response(cls, data: dict) -> CustomerStatusDownstreamChannelInfo:
        raw_channels = data["CustomerConnDownstreamChannel"]
        channel_strs = raw_channels.split("|+|")
        channels = [DownstreamChannel.from_string(s) for s in channel_strs if s.strip()]
        return cls(channels=channels)


@dataclass
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


@dataclass
class CustomerStatusUpstreamChannelInfo:
    channels: list[UpstreamChannel]

    HNAP_ACTION: ClassVar[HNAPAction] = HNAPAction(
        "GetCustomerStatusUpstreamChannelInfo"
    )

    @classmethod
    def from_response(cls, data: dict) -> CustomerStatusUpstreamChannelInfo:
        raw_channels = data["CustomerConnUpstreamChannel"]
        channel_strs = raw_channels.split("|+|")
        channels = [UpstreamChannel.from_string(s) for s in channel_strs if s.strip()]
        return cls(channels=channels)


@dataclass
class LogEntry:
    timestamp_unix: int
    level: int
    message: str

    @classmethod
    def from_string(cls, s: str) -> LogEntry:
        # example: 0^20:20:27^17\/2\/2026^6^US profile assignment change. US Chan ID: 8; Previous Profile: 9 13; New Profile: 6 13.;CM-MAC=f8:79:0a:8a:cc:75;CMTS-MAC=00:01:5c:bf:a4:66;CM-QOS=1.1;CM-VER=3.1;
        parts = s.split("^")
        timestamp_str = f"{parts[2]} {parts[1]}"
        timestamp_unix = int(
            datetime.strptime(timestamp_str, "%d/%m/%Y %H:%M:%S")
            .replace(tzinfo=EASTERN)
            .timestamp()
        )
        return cls(
            timestamp_unix=timestamp_unix,
            level=int(parts[3]),
            message=parts[4],
        )


@dataclass
class CustomerStatusLog:
    entries: list[LogEntry]

    HNAP_ACTION: ClassVar[HNAPAction] = HNAPAction("GetCustomerStatusLog")

    @classmethod
    def from_response(cls, data: dict) -> CustomerStatusLog:
        raw_entries = data["CustomerStatusLogList"]
        entry_strs = raw_entries.split("}-{")
        entries = [LogEntry.from_string(s) for s in entry_strs if s.strip()]
        return cls(entries=entries)


@dataclass
class CustomerStatusLogXXX:
    # Probably useless, but let's parse it anyway
    xxx: str

    HNAP_ACTION: ClassVar[HNAPAction] = HNAPAction("GetCustomerStatusLogXXX")

    @classmethod
    def from_response(cls, data: dict) -> CustomerStatusLogXXX:
        return cls(xxx=data["XXX"])


@dataclass
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


@dataclass
class CustomerStatusXXX:
    # Probably useless, but let's parse it anyway
    xxx: str

    HNAP_ACTION: ClassVar[HNAPAction] = HNAPAction("GetCustomerStatusXXX")

    @classmethod
    def from_response(cls, data: dict) -> CustomerStatusXXX:
        return cls(xxx=data["XXX"])


@dataclass
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


@dataclass
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


@dataclass
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


@dataclass
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


@dataclass
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


def hex_hmac_md5(key: str, data: str) -> str:
    """Calculate HMAC-MD5 and return as hex string."""
    return hmac.new(key.encode("utf-8"), data.encode("utf-8"), hashlib.md5).hexdigest()


def change_text(s: str) -> str:
    """Toggle case of all characters in string."""
    return s.swapcase()


@dataclass
class RebootOptions:
    set_eee_enable: bool = False
    led_status: bool = True

    def to_payload(self) -> dict:
        return {
            "SetEEEEnable": "1" if self.set_eee_enable else "0",
            "LED_Status": "1" if self.led_status else "0",
        }


class HNAPClient:
    def __init__(self, host: str, username: str, password: str):
        self.host = host
        self.username = username
        self.password = password
        self.base_url = f"https://{host}"
        self.client = httpx.Client(verify=False, follow_redirects=True)
        self.private_key = None
        self.cookie = None

    def _calculate_hnap_auth(self, action: str) -> tuple[str, int]:
        """Calculate HNAP_AUTH header value."""
        current_time = int(time.time() * 1000) % 2000000000000
        uri = f'"http://purenetworks.com/HNAP1/{action}"'

        # Use PrivateKey if we have it, otherwise use the default for login
        key = self.private_key if self.private_key else "withoutloginkey"

        auth = hex_hmac_md5(key, str(current_time) + uri)
        auth = change_text(auth)

        return auth, current_time

    def _make_request(self, action: str, payload: dict) -> dict:
        """Make an authenticated HNAP request."""
        auth, timestamp = self._calculate_hnap_auth(action)

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "SOAPACTION": f'"http://purenetworks.com/HNAP1/{action}"',
            "HNAP_AUTH": f"{auth} {timestamp}",
        }

        # Add cookies if we have them
        cookies = {}
        if self.cookie:
            cookies["uid"] = self.cookie
        if self.private_key:
            cookies["PrivateKey"] = self.private_key

        response = self.client.post(
            f"{self.base_url}/HNAP1/",
            json=payload,
            headers=headers,
            cookies=cookies,
            timeout=10,
        )
        response.raise_for_status()
        return response.json()

    def login(self):
        """Perform two-stage login process."""
        # Stage 1: Request challenge
        payload = {
            "Login": {
                "Action": "request",
                "Username": self.username,
                "LoginPassword": "",
                "Captcha": "",
                "PrivateLogin": "LoginPassword",
            }
        }

        response = self._make_request("Login", payload)
        login_response = response.get("LoginResponse", {})

        challenge = login_response.get("Challenge")
        self.cookie = login_response.get("Cookie")
        public_key = login_response.get("PublicKey")

        if not all([challenge, self.cookie, public_key]):
            raise ValueError(
                "Missing challenge, cookie, or public key in login response"
            )

        # Calculate PrivateKey
        self.private_key = hex_hmac_md5(public_key + self.password, challenge).upper()

        # Calculate LoginPassword
        login_password = hex_hmac_md5(self.private_key, challenge).upper()

        # Stage 2: Perform actual login
        payload = {
            "Login": {
                "Action": "login",
                "Username": self.username,
                "LoginPassword": login_password,
                "Captcha": "",
                "PrivateLogin": "LoginPassword",
            }
        }

        response = self._make_request("Login", payload)
        login_result = response.get("LoginResponse", {}).get("LoginResult")

        if login_result != "OK":
            raise RuntimeError(
                f'Login failed, result not OK: "{login_result}". Password may be incorrect.'
            )

    def get_stats(self, actions: list[HNAPAction]) -> ModemStats:
        if len(actions) == 0:
            raise ValueError("Must specify at least one action to fetch stats")
        get_multiple_hnap_action = "GetMultipleHNAPs"
        payload = {get_multiple_hnap_action: {action: "" for action in actions}}
        response = self._make_request(get_multiple_hnap_action, payload)
        hnap_responses = response
        if len(actions) > 1:
            hnap_responses = response["GetMultipleHNAPsResponse"]
        return parse_modem_response(hnap_responses)

    def reboot(self, options: RebootOptions | None = None):
        """Reboot the modem."""
        set_arris_configuration_info = "SetArrisConfigurationInfo"
        # POST /HNAP1/ with body {"SetArrisConfigurationInfo":{"Action":"reboot","SetEEEEnable":"0","LED_Status":"1"}}
        payload = {set_arris_configuration_info: {"Action": "reboot"}}
        if options:
            payload[set_arris_configuration_info].update(options.to_payload())

        # empty action, modem doesn't use one for this command
        response = self._make_request(set_arris_configuration_info, payload)
        result = response.get("SetArrisConfigurationInfoResponse", {}).get(
            "SetArrisConfigurationInfoResult"
        )
        if result != "OK":
            raise RuntimeError(f'Reboot failed, result not OK: "{result}"')

    def close(self):
        """Close the HTTP client."""
        self.client.close()


def connection_options(f: click.decorators.FC) -> click.decorators.FC:
    f = click.option(
        "-u",
        "--username",
        default="admin",
        show_default=True,
        envvar="S33_MODEM_USERNAME",
        help="modem username",
    )(f)
    f = click.option(
        "-p",
        "--password",
        required=True,
        envvar="S33_MODEM_PASSWORD",
        help="modem password or login key",
    )(f)
    f = click.option(
        "--host",
        default="192.168.0.1",
        show_default=True,
        envvar="S33_MODEM_HOST",
        help="modem host or IP",
    )(f)
    return f


def _login(username: str, password: str, host: str) -> HNAPClient:
    client = HNAPClient(host, username, password)
    click.echo(
        f"Logging in to s33 modem {username}:{'*' * len(password)} @ {host}...",
        err=True,
        nl=False,
    )
    client.login()
    click.echo("✅", err=True)
    return client


@click.group()
def main() -> None:
    """Arris S33 modem CLI."""


@main.command("stats")
@connection_options
@click.argument("actions", nargs=-1, metavar="ACTION")
def stats_cmd(
    username: str, password: str, host: str, actions: tuple[str, ...]
) -> None:
    """Fetch modem statistics. ACTION may be 'all' or a specific action name (default: all)."""
    all_actions = get_all_hnap_actions()

    selected: list[HNAPAction] = []
    if not actions or any(a.lower() == "all" for a in actions):
        selected = all_actions
    else:
        valid = set(all_actions)
        unknown = [a for a in actions if a not in valid]
        if unknown:
            raise click.UsageError(
                "unknown action(s): "
                + ", ".join(unknown)
                + "\nvalid actions: all, "
                + ", ".join(all_actions)
            )
        selected = [HNAPAction(a) for a in actions]

    client = _login(username, password, host)
    click.echo(
        f"Fetching stats for actions: {', '.join(selected)}...", err=True, nl=False
    )
    result = client.get_stats(selected)
    click.echo("✅", err=True)

    click.echo(json.dumps({k: asdict(v) for k, v in result.items()}, indent=2))  # ty: ignore[invalid-argument-type]
    client.close()


@main.command("reboot")
@connection_options
@click.option(
    "--set-eee-enable/--no-set-eee-enable",
    default=False,
    show_default=True,
    help="enable Energy Efficient Ethernet",
)
@click.option(
    "--led-status/--no-led-status",
    default=True,
    show_default=True,
    help="LED status after reboot",
)
def reboot_cmd(
    username: str, password: str, host: str, set_eee_enable: bool, led_status: bool
) -> None:
    """Reboot the modem."""
    client = _login(username, password, host)
    click.echo("Sending reboot command...", err=True, nl=False)
    client.reboot(RebootOptions(set_eee_enable=set_eee_enable, led_status=led_status))
    click.echo("✅", err=True)
    client.close()


if __name__ == "__main__":
    main()
