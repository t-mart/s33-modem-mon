from dataclasses import dataclass

import httpx


@dataclass
class OwnIp:
    ip: str


def get_own_ip() -> OwnIp:
    data = httpx.get("https://api.ipify.org?format=json").raise_for_status().json()
    return OwnIp(ip=data["ip"])