from dataclasses import dataclass
from ipaddress import ip_address

import httpx

IP_LOOKUP_URL = "https://api.ipify.org?format=json"


@dataclass(frozen=True)
class OwnIp:
    ip: str


def get_own_ip(url: str = IP_LOOKUP_URL) -> OwnIp:
    data = httpx.get(url, timeout=5).raise_for_status().json()
    return OwnIp(ip=str(ip_address(data["ip"])))
